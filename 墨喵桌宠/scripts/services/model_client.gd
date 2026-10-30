@tool
class_name ModelClient
extends Node

## 诗韵 v7.9 HTTP 客户端。业务错误也返回 HTTP 200 + {"error": ...}，
## 所以永远先看 body 里的 error，再看 ok。

const BASE_DEFAULT := "http://127.0.0.1:8100"
const TRANSPORT_MOCK := "mock"
const TRANSPORT_HTTP := "http"

const CMD_TIMEOUT_MS := {
	"theme": 55000,
	"couplet": 28000,
	"check": 13000,
	"write": 28000,
	"chat": 55000,
	"memory_get": 5000,
	"memory_reset": 5000,
	"tts": 60000,
	"asr": 30000,
	"asr_start": 5000,
	"asr_stop": 40000,
	"health": 2000,
	"ping": 4000,
}

var transport := TRANSPORT_HTTP
var base_url := BASE_DEFAULT
var poetry_available := false
var chat_available := false
var tts_available := false
var asr_available := false
var available := false

var _next_id := 1
var _inflight := false
var _tts_inflight := false
var _asr_inflight := false
var _asr_recording := false
var _active_http: HTTPRequest = null


func set_transport(t: String) -> void:
	transport = t


func set_base_url(url: String) -> void:
	base_url = url


func set_available(poetry: bool, chat: bool, tts_on: bool, asr_on: bool = false) -> void:
	poetry_available = poetry
	chat_available = chat
	tts_available = tts_on
	asr_available = asr_on
	available = poetry or chat


func is_poetry_available() -> bool:
	return poetry_available or transport == TRANSPORT_MOCK


func is_chat_available() -> bool:
	return chat_available or transport == TRANSPORT_MOCK


func is_asr_available() -> bool:
	return asr_available or transport == TRANSPORT_MOCK


func request(cmd: String, payload: Dictionary, timeout_ms: int = -1) -> Dictionary:
	var id := _next_id
	_next_id += 1
	var started := Time.get_ticks_msec()
	var check := validate_payload(cmd, payload)
	if not check.ok:
		return _error(id, cmd, "invalid_input", check.message, false, started)

	if transport == TRANSPORT_MOCK:
		if get_tree() != null:
			await get_tree().create_timer(0.25).timeout
		return {
			"id": id, "cmd": cmd, "ok": true,
			"data": build_mock_data(cmd, payload),
			"latency_ms": Time.get_ticks_msec() - started,
		}

	if not available:
		return _error(id, cmd, "sidecar_offline", "模型还没醒", true, started)
	if cmd == "chat" and not chat_available:
		return _error(id, cmd, "model_not_ready", "闲聊还在加载", true, started)
	if cmd in ["theme", "couplet", "check", "write"] and not poetry_available:
		return _error(id, cmd, "model_not_ready", "诗模型还在加载", true, started)
	if _inflight:
		return _error(id, cmd, "busy", "还在吟诗，稍等一下喵", true, started)

	var spec := _endpoint_for(cmd, payload)
	if spec.has("error"):
		return _error(id, cmd, "invalid_input", str(spec["error"]), false, started)

	_inflight = true
	var timeout := timeout_ms if timeout_ms > 0 else int(CMD_TIMEOUT_MS.get(cmd, 30000))
	var parsed: Dictionary = await _http_request(
		int(spec.get("method", HTTPClient.METHOD_POST)),
		str(spec.get("path", "")),
		spec.get("body", null),
		timeout
	)
	_inflight = false

	if parsed.has("_transport"):
		return _error(id, cmd, "sidecar_offline", str(parsed["_transport"]), true, started)
	if parsed.has("_http_code"):
		var code := int(parsed["_http_code"])
		var msg := str(parsed.get("_error", ""))
		var err_code := "internal"
		if code == 503:
			err_code = "model_not_ready"
		elif code == 404:
			err_code = "not_implemented"
		return _error(id, cmd, err_code, msg if not msg.is_empty() else "HTTP %d" % code, code >= 500, started)
	if parsed.has("error") and not str(parsed["error"]).is_empty():
		return _error(id, cmd, "business", str(parsed["error"]), false, started, str(parsed.get("type", "")))

	return {
		"id": id, "cmd": cmd, "ok": true,
		"data": parsed,
		"latency_ms": Time.get_ticks_msec() - started,
	}


func health(timeout_ms: int = 2000) -> Dictionary:
	if transport == TRANSPORT_MOCK:
		return {"ok": true, "poetry": true, "chat": true, "tts": true, "mock": true}
	var parsed: Dictionary = await _http_request(
		HTTPClient.METHOD_GET, "/api/health", null, timeout_ms, false)
	if parsed.has("_transport") or parsed.has("_http_code"):
		return {"ok": false, "error": parsed}
	return parsed


func memory_get(timeout_ms: int = -1) -> Dictionary:
	if transport == TRANSPORT_MOCK:
		return {"ok": true, "data": {"pet_name": "墨喵", "user_name": null, "facts": []}}
	var parsed: Dictionary = await _http_request(
		HTTPClient.METHOD_GET, "/api/memory", null,
		timeout_ms if timeout_ms > 0 else int(CMD_TIMEOUT_MS["memory_get"]))
	return _wrap_get(parsed, "memory")


func memory_reset(timeout_ms: int = -1) -> Dictionary:
	if transport == TRANSPORT_MOCK:
		return {"ok": true, "data": {"pet_name": "墨喵", "user_name": null, "facts": []}}
	var parsed: Dictionary = await _http_request(
		HTTPClient.METHOD_POST, "/api/memory", {"reset": true},
		timeout_ms if timeout_ms > 0 else int(CMD_TIMEOUT_MS["memory_reset"]))
	return _wrap_get(parsed, "memory")


func tts(text: String, timeout_ms: int = -1) -> Dictionary:
	var started := Time.get_ticks_msec()
	if text.strip_edges().is_empty():
		return _error(0, "tts", "invalid_input", "没有要朗读的文字", false, started)
	if transport == TRANSPORT_MOCK:
		return _error(0, "tts", "not_implemented", "mock 模式没有语音", false, started)
	if not tts_available:
		return _error(0, "tts", "tts_unavailable", "朗读不可用", true, started)
	if _tts_inflight:
		return _error(0, "tts", "busy", "上一段还没念完", true, started)
	_tts_inflight = true
	var res: Dictionary = await _http_bytes(
		"/api/tts", {"text": text},
		timeout_ms if timeout_ms > 0 else int(CMD_TIMEOUT_MS["tts"]))
	_tts_inflight = false
	return res


func asr(wav: PackedByteArray, timeout_ms: int = -1) -> Dictionary:
	var started := Time.get_ticks_msec()
	if transport == TRANSPORT_MOCK:
		return {"id": 0, "cmd": "asr", "ok": true,
			"data": build_mock_data("asr", {}), "latency_ms": 0}
	if not asr_available:
		return _error(0, "asr", "model_not_ready", "语音识别还没醒", true, started)
	if _asr_inflight:
		return _error(0, "asr", "busy", "上一句还在听", true, started)
	if wav.is_empty():
		return _error(0, "asr", "invalid_input", "没有录到声音", false, started)
	_asr_inflight = true
	var res: Dictionary = await _http_binary(
		"/api/asr", wav, "audio/wav",
		timeout_ms if timeout_ms > 0 else int(CMD_TIMEOUT_MS["asr"]))
	_asr_inflight = false
	return res


## 开始录音（录音在 sidecar 侧用 sounddevice 做，Godot 不碰麦克风）。
func asr_start(timeout_ms: int = -1) -> Dictionary:
	var started := Time.get_ticks_msec()
	if _asr_inflight or _asr_recording:
		return _error(0, "asr_start", "busy", "还在听上一句喵", true, started)
	if transport == TRANSPORT_MOCK:
		_asr_recording = true
		return {"id": 0, "cmd": "asr_start", "ok": true,
			"data": {"ok": true, "samplerate": 48000}, "latency_ms": 0}
	if not asr_available:
		return _error(0, "asr_start", "model_not_ready", "语音识别还没醒", true, started)
	var parsed: Dictionary = await _http_request(
		HTTPClient.METHOD_POST, "/api/asr/start", {},
		timeout_ms if timeout_ms > 0 else int(CMD_TIMEOUT_MS["asr_start"]))
	var err := _wrap_asr_control(parsed, "asr_start", started)
	if not err.is_empty():
		return err
	_asr_recording = true
	return {"id": 0, "cmd": "asr_start", "ok": true,
		"data": parsed, "latency_ms": Time.get_ticks_msec() - started}


## 停止录音并转写；返回 {ok, data:{text, language, duration, ms}}。
func asr_stop(timeout_ms: int = -1) -> Dictionary:
	var started := Time.get_ticks_msec()
	if not _asr_recording:
		return _error(0, "asr_stop", "invalid_input", "没有在录音", false, started)
	_asr_recording = false
	if transport == TRANSPORT_MOCK:
		return {"id": 0, "cmd": "asr_stop", "ok": true,
			"data": build_mock_data("asr", {}), "latency_ms": 0}
	if _asr_inflight:
		return _error(0, "asr_stop", "busy", "正在识别上一句喵", true, started)
	_asr_inflight = true
	var parsed: Dictionary = await _http_request(
		HTTPClient.METHOD_POST, "/api/asr/stop", {},
		timeout_ms if timeout_ms > 0 else int(CMD_TIMEOUT_MS["asr_stop"]))
	_asr_inflight = false
	var err := _wrap_asr_control(parsed, "asr_stop", started)
	if not err.is_empty():
		return err
	return {"id": 0, "cmd": "asr_stop", "ok": true,
		"data": parsed, "latency_ms": Time.get_ticks_msec() - started}


func is_asr_recording() -> bool:
	return _asr_recording


func cancel_current() -> void:
	if _active_http != null and is_instance_valid(_active_http):
		_active_http.cancel_request()
	_active_http = null
	_inflight = false


# ----- HTTP helpers -----

func _http_request(method: int, path: String, body: Variant, timeout_ms: int,
		track_active: bool = true) -> Dictionary:
	if base_url.is_empty():
		return {"_transport": "模型地址为空"}
	var http := HTTPRequest.new()
	http.timeout = maxf(1.0, float(timeout_ms) / 1000.0)
	add_child(http)
	if track_active:
		_active_http = http
	var headers := PackedStringArray(["Content-Type: application/json; charset=utf-8"])
	var data := ""
	if method == HTTPClient.METHOD_POST and body != null:
		data = JSON.stringify(body)
	var err := http.request(base_url + path, headers, method, data)
	if err != OK:
		http.queue_free()
		if _active_http == http:
			_active_http = null
		return {"_transport": "请求失败（%d）" % err}

	var res: Array = await http.request_completed
	if _active_http == http:
		_active_http = null
	var result := int(res[0])
	var code := int(res[1])
	var bytes: PackedByteArray = res[3]
	http.queue_free()

	if result != HTTPRequest.RESULT_SUCCESS:
		return {"_transport": "连接失败（%d）" % result}
	var text := bytes.get_string_from_utf8()
	var parsed: Variant = JSON.parse_string(text) if not text.is_empty() else {}
	if code != 200:
		var msg := ""
		if parsed is Dictionary:
			msg = str(parsed.get("error", ""))
		return {"_http_code": code, "_error": msg}
	if not parsed is Dictionary:
		return {"_http_code": code, "_error": "响应不是 JSON"}
	return parsed


func _http_bytes(path: String, body: Variant, timeout_ms: int) -> Dictionary:
	var started := Time.get_ticks_msec()
	var http := HTTPRequest.new()
	http.timeout = maxf(1.0, float(timeout_ms) / 1000.0)
	add_child(http)
	var headers := PackedStringArray(["Content-Type: application/json; charset=utf-8"])
	var err := http.request(base_url + path, headers, HTTPClient.METHOD_POST, JSON.stringify(body))
	if err != OK:
		http.queue_free()
		return _error(0, "tts", "sidecar_offline", "请求失败（%d）" % err, true, started)
	var res: Array = await http.request_completed
	var result := int(res[0])
	var code := int(res[1])
	var bytes: PackedByteArray = res[3]
	http.queue_free()
	if result != HTTPRequest.RESULT_SUCCESS:
		return _error(0, "tts", "sidecar_offline", "连接失败（%d）" % result, true, started)
	if code != 200:
		var msg := ""
		var parsed: Variant = JSON.parse_string(bytes.get_string_from_utf8())
		if parsed is Dictionary:
			msg = str(parsed.get("error", ""))
		return _error(0, "tts", "tts_unavailable", msg if not msg.is_empty() else "HTTP %d" % code, true, started)
	# 有的错误也会 200 + JSON
	if bytes.size() >= 2 and bytes[0] == 0x7B:  # '{'
		var parsed2: Variant = JSON.parse_string(bytes.get_string_from_utf8())
		if parsed2 is Dictionary and parsed2.has("error"):
			return _error(0, "tts", "tts_unavailable", str(parsed2.get("error", "")), true, started)
	return {
		"id": 0, "cmd": "tts", "ok": true,
		"data": {"bytes": bytes, "mime": "audio/wav"},
		"latency_ms": Time.get_ticks_msec() - started,
	}


func _http_binary(path: String, data: PackedByteArray, mime: String, timeout_ms: int) -> Dictionary:
	var started := Time.get_ticks_msec()
	var http := HTTPRequest.new()
	http.timeout = maxf(1.0, float(timeout_ms) / 1000.0)
	add_child(http)
	var headers := PackedStringArray(["Content-Type: " + mime])
	var err := http.request_raw(base_url + path, headers, HTTPClient.METHOD_POST, data)
	if err != OK:
		http.queue_free()
		return _error(0, "asr", "sidecar_offline", "请求失败（%d）" % err, true, started)
	var res: Array = await http.request_completed
	var result := int(res[0])
	var code := int(res[1])
	var bytes: PackedByteArray = res[3]
	http.queue_free()
	if result != HTTPRequest.RESULT_SUCCESS:
		return _error(0, "asr", "sidecar_offline", "连接失败（%d）" % result, true, started)
	var text := bytes.get_string_from_utf8()
	var parsed: Variant = JSON.parse_string(text) if not text.is_empty() else {}
	if code != 200:
		var msg := str(parsed.get("error", "")) if parsed is Dictionary else ""
		return _error(0, "asr", "model_not_ready", msg if not msg.is_empty() else "HTTP %d" % code, true, started)
	if not parsed is Dictionary:
		return _error(0, "asr", "internal", "识别响应不是 JSON", false, started)
	if parsed.has("error") and not str(parsed["error"]).is_empty():
		return _error(0, "asr", "business", str(parsed["error"]), false, started)
	return {"id": 0, "cmd": "asr", "ok": true, "data": parsed,
		"latency_ms": Time.get_ticks_msec() - started}


## /api/asr/start|stop 的响应包装：和别的接口一样，200 里也可能带 error。
func _wrap_asr_control(parsed: Dictionary, cmd: String, started_ms: int) -> Dictionary:
	if parsed.has("_transport"):
		return _error(0, cmd, "sidecar_offline", str(parsed["_transport"]), true, started_ms)
	if parsed.has("_http_code"):
		var msg := str(parsed.get("_error", ""))
		return _error(0, cmd, "model_not_ready" if int(parsed["_http_code"]) == 503 else "internal",
			msg if not msg.is_empty() else "HTTP %d" % int(parsed["_http_code"]), true, started_ms)
	if parsed.has("error") and not str(parsed["error"]).is_empty():
		return _error(0, cmd, "business", str(parsed["error"]), false, started_ms)
	return {}


func _wrap_get(parsed: Dictionary, cmd: String) -> Dictionary:
	if parsed.has("_transport"):
		return _error(0, cmd, "sidecar_offline", str(parsed["_transport"]), true, 0)
	if parsed.has("_http_code"):
		return _error(0, cmd, "internal", str(parsed.get("_error", "")), true, 0)
	if parsed.has("error") and not str(parsed["error"]).is_empty():
		return _error(0, cmd, "business", str(parsed["error"]), false, 0)
	return {"id": 0, "cmd": cmd, "ok": true, "data": parsed, "latency_ms": 0}


func _endpoint_for(cmd: String, payload: Dictionary) -> Dictionary:
	match cmd:
		"theme":
			return {
				"method": HTTPClient.METHOD_POST,
				"path": "/api/theme",
				"body": {
					"word": str(payload.get("word", payload.get("text", ""))).strip_edges(),
					"form": str(payload.get("form", "auto")),
				},
			}
		"couplet":
			return {
				"method": HTTPClient.METHOD_POST,
				"path": "/api/couplet",
				"body": {"upper": normalize_verse(str(payload.get("upper", ""))), "n": 6},
			}
		"check":
			return {
				"method": HTTPClient.METHOD_POST,
				"path": "/api/check",
				"body": {"text": str(payload.get("text", payload.get("poem", "")))},
			}
		"write":
			return {
				"method": HTTPClient.METHOD_POST,
				"path": "/api/write",
				"body": {
					"prompt": str(payload.get("prompt", payload.get("opening", ""))),
					"lines": int(payload.get("lines", 2)),
				},
			}
		"chat":
			return {
				"method": HTTPClient.METHOD_POST,
				"path": "/api/chat",
				"body": {"text": str(payload.get("text", "")).strip_edges()},
			}
	return {"error": "未知命令：%s" % cmd}


# ----- 纯函数：便于单测 -----

static func validate_payload(cmd: String, payload: Dictionary) -> Dictionary:
	match cmd:
		"theme":
			var word := str(payload.get("word", payload.get("text", ""))).strip_edges()
			if word.is_empty():
				return _invalid("请先输入一个词")
			if word.length() > 4:
				return _invalid("关键词最多 4 个字")
		"couplet":
			var upper := normalize_verse(str(payload.get("upper", "")))
			if upper.is_empty():
				return _invalid("请先输入上句")
			if upper.length() != 5 and upper.length() != 7:
				return _invalid("上句需要是五言或七言")
		"check":
			var poem := str(payload.get("text", payload.get("poem", ""))).strip_edges()
			if poem.is_empty():
				return _invalid("请先粘贴诗句")
		"write":
			var prompt := str(payload.get("prompt", payload.get("opening", ""))).strip_edges()
			if prompt.is_empty():
				return _invalid("请先输入开头")
		"chat":
			var text := str(payload.get("text", "")).strip_edges()
			if text.is_empty():
				return _invalid("说点什么喵")
		"ping":
			pass
		_:
			return _invalid("未知命令：%s" % cmd)
	return {"ok": true, "code": "", "message": ""}


static func normalize_verse(text: String) -> String:
	var out := ""
	for c in text:
		if c in "，。！？；：、,.!?;: \t\n\r「」『』“”\"'（）()":
			continue
		out += c
	return out


static func build_mock_data(cmd: String, payload: Dictionary) -> Dictionary:
	match cmd:
		"theme":
			var word := str(payload.get("word", payload.get("text", "孤舟")))
			return {
				"poem": "我今南北游，孤舟望长安。\n君今东上去，遗我古衣冠。",
				"form": "五言",
				"keyword_at": "第2句第1字",
				"note": "题眼：%s" % word,
			}
		"couplet":
			return {
				"items": [
					{"text": "高树晚云生", "nll": 1.87, "score": 0.96,
					 "义类": 1.0, "虚实": 1.0, "字级": 0.8},
					{"text": "微径夕阳时", "nll": 1.95, "score": 0.88,
					 "义类": 1.0, "虚实": 1.0, "字级": 0.4},
				],
				"note": null,
			}
		"check":
			return {
				"form": "五言",
				"couplets": 1,
				"jushi_ok": true,
				"jushi_bad": [],
				"pingze_ok": false,
				"pingze_bad": ["第2句第4字应平实仄（模板ZZZPP）"],
				"yayun_ok": true,
				"rhymes": ["霜"],
				"yayun_bad": [],
			}
		"write":
			return {
				"poem": "小院灯初上，虚堂漏未残。\n忽惊衣带重，方觉漏声寒。",
				"note": null,
			}
		"chat":
			return {
				"reply": "喵，吾在砚台边。",
				"tool": null,
				"poem": null,
				"note": null,
				"ms": 300,
				"pet_name": "墨喵",
			}
		"asr":
			return {"text": "你好呀", "language": "zh", "duration": 1.0, "ms": 100}
		"ping":
			return {"ok": true, "mock": true}
	return {}


static func _invalid(message: String) -> Dictionary:
	return {"ok": false, "code": "invalid_input", "message": message}


static func _error(id: int, cmd: String, code: String, message: String,
		retryable: bool, started_ms: int, type_hint: String = "") -> Dictionary:
	return {
		"id": id,
		"cmd": cmd,
		"ok": false,
		"error": {
			"code": code,
			"message": message,
			"type": type_hint,
			"retryable": retryable,
		},
		"latency_ms": Time.get_ticks_msec() - started_ms,
	}
