class_name PetBrain
extends Node

## 命令编排：状态机请求令牌 + ModelClient + 气泡/面板展示 + 可选朗读。

signal result_ready(cmd: String, data: Dictionary)

const LOG := preload("res://scripts/util/app_log.gd")

const WAIT_TEXT := {
	"theme": "让我想想，替你这句诗点个题……",
	"couplet": "平仄推敲中……",
	"check": "正在逐字体检……",
	"write": "墨已研好，正在续写……",
	"chat": "墨喵歪着头听……",
}

const CMD_TITLE := {
	"theme": "点题",
	"couplet": "对句",
	"check": "体检",
	"write": "续写",
	"chat": "闲聊",
}

var sm: PetStateMachine
var model: ModelClient
var bubble: SpeechBubble
var panel: InputPanel
var voice = null            # VoicePlayer；M2 可选

var _last_text := ""


func setup(sm_ref: PetStateMachine, model_ref: ModelClient,
		bubble_ref: SpeechBubble, panel_ref: InputPanel, voice_ref = null) -> void:
	sm = sm_ref
	model = model_ref
	bubble = bubble_ref
	panel = panel_ref
	voice = voice_ref
	if sm != null and not sm.request_timed_out.is_connected(_on_request_timed_out):
		sm.request_timed_out.connect(_on_request_timed_out)


func run_command(cmd: String, text: String) -> void:
	if sm == null or model == null:
		return
	if cmd == "chat":
		if not model.is_chat_available():
			_show_error("model_not_ready", "闲聊还在加载，先用点题/对句吧。")
			return
	elif not model.is_poetry_available():
		_show_error("model_not_ready", "诗模型还没醒，稍等喵。")
		return

	_last_text = text
	var payload := _make_payload(cmd, text)
	LOG.info("请求开始 cmd=%s input=%s" % [cmd, _short(text)])
	var id := sm.begin_request(cmd)
	if id < 0:
		LOG.warn("请求拒绝 cmd=%s reason=busy" % cmd)
		_show_error("busy", "还在吟诗，稍等一下喵。")
		return

	var wait := str(WAIT_TEXT.get(cmd, "让我想想……"))
	if panel != null and panel.is_open():
		panel.set_busy(true)
		panel.show_progress(wait)
	else:
		bubble.show_waiting(wait)

	var res: Dictionary = await model.request(cmd, payload)
	if sm == null or not sm.is_request_current(id):
		if panel != null:
			panel.set_busy(false)
		return

	if res.get("ok", false):
		sm.finish_request(id, true)
		var data: Dictionary = res.get("data", {})
		LOG.info("请求完成 cmd=%s ok=true latency=%dms" % [cmd, int(res.get("latency_ms", 0))])
		var fmt := format_result(cmd, data, _last_text)
		_present(fmt)
		_speak(str(fmt.get("speech", "")))
		result_ready.emit(cmd, data)
	else:
		var err: Dictionary = res.get("error", {})
		var code := str(err.get("code", "internal"))
		LOG.warn("请求失败 cmd=%s ok=false code=%s message=%s latency=%dms" % [
			cmd, code, str(err.get("message", "")), int(res.get("latency_ms", 0))])
		sm.finish_request(id, false, code)
		_show_error(code, str(err.get("message", "")))

	if panel != null:
		panel.set_busy(false)


func _on_request_timed_out(_id: int, _cmd: String) -> void:
	if panel != null:
		panel.set_busy(false)
	_show_error("timeout", "")


func _make_payload(cmd: String, text: String) -> Dictionary:
	match cmd:
		"theme":
			return {"word": text.strip_edges()}
		"couplet":
			return {"upper": ModelClient.normalize_verse(text)}
		"check":
			return {"text": text}
		"write":
			return {"prompt": text}
		"chat":
			return {"text": text.strip_edges()}
	return {}


func _present(fmt: Dictionary) -> void:
	var title := str(fmt.get("title", ""))
	var body := str(fmt.get("body", ""))
	if panel != null and panel.is_open():
		panel.show_result(title, body)
	else:
		var full := "%s\n%s" % [title, body] if not title.is_empty() else body
		if full.length() > 160:
			full = full.substr(0, 160) + "…"
		bubble.show_result(full, 8.0)


func _speak(text: String) -> void:
	if text.is_empty() or voice == null:
		return
	if not voice.has_method("speak"):
		return
	if not bool(voice.get("enabled")):
		return
	voice.speak(text)


func _show_error(code: String, message: String) -> void:
	var text := error_text(code, message)
	if panel != null and panel.is_open():
		panel.show_error(text)
	else:
		bubble.show_text(text, 3.0)


# ----- 纯函数：便于单测 -----

static func format_result(cmd: String, data: Dictionary, input_text: String) -> Dictionary:
	var title := str(CMD_TITLE.get(cmd, "结果"))
	var body := ""
	var speech := ""
	match cmd:
		"theme":
			var form := str(data.get("form", ""))
			title = "点题" + (" · " + form if not form.is_empty() else "")
			body = str(data.get("poem", ""))
			var at := str(data.get("keyword_at", ""))
			if not at.is_empty():
				body += "\n（题在%s）" % at
			var note := str(data.get("note", "")) if data.get("note") != null else ""
			if not note.is_empty():
				body += "\n" + note
			speech = str(data.get("poem", ""))
		"couplet":
			title = "对句"
			var items_v: Variant = data.get("items", [])
			var items: Array = items_v if items_v is Array else []
			if items.is_empty():
				body = "没有合律的候选。"
			else:
				var best: Dictionary = items[0] if items[0] is Dictionary else {}
				body = "上句：%s\n下句：%s" % [
					ModelClient.normalize_verse(input_text),
					str(best.get("text", "")),
				]
				if items.size() > 1:
					var others: Array[String] = []
					for i in range(1, mini(3, items.size())):
						var it: Dictionary = items[i] if items[i] is Dictionary else {}
						others.append(str(it.get("text", "")))
					body += "\n另两句：" + "；".join(others)
				if best.has("score") and best["score"] != null:
					body += "\n评分：%.2f" % float(best["score"])
				var note := str(data.get("note", "")) if data.get("note") != null else ""
				if not note.is_empty():
					body += "\n" + note
				speech = "%s，%s。" % [ModelClient.normalize_verse(input_text), str(best.get("text", ""))]
		"check":
			var form := str(data.get("form", ""))
			title = "体检" + (" · " + form if not form.is_empty() else "")
			var lines: Array[String] = []
			var jb := _arr(data.get("jushi_bad", []))
			lines.append("句式  " + ("✓" if data.get("jushi_ok", false) else "✗ " + "；".join(jb)))
			if data.get("pingze_bad") == null:
				lines.append("平仄  —（句式不合，无法判）")
			else:
				var pb := _arr(data.get("pingze_bad", []))
				lines.append("平仄  " + ("✓" if data.get("pingze_ok", false) else "✗ " + "；".join(pb)))
			var rhymes := _arr(data.get("rhymes", []))
			var yb := _arr(data.get("yayun_bad", []))
			if rhymes.is_empty():
				lines.append("押韵  —（句数不足）")
			elif data.get("yayun_ok", false):
				lines.append("押韵  ✓ " + "、".join(rhymes))
			else:
				lines.append("押韵  ✗ " + "、".join(yb))
			body = "\n".join(lines)
		"write":
			title = "续写"
			body = str(data.get("poem", ""))
			var note := str(data.get("note", "")) if data.get("note") != null else ""
			if not note.is_empty():
				body += "\n" + note
			speech = str(data.get("poem", ""))
		"chat":
			title = "闲聊"
			var reply := str(data.get("reply", ""))
			var poem := str(data.get("poem", "")) if data.get("poem") != null else ""
			body = reply
			if not poem.is_empty() and reply.find(poem) == -1:
				body += ("\n" if not body.is_empty() else "") + poem
			var note := str(data.get("note", "")) if data.get("note") != null else ""
			if not note.is_empty():
				body += "\n" + note
			speech = poem if not poem.is_empty() else reply
			if speech.length() > 120:
				speech = speech.substr(0, 120) + "…"
	return {"title": title, "body": body, "speech": speech}


static func error_text(code: String, message: String) -> String:
	match code:
		"business":
			return message if not message.is_empty() else "这事做不了喵。"
		"invalid_input":
			return message if not message.is_empty() else "这个输入还差点意思喵。"
		"sidecar_offline":
			return "模型还没醒，右键菜单可以重连喵。"
		"model_not_ready":
			return message if not message.is_empty() else "模型还在加载，稍等喵。"
		"timeout":
			return "等了太久，墨喵走神了。"
		"busy":
			return message if not message.is_empty() else "还在吟诗，稍等一下喵。"
		"tts_unavailable":
			return "嗓子暂时哑了喵（TTS 不可用）。"
		"internal":
			return "出了点小差错：%s" % message if not message.is_empty() else "出了点小差错。"
	return message if not message.is_empty() else "喵？信号好像断了。"


static func _arr(v: Variant) -> Array:
	return v if v is Array else []


static func _short(text: String, limit: int = 40) -> String:
	var t := text.strip_edges().replace("\n", " ")
	return t.substr(0, limit) + "…" if t.length() > limit else t
