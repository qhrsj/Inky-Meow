class_name VoiceInput
extends Node

## 按住面板「语音」说话：/api/asr/start 开始录音（录音在 sidecar 里用
## sounddevice 做，Godot 不碰麦克风）→ /api/asr/stop 停止并转写 → 直接发给墨喵。
## 语音模式下 Main 会自动打开 TTS，形成说→回→再说。

signal transcript(text: String)
signal failed(message: String)

const MIN_RECORD_SEC := 0.35

var model: ModelClient
var panel: InputPanel
var brain: PetBrain
var voice: VoicePlayer
var bubble: SpeechBubble

var _starting := false
var _stop_requested := false
var _discard := false
var _recording := false
var _busy := false
var _record_started_ms := 0


func setup(model_ref: ModelClient, panel_ref: InputPanel, brain_ref: PetBrain,
		voice_ref: VoicePlayer, bubble_ref: SpeechBubble) -> void:
	model = model_ref
	panel = panel_ref
	brain = brain_ref
	voice = voice_ref
	bubble = bubble_ref


func start_recording() -> void:
	if _busy or _recording or _starting:
		return
	if model == null or not model.is_asr_available() or not model.is_chat_available():
		_fail("语音还没准备好喵。")
		return
	if panel != null and not panel.is_open():
		panel.open("chat")
	if voice != null:                        # 别把墨喵自己的朗读录进去
		voice.stop()

	_starting = true
	_stop_requested = false
	var res: Dictionary = await model.asr_start()
	_starting = false
	if not res.get("ok", false):
		_stop_requested = false
		_discard = false
		var err: Dictionary = res.get("error", {})
		_fail(str(err.get("message", "麦克风打不开喵。")))
		return

	_recording = true
	_record_started_ms = Time.get_ticks_msec()
	if panel != null:
		panel.set_voice_recording(true)
		panel.show_hint("听着呢，说完松开喵。")
	if _stop_requested:                      # 松手太早：start 回来时已经松了
		_stop_requested = false
		await stop_recording()


func stop_recording() -> void:
	if _starting:                            # start 还没回来，等它回来再收尾
		_stop_requested = true
		return
	if not _recording:
		return
	_recording = false
	var seconds := (Time.get_ticks_msec() - _record_started_ms) / 1000.0
	if panel != null:
		panel.set_voice_recording(false)

	_busy = true
	var too_short := seconds < MIN_RECORD_SEC
	if panel != null:
		panel.set_voice_ready(false)
		if not too_short:
			panel.show_hint("正在听……")
	var res: Dictionary = await model.asr_stop()
	_busy = false
	if panel != null:
		panel.set_voice_ready(model.is_asr_available() and model.is_chat_available())
	if _discard:                             # 面板被关/退出：只收尾，不发送
		_discard = false
		return
	if too_short:
		if panel != null:
			panel.show_hint("按住久一点再松开喵。")
		return
	if not res.get("ok", false):
		var err: Dictionary = res.get("error", {})
		_fail(str(err.get("message", "没听清，再说一遍喵。")))
		return

	var data: Dictionary = res.get("data", {})
	var text := str(data.get("text", "")).strip_edges()
	if text.is_empty():
		if str(data.get("reason", "")) == "silence":
			_fail("没听到声音，凑近点再说喵。")
		else:
			_fail("没听清，再说一遍喵。")
		return
	transcript.emit(text)
	if panel != null:
		panel.open("chat")
		panel.editor.text = text
		panel._submit()
	elif brain != null:
		brain.run_command("chat", text)


func is_recording() -> bool:
	return _recording or _starting


## 面板被关掉/程序退出时兜底：停掉录音，不把半句话发出去。
func cancel_recording() -> void:
	_discard = true
	if _starting:
		_stop_requested = true
		return
	if not _recording:
		_discard = false
		return
	await stop_recording()


func _fail(message: String) -> void:
	failed.emit(message)
	if panel != null and panel.is_open():
		panel.show_hint(message)
	elif bubble != null:
		bubble.show_text(message, 3.0)
