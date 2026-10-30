extends Node
## 诗韵 · Godot 接入示例（Godot 4.x）
##
## 用法：
##   1. 先在本机启动服务： python serve.py
##   2. 把这个脚本挂到任意 Node 上（桌宠根节点即可）
##   3. 调 chat("你好呀") / theme("孤舟") / speak("...")
##
## 设计要点：
## · 每次请求新建 HTTPRequest（次数很少，比复用队列省心）
## · 服务端业务异常也返回 HTTP 200 + error 字段，所以**先看 error，再看字段**
## · TTS 返回 wav 二进制，这里手工解 44 字节头 → AudioStreamWAV（各版本通用）

const BASE := "http://127.0.0.1:8100"

signal replied(text: String, tool: String)
signal spoke()


# ---------- 基础请求 ----------
func _post(path: String, body: Dictionary) -> Dictionary:
	var http := HTTPRequest.new()
	add_child(http)
	var err := http.request(BASE + path,
			["Content-Type: application/json"],
			HTTPClient.METHOD_POST, JSON.stringify(body))
	if err != OK:
		http.queue_free()
		return {"error": "无法连接服务（err=%d），先运行 python serve.py" % err}
	var res: Array = await http.request_completed
	http.queue_free()
	if res[1] != 200:
		return {"error": "HTTP %d" % res[1]}
	var txt := (res[3] as PackedByteArray).get_string_from_utf8()
	var data = JSON.parse_string(txt)
	return data if data is Dictionary else {"error": "响应不是 JSON"}


func _get(path: String) -> Dictionary:
	var http := HTTPRequest.new()
	add_child(http)
	var err := http.request(BASE + path)
	if err != OK:
		http.queue_free()
		return {"error": "无法连接服务（err=%d）" % err}
	var res: Array = await http.request_completed
	http.queue_free()
	if res[1] != 200:
		return {"error": "HTTP %d" % res[1]}
	var data = JSON.parse_string((res[3] as PackedByteArray).get_string_from_utf8())
	return data if data is Dictionary else {"error": "响应不是 JSON"}


# ---------- 对外接口 ----------
## 统一入口：闲聊 / 作诗 / 对句 / 体检 / 续写 / 看屏幕
func chat(text: String) -> Dictionary:
	var r: Dictionary = await _post("/api/chat", {"text": text})
	if r.has("error"):
		replied.emit("喵……" + str(r["error"]), "")
		return r
	replied.emit(str(r.get("reply", "")), str(r.get("tool", "")))
	return r


## 点题成诗
func theme(word: String, form := "auto") -> Dictionary:
	return await _post("/api/theme", {"word": word, "form": form})


## 对下句（返回 items 数组，按 NLL + 对仗排序）
func couplet(upper: String, n := 6) -> Dictionary:
	return await _post("/api/couplet", {"upper": upper, "n": n})


## 格律体检
func check(text: String) -> Dictionary:
	return await _post("/api/check", {"text": text})


## 续写（lines: 2=绝句 / 4=律诗）
func write(prompt: String, lines := 2) -> Dictionary:
	return await _post("/api/write", {"prompt": prompt, "lines": lines})


# ---------- 语音 ----------
## 说一句话（自动播放）。念诗会自动逐联合成、带停顿。
func speak(text: String) -> void:
	var http := HTTPRequest.new()
	add_child(http)
	http.request(BASE + "/api/tts",
			["Content-Type: application/json"],
			HTTPClient.METHOD_POST, JSON.stringify({"text": text}))
	var res: Array = await http.request_completed
	http.queue_free()
	if res[1] != 200:
		push_warning("TTS 不可用（需联网 + ffmpeg）")
		return
	var bytes: PackedByteArray = res[3]
	var stream := wav_from_bytes(bytes)
	if stream == null:
		return
	var player := AudioStreamPlayer.new()
	player.stream = stream
	add_child(player)
	player.finished.connect(player.queue_free)
	player.play()
	spoke.emit()


## 手工解 wav（48kHz/单声道/16bit）。Godot 4.4+ 也可用
## AudioStreamWAV.load_from_buffer(bytes) 一行搞定。
func wav_from_bytes(bytes: PackedByteArray) -> AudioStreamWAV:
	if bytes.size() <= 44:
		return null
	# 跳过 RIFF/WAVE/fmt/data 头（本服务固定为 44 字节头）
	var pcm := bytes.slice(44)
	var s := AudioStreamWAV.new()
	s.format = AudioStreamWAV.FORMAT_16_BITS
	s.mix_rate = 48000
	s.stereo = false
	s.data = pcm
	return s


# ---------- 桌宠行为示例 ----------
## 空闲时每 N 分钟看一次屏幕，画面变了就说一句或吟一首
func start_idle_watch(interval_sec := 180.0) -> void:
	var last_hash := 0
	while true:
		await get_tree().create_timer(interval_sec).timeout
		var sc: Dictionary = await look()
		if sc.has("error"):
			continue
		# 用亮度+色相做粗粒度"变化检测"，避免每次都打扰
		var h := int(float(sc.get("luma", 0)) * 100) * 10 + str(sc.get("hue", "")).hash() % 10
		if h == last_hash:
			continue
		last_hash = h
		if randf() < 0.45:
			var line := "唔，%s。" % sc.get("desc", "")
			replied.emit(line, "")
			speak(line)
		else:
			var r: Dictionary = await theme(str(sc.get("word", "夜")))
			if not r.has("error"):
				var poem := str(r.get("poem", ""))
				replied.emit(poem, "theme")
				speak(poem)


## 启动时检查服务是否就绪
func wait_ready(timeout_sec := 60.0) -> bool:
	var t := 0.0
	while t < timeout_sec:
		var h: Dictionary = await _get("/api/health")
		if h.get("ok", false):
			print("[诗韵] 服务就绪：", h)
			return true
		await get_tree().create_timer(1.0).timeout
		t += 1.0
	push_error("[诗韵] 服务未启动，请先运行 python serve.py")
	return false
