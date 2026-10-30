class_name VoicePlayer
extends Node

## 朗读：优先走 `/api/tts/stream`（服务端边合成边推 PCM，这边边收边播，
## 起播约 1.2 秒）；流式不可用时退回整段 wav（/api/tts）。
## 失败静默降级；连续失败两次本次运行自动关闭。
##
## 注意：这里用 StreamPeerTCP 自己发一个最小 HTTP 请求，而不是 HTTPClient——
## 实测本机 Godot 4.7 的 HTTPClient.connect_to_host 一直返回 DISCONNECTED
## （HTTPRequest 却正常），而 StreamPeerTCP 连接正常，所以流式读自己来。

signal unavailable(message: String)

const SAMPLE_RATE := 48000
const BUFFER_SEC := 0.6
const STALL_TIMEOUT_MS := 20000
const HEADER_LIMIT := 16384
const CRLF := "\r\n\r\n"
# 播放没跟上时最多先囤 2 秒 PCM；再多就先不读 socket（TCP 自己会限速）
const MAX_PENDING_BYTES := SAMPLE_RATE * 2 * 2

var enabled := false
var model: ModelClient

var _player: AudioStreamPlayer
var _busy := false
var _fail_count := 0

# ----- 流式状态 -----
var _tcp: StreamPeerTCP
var _playback: AudioStreamGeneratorPlayback
var _buffer_frames := 0
var _host := ""
var _port := 0
var _request_sent := false
var _headers_done := false
var _http_code := 0
var _header_buf := PackedByteArray()
var _eof := false
var _pending := PackedByteArray()      # 还没塞进播放缓冲的 PCM（不能丢！）
var _text := ""
var _stream_bytes := 0                 # 已推入播放缓冲
var _recv_bytes := 0                   # 已从网络收到（用来核对有没有丢）
var _last_data_ms := 0
var _stream_started_ms := 0


func setup(model_ref: ModelClient) -> void:
	model = model_ref
	_player = AudioStreamPlayer.new()
	_player.volume_db = -6.0
	add_child(_player)
	set_process(false)


func set_enabled(on: bool) -> void:
	enabled = on
	if not on:
		stop()


## force=true 绕过总开关（番茄钟播报等主动交互）；TTS 不可用时静默降级。
func speak(text: String, force: bool = false) -> void:
	if (not enabled and not force) or text.strip_edges().is_empty() or _busy:
		return
	if model == null or _player == null:
		return
	stop()
	_busy = true
	_text = text
	if _start_stream():
		return
	await _speak_buffered(text)


func stop() -> void:
	_close_stream()
	if _player != null:
		_player.stop()
	_busy = false


func is_playing() -> bool:
	return _player != null and _player.playing


# ----- 流式播放 -----

func _start_stream() -> bool:
	if not _parse_url():
		return false
	var gen := AudioStreamGenerator.new()
	gen.mix_rate = SAMPLE_RATE
	gen.buffer_length = BUFFER_SEC
	_player.stream = gen
	_player.play()
	_playback = _player.get_stream_playback() as AudioStreamGeneratorPlayback
	if _playback == null:
		return false
	_buffer_frames = int(SAMPLE_RATE * BUFFER_SEC)

	_tcp = StreamPeerTCP.new()
	if _tcp.connect_to_host(_host, _port) != OK:
		_close_stream()
		return false
	_request_sent = false
	_headers_done = false
	_http_code = 0
	_header_buf = PackedByteArray()
	_eof = false
	_pending = PackedByteArray()
	_stream_bytes = 0
	_recv_bytes = 0
	_last_data_ms = Time.get_ticks_msec()
	_stream_started_ms = Time.get_ticks_msec()
	if OS.is_debug_build():
		print("[voice] 流式朗读开始 → %s:%d" % [_host, _port])
	set_process(true)
	return true


func _process(_delta: float) -> void:
	_flush_pending()                     # 每帧都往播放缓冲里补，别等数据一坨一坨来
	if _tcp == null:
		# 已经收完数据：等播放缓冲放干净再收工（不能直接关掉 _process）
		if _eof:
			_maybe_finish()
		else:
			set_process(false)
		return
	if not _eof and Time.get_ticks_msec() - _last_data_ms > STALL_TIMEOUT_MS:
		_stream_failed()
		return

	_tcp.poll()
	var status := _tcp.get_status()
	match status:
		StreamPeerTCP.STATUS_CONNECTED:
			if not _request_sent:
				_send_request()
			if _tcp != null:
				_read_available()
		StreamPeerTCP.STATUS_ERROR:
			_stream_failed()
		StreamPeerTCP.STATUS_NONE:
			# 服务端写完就 close（Connection: close）；socket 已关就别再读了
			if _request_sent:
				_eof = true
				_close_tcp()
				_maybe_finish()
	if _eof:
		_maybe_finish()


func _send_request() -> void:
	_request_sent = true
	var body := JSON.stringify({"text": _text})
	var body_bytes := body.to_utf8_buffer()
	var head := ("POST /api/tts/stream HTTP/1.1\r\n"
		+ "Host: %s:%d\r\n" % [_host, _port]
		+ "Content-Type: application/json\r\n"
		+ "Content-Length: %d\r\n" % body_bytes.size()
		+ "Connection: close\r\n\r\n")
	var err := _tcp.put_data(head.to_utf8_buffer() + body_bytes)
	if err != OK:
		_stream_failed()


func _read_available() -> void:
	if _tcp == null:
		return
	if _pending.size() >= MAX_PENDING_BYTES:
		return                           # 囤够了，先让播放消化，socket 里剩着不会丢
	var n := _tcp.get_available_bytes()
	while n > 0:
		var got: Array = _tcp.get_data(n)
		if int(got[0]) != OK:
			return
		var data: PackedByteArray = got[1]
		if data.is_empty():
			return
		_last_data_ms = Time.get_ticks_msec()
		if _headers_done:
			_on_pcm(data)
		else:
			_header_buf += data
			var sep := _find_seq(_header_buf, CRLF.to_utf8_buffer())
			if sep < 0:
				if _header_buf.size() > HEADER_LIMIT:
					_stream_failed()
				return
			var head := _header_buf.slice(0, sep).get_string_from_utf8()
			var rest := _header_buf.slice(sep + 4)
			_header_buf = PackedByteArray()
			_headers_done = true
			var first_line := ""
			if not head.is_empty():
				first_line = head.split("\r\n")[0]
			_http_code = 200 if first_line.contains(" 200 ") else 500
			var is_pcm := head.to_lower().contains("content-type: application/octet-stream")
			if _http_code != 200 or not is_pcm:
				_stream_failed()
				return
			if not rest.is_empty():
				_on_pcm(rest)
		n = _tcp.get_available_bytes()


## PackedByteArray.find() 只接受单字节，这里自己找子序列（只在头部小缓冲上用）
static func _find_seq(buf: PackedByteArray, seq: PackedByteArray) -> int:
	var n := seq.size()
	if n == 0 or buf.size() < n:
		return -1
	for i in range(buf.size() - n + 1):
		if buf[i] != seq[0]:
			continue
		var hit := true
		for j in n:
			if buf[i + j] != seq[j]:
				hit = false
				break
		if hit:
			return i
	return -1


## 新到的 PCM 先囤起来，再尽量塞进播放缓冲；塞不下的留到下一帧，绝不丢。
func _on_pcm(bytes: PackedByteArray) -> void:
	_recv_bytes += bytes.size()
	_pending += bytes
	_flush_pending()


func _flush_pending() -> void:
	if _playback == null or _pending.size() < 2:
		return
	if _stream_bytes == 0 and OS.is_debug_build():
		print("[voice] 首包 %dms" % (Time.get_ticks_msec() - _stream_started_ms))
	while _pending.size() >= 2:
		var avail := _playback.get_frames_available()
		if avail <= 0:
			return
		var total := _pending.size() / 2
		var take := mini(avail, total)
		var frames := PackedVector2Array()
		frames.resize(take)
		for i in take:
			var v := float(_pending.decode_s16(i * 2)) / 32768.0
			frames[i] = Vector2(v, v)
		_playback.push_buffer(frames)
		_stream_bytes += take * 2
		_pending = _pending.slice(take * 2)


func _maybe_finish() -> void:
	if not _eof or _playback == null:
		return
	if _pending.size() < 2 and _playback.get_frames_available() >= _buffer_frames:   # 全放完了
		var got_audio := _stream_bytes > 0
		if OS.is_debug_build() and got_audio:
			print("[voice] 流式朗读结束：收到 %d / 推入 %d 字节（%.2fs）用时 %dms" % [
				_recv_bytes, _stream_bytes, _stream_bytes / 2.0 / SAMPLE_RATE,
				Time.get_ticks_msec() - _stream_started_ms])
		_close_stream()
		_player.stop()
		_busy = false
		if got_audio:
			_fail_count = 0


func _stream_failed() -> void:
	var text := _text
	var had_audio := _stream_bytes > 0
	if OS.is_debug_build():
		print("[voice] 流式朗读失败（已收到 %d 字节）" % _stream_bytes)
	_close_stream()
	_player.stop()
	_busy = false
	if had_audio:
		return                           # 已经播过一段，就当念完了
	_fail_count += 1
	if _fail_count >= 2:
		enabled = false
		unavailable.emit("朗读不可用（需要联网 + ffmpeg）")
		return
	await _speak_buffered(text)


func _close_tcp() -> void:
	if _tcp != null:
		_tcp.disconnect_from_host()
		_tcp = null


func _close_stream() -> void:
	_close_tcp()
	_playback = null
	_request_sent = false
	_headers_done = false
	_header_buf = PackedByteArray()
	_eof = false
	_pending = PackedByteArray()
	set_process(false)


func _parse_url() -> bool:
	var url := model.base_url if model != null else ""
	url = url.replace("http://", "").replace("https://", "")
	var slash := url.find("/")
	if slash >= 0:
		url = url.substr(0, slash)
	var parts := url.split(":", true, 1)
	if parts.size() < 2:
		return false
	_host = parts[0]
	_port = int(parts[1])
	return _port > 0


# ----- 整段兜底 -----

func _speak_buffered(text: String) -> void:
	_busy = true
	var res: Dictionary = await model.tts(text)
	if not res.get("ok", false):
		_busy = false
		_fail_count += 1
		if _fail_count >= 2:
			enabled = false
			unavailable.emit("朗读不可用（需要联网 + ffmpeg）")
		return
	_fail_count = 0
	var data: Dictionary = res.get("data", {})
	var bytes: PackedByteArray = data.get("bytes", PackedByteArray())
	var stream: AudioStreamWAV = WavDecoder.parse(bytes)
	if stream == null:
		_busy = false
		_fail_count += 1
		if _fail_count >= 2:
			enabled = false
			unavailable.emit("朗读音频解码失败")
		return
	_player.stop()
	_player.stream = stream
	_player.play()
	_busy = false
