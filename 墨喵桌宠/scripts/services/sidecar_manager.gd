class_name SidecarManager
extends Node

## 管理诗韵 v7.9 sidecar：探测/拉起/等待 poetry & chat 就绪/退出清理。
## 只 kill 自己 spawn 的进程；手动启动的服务直接复用。

signal status_changed(state: String, detail: String)
signal ready_changed(poetry: bool, chat: bool, tts: bool, asr: bool)

const STATUS_PROBING := "probing"
const STATUS_SPAWNING := "spawning"
const STATUS_LOADING_POETRY := "loading_poetry"
const STATUS_POETRY_READY := "poetry_ready"
const STATUS_CHAT_LOADING := "chat_loading"
const STATUS_CHAT_READY := "chat_ready"
const STATUS_FAILED := "failed"

const PORT_START := 8100
const PORT_END := 8104
const POETRY_TIMEOUT_MS := 90000
const CHAT_TIMEOUT_MS := 180000

var model: ModelClient
var base_url := ""
var port := PORT_START
var status := STATUS_PROBING
var detail := ""

var _spawned := false
var _pid := 0
var _busy := false
var _stopping := false

var _python := "python"
var _launcher := ""
var _package := ""
var _log_path := ""
var _memory_path := ""
var _tts_cache := ""


func setup(model_ref: ModelClient) -> void:
	model = model_ref
	model.set_transport(ModelClient.TRANSPORT_HTTP)
	_resolve_paths()


func ensure_ready() -> Dictionary:
	if _busy:
		return {"ok": false, "error": "正在启动中"}
	_busy = true
	_stopping = false
	_set_status(STATUS_PROBING, "")

	var pick := await _pick_port()
	if not pick.ok:
		_busy = false
		_set_status(STATUS_FAILED, str(pick.error))
		return {"ok": false, "error": pick.error}

	port = int(pick.port)
	base_url = "http://127.0.0.1:%d" % port
	model.set_base_url(base_url)

	if not pick.get("running", false):
		var spawn := _spawn()
		if not spawn.ok:
			_busy = false
			_set_status(STATUS_FAILED, str(spawn.error))
			return {"ok": false, "error": spawn.error}
		_set_status(STATUS_LOADING_POETRY, "首次约 30–90 秒")

	var h := await _wait_poetry()
	if not h.get("ok", false):
		_busy = false
		_set_status(STATUS_FAILED, str(h.get("error", "诗模型未就绪")))
		return h

	_apply_health(h)
	_set_status(STATUS_POETRY_READY, "可以作诗了")
	_busy = false
	# chat / asr 继续在后台加载，不阻塞四功能
	_poll_chat()
	return {"ok": true, "base_url": base_url, "poetry": true,
		"chat": bool(h.get("chat", false)), "tts": bool(h.get("tts", false)),
		"asr": bool(h.get("asr", false)), "spawned": _spawned}


func retry() -> Dictionary:
	if _spawned:
		cleanup()
	return await ensure_ready()


func cleanup() -> void:
	_stopping = true
	if _spawned and _pid > 0:
		if OS.is_process_running(_pid):
			var out := []
			OS.execute("taskkill", ["/PID", str(_pid), "/T", "/F"], out, false, false)
			await get_tree().create_timer(0.2).timeout
	_spawned = false
	_pid = 0


func is_spawned() -> bool:
	return _spawned


func get_status_text() -> String:
	match status:
		STATUS_PROBING: return "探测中"
		STATUS_SPAWNING: return "启动中"
		STATUS_LOADING_POETRY: return "诗模型加载中"
		STATUS_POETRY_READY: return "诗模型就绪"
		STATUS_CHAT_LOADING: return "闲聊加载中"
		STATUS_CHAT_READY: return "全部就绪"
		STATUS_FAILED: return "失败"
	return status


# ----- 路径/启动 -----

func _resolve_paths() -> void:
	_python = OS.get_environment("MOMIAO_PYTHON")
	if _python.is_empty():
		_python = "python"

	var project_dir := ProjectSettings.globalize_path("res://").trim_suffix("/")
	var sidecar_dir := project_dir.get_base_dir().path_join("sidecar")
	_launcher = sidecar_dir.path_join("launcher").path_join("start_server.py")
	_package = sidecar_dir.path_join("shiyun-v7.9").path_join("shiyun-godot-v7.9")

	var user_dir := ProjectSettings.globalize_path("user://").trim_suffix("/")
	var log_dir := user_dir.path_join("logs")
	DirAccess.make_dir_recursive_absolute(log_dir)
	DirAccess.make_dir_recursive_absolute(user_dir.path_join("memory"))
	DirAccess.make_dir_recursive_absolute(user_dir.path_join("tts_cache"))
	_log_path = log_dir.path_join("sidecar-%s.log" % Time.get_date_string_from_system())
	_memory_path = user_dir.path_join("memory").path_join("chat_memory.json")
	_tts_cache = user_dir.path_join("tts_cache")


func _spawn() -> Dictionary:
	if not FileAccess.file_exists(_launcher):
		return {"ok": false, "error": "找不到启器：%s" % _launcher}
	if not DirAccess.dir_exists_absolute(_package):
		return {"ok": false, "error": "找不到模型包：%s" % _package}
	_set_status(STATUS_SPAWNING, "")
	var args := [
		_launcher,
		"--port", str(port),
		"--log", _log_path,
		"--memory", _memory_path,
		"--tts-cache", _tts_cache,
	]
	var pid := OS.create_process(_python, args)
	if pid <= 0:
		return {"ok": false, "error": "无法启动 Python（检查 MOMIAO_PYTHON / PATH）"}
	_spawned = true
	_pid = pid
	return {"ok": true}


# ----- 探测/等待 -----

func _pick_port() -> Dictionary:
	for p in range(PORT_START, PORT_END + 1):
		var probe := await _probe(p)
		if probe.state == "ours":
			port = p
			base_url = "http://127.0.0.1:%d" % p
			model.set_base_url(base_url)
			var h: Dictionary = probe.health
			_apply_health(h)
			# 已有服务：如果 chat 还没好，后台继续等
			if h.get("chat", false):
				_set_status(STATUS_CHAT_READY, "复用已运行的服务")
			else:
				_set_status(STATUS_CHAT_LOADING, "复用已运行的服务")
			return {"ok": true, "port": p, "running": true, "health": h}
		if probe.state == "free":
			return {"ok": true, "port": p, "running": false}
	# 全部被占且不是本服务
	return {"ok": false, "error": "8100–8104 都被占用，无法启动模型服务"}


func _probe(p: int) -> Dictionary:
	var parsed := await _http_get("http://127.0.0.1:%d/api/health" % p, 1.5)
	if parsed.is_empty():
		return {"state": "free"}
	if parsed.has("poetry"):
		return {"state": "ours", "health": parsed}
	return {"state": "foreign"}


func _wait_poetry() -> Dictionary:
	var deadline := Time.get_ticks_msec() + POETRY_TIMEOUT_MS
	while Time.get_ticks_msec() < deadline and not _stopping:
		var h := await _http_get(base_url + "/api/health", 2.0)
		if h.get("poetry", false):
			return h
		if _spawned and _pid > 0 and not OS.is_process_running(_pid):
			return {"ok": false, "error": "服务进程已退出：\n" + _read_log_tail(20)}
		if not _spawned and h.is_empty():
			return {"ok": false, "error": "已有服务无响应"}
		await get_tree().create_timer(1.0).timeout
	return {"ok": false, "error": "诗模型 90 秒内未就绪\n" + _read_log_tail(20)}


func _poll_chat() -> void:
	var deadline := Time.get_ticks_msec() + CHAT_TIMEOUT_MS
	var chat_ready := false
	while Time.get_ticks_msec() < deadline and not _stopping:
		if not chat_ready:
			_set_status(STATUS_CHAT_LOADING, "闲聊模型还在加载")
		var h := await _http_get(base_url + "/api/health", 2.0)
		if not h.is_empty():
			_apply_health(h)
			if h.get("chat", false):
				chat_ready = true
				_set_status(STATUS_CHAT_READY, "闲聊就绪")
				# ASR 可能稍后才就绪；就绪或报错后就不再轮询
				if h.get("asr", false) or not str(h.get("asr_error", "")).is_empty():
					return
		if _spawned and _pid > 0 and not OS.is_process_running(_pid):
			_set_status(STATUS_FAILED, "服务进程已退出")
			return
		if chat_ready and str(h.get("asr_error", "")).is_empty() and not h.get("asr", false) \
				and Time.get_ticks_msec() >= deadline:
			return
		await get_tree().create_timer(2.0).timeout
	if not _stopping and not chat_ready:
		_set_status(STATUS_FAILED, "闲聊模型加载超时")


func _apply_health(h: Dictionary) -> void:
	var poetry := bool(h.get("poetry", false))
	var chat := bool(h.get("chat", false))
	var tts := bool(h.get("tts", false))
	var asr := bool(h.get("asr", false))
	if model != null:
		model.set_base_url(base_url)
		model.set_available(poetry, chat, tts, asr)
	ready_changed.emit(poetry, chat, tts, asr)


func _http_get(url: String, timeout_sec: float) -> Dictionary:
	var http := HTTPRequest.new()
	http.timeout = timeout_sec
	add_child(http)
	var err := http.request(url)
	if err != OK:
		http.queue_free()
		return {}
	var res: Array = await http.request_completed
	var result := int(res[0])
	var code := int(res[1])
	var bytes: PackedByteArray = res[3]
	http.queue_free()
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		return {}
	var parsed: Variant = JSON.parse_string(bytes.get_string_from_utf8())
	return parsed if parsed is Dictionary else {}


func _read_log_tail(lines: int) -> String:
	if _log_path.is_empty() or not FileAccess.file_exists(_log_path):
		return "（日志为空：%s）" % _log_path
	var f := FileAccess.open(_log_path, FileAccess.READ)
	if f == null:
		return "（无法读取日志）"
	var all := f.get_as_text()
	f.close()
	var parts := all.split("\n")
	var start := maxi(0, parts.size() - lines)
	return "\n".join(parts.slice(start))


func _set_status(s: String, d: String) -> void:
	status = s
	detail = d
	status_changed.emit(s, d)
