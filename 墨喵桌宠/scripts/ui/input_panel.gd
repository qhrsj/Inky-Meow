class_name InputPanel
extends PanelContainer

## 通用诗笺输入面板：四个功能共用，可在面板内切换。

signal submitted(cmd: String, text: String)
signal voice_started
signal voice_stopped
signal opened
signal closed

const CMD_META := {
	"theme": {
		"title": "点题喂猫",
		"prompt": "输入一个词（1–4 字，如：孤舟）",
		"placeholder": "孤舟",
	},
	"couplet": {
		"title": "出上句 · 对下句",
		"prompt": "输入上句（五言或七言）",
		"placeholder": "空山新雨后",
	},
	"check": {
		"title": "贴诗 · 格律体检",
		"prompt": "粘贴诗句，标点可有可无",
		"placeholder": "床前明月光，疑是地上霜。",
	},
	"write": {
		"title": "给开头 · 续写",
		"prompt": "输入开头（如：空山新雨后，）",
		"placeholder": "空山新雨后，",
	},
	"chat": {
		"title": "与墨喵说话",
		"prompt": "闲聊、作诗、对句都能听懂",
		"placeholder": "今天有点累……",
	},
}

@onready var title_label: Label = $VBox/Header/Title
@onready var voice_button: Button = $VBox/Header/Voice
@onready var close_button: Button = $VBox/Header/Close
@onready var prompt_label: Label = $VBox/Prompt
@onready var editor: TextEdit = $VBox/Editor
@onready var hint_label: Label = $VBox/Hint
@onready var result_label: RichTextLabel = $VBox/Result
@onready var buttons: Dictionary = {
	"theme": $VBox/Buttons/ThemeBtn,
	"couplet": $VBox/Buttons/CoupletBtn,
	"check": $VBox/Buttons/CheckBtn,
	"write": $VBox/Buttons/WriteBtn,
	"chat": $VBox/Buttons/ChatBtn,
}

const UI_THEME := preload("res://scripts/ui/ui_theme.gd")

const COLLAPSED_HEIGHT := 220.0
const EXPANDED_HEIGHT := 338.0

## 可重做的命令（体检结果没有「换一首」的意义）
const REDO_CMDS := ["theme", "couplet", "write", "chat"]

var current_cmd := "theme"
var _busy := false
var _poetry_ready := false
var _chat_ready := false
var _voice_ready := false
var _target_height := COLLAPSED_HEIGHT
var refresh_button: Button
var send_button: Button
var _last_cmd := ""
var _last_text := ""


func _ready() -> void:
	visible = false
	set_process(true)
	_target_height = COLLAPSED_HEIGHT
	title_label.add_theme_color_override("font_color", UI_THEME.CINNABAR)
	title_label.add_theme_font_size_override("font_size", 19)
	close_button.pressed.connect(close)
	voice_button.button_down.connect(_on_voice_down)
	voice_button.button_up.connect(_on_voice_up)
	_make_refresh_button()
	_make_send_button()
	editor.gui_input.connect(_on_editor_input)
	for cmd in buttons.keys():
		buttons[cmd].pressed.connect(_on_cmd_button.bind(cmd))
	editor.text_changed.connect(_on_text_changed)
	_apply_cmd(current_cmd)
	_update_buttons()


## 发送按钮：悬浮在输入框内部右下角（像聊天软件那样），每种模式通用
func _make_send_button() -> void:
	send_button = Button.new()
	send_button.text = "发送"
	send_button.custom_minimum_size = Vector2(64, 30)
	var sb := UI_THEME.button_box(UI_THEME.CINNABAR, UI_THEME.CINNABAR)
	send_button.add_theme_stylebox_override("normal", sb)
	var hov := UI_THEME.button_box(UI_THEME.CINNABAR_HOVER, UI_THEME.CINNABAR_HOVER)
	send_button.add_theme_stylebox_override("hover", hov)
	send_button.add_theme_stylebox_override("pressed", hov)
	var dis := UI_THEME.button_box(UI_THEME.CINNABAR, UI_THEME.CINNABAR)
	dis.bg_color = Color(UI_THEME.CINNABAR.r, UI_THEME.CINNABAR.g, UI_THEME.CINNABAR.b, 0.4)
	send_button.add_theme_stylebox_override("disabled", dis)
	for col in ["font_color", "font_hover_color", "font_pressed_color", "font_disabled_color"]:
		send_button.add_theme_color_override(col, UI_THEME.PAPER)
	send_button.pressed.connect(_submit)

	# 编辑框包进一层容器，按钮锚定在容器内右下角
	var parent := editor.get_parent()
	var wrap := Control.new()
	wrap.name = "EditorWrap"
	wrap.custom_minimum_size = editor.custom_minimum_size
	var idx := editor.get_index()
	parent.remove_child(editor)
	parent.add_child(wrap)
	parent.move_child(wrap, idx)
	wrap.add_child(editor)
	editor.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	wrap.add_child(send_button)
	send_button.set_anchors_preset(Control.PRESET_BOTTOM_RIGHT)
	send_button.offset_left = -76.0
	send_button.offset_top = -38.0
	send_button.offset_right = -8.0
	send_button.offset_bottom = -8.0


## Ctrl+Enter 快捷发送（Enter 留给换行）
func _on_editor_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo \
			and event.keycode in [KEY_ENTER, KEY_KP_ENTER] and event.ctrl_pressed:
		_submit()
		get_viewport().set_input_as_handled()


## 结果区旁的「换一首」：重发上一次的命令+输入（不看编辑框现在写了什么）。
func _make_refresh_button() -> void:
	refresh_button = Button.new()
	refresh_button.text = "换一首"
	refresh_button.custom_minimum_size = Vector2(64, 28)
	refresh_button.visible = false
	refresh_button.pressed.connect(_on_refresh)
	var header := $VBox/Header
	header.add_child(refresh_button)
	header.move_child(refresh_button, close_button.get_index())   # 插到关闭键前面


func open(cmd: String = "theme") -> void:
	current_cmd = cmd if CMD_META.has(cmd) else "theme"
	_apply_cmd(current_cmd)
	clear_result()
	_set_hint("")
	set_busy(false)
	_update_buttons()
	visible = true
	_set_height(COLLAPSED_HEIGHT)
	var win := get_window()
	if win != null:
		win.grab_focus()
	call_deferred("_focus_editor")
	opened.emit()


func close() -> void:
	if not visible:
		return
	visible = false
	closed.emit()


func is_open() -> bool:
	return visible


func set_busy(busy: bool) -> void:
	_busy = busy
	editor.editable = not busy
	if refresh_button != null:
		refresh_button.disabled = busy
	if send_button != null:
		send_button.disabled = busy
	_update_buttons()
	close_button.disabled = false


func set_model_ready(poetry: bool, chat: bool) -> void:
	_poetry_ready = poetry
	_chat_ready = chat
	_update_buttons()


func set_voice_ready(is_ready: bool) -> void:
	_voice_ready = is_ready
	if voice_button != null:
		voice_button.disabled = _busy or not _voice_ready
		if not _voice_ready:
			voice_button.text = "语音"


func set_voice_recording(recording: bool) -> void:
	if voice_button != null:
		voice_button.text = "松开" if recording else "语音"


func show_hint(text: String) -> void:
	_set_hint(text)


func _update_buttons() -> void:
	for key in buttons.keys():
		var b := buttons[key] as Button
		if b == null:
			continue
		var is_ready: bool = _chat_ready if key == "chat" else _poetry_ready
		b.disabled = _busy or not is_ready
	if voice_button != null:
		voice_button.disabled = _busy or not _voice_ready


func show_progress(text: String) -> void:
	_set_height(EXPANDED_HEIGHT)
	_show_result_text("[color=#7a6a52]%s[/color]" % escape_bbcode(text))


func show_result(title: String, body: String) -> void:
	_set_height(EXPANDED_HEIGHT)
	_show_refresh()
	_show_result_text("[b][color=#8b1e1e]%s[/color][/b]\n%s" % [
		escape_bbcode(title),
		escape_bbcode(body),
	])


func show_error(message: String) -> void:
	_set_height(EXPANDED_HEIGHT)
	_show_result_text("[color=#8b1e1e]%s[/color]" % escape_bbcode(message))


func clear_result() -> void:
	result_label.text = ""
	result_label.visible = false
	_hide_refresh()
	_set_height(COLLAPSED_HEIGHT)


func _show_refresh() -> void:
	if refresh_button != null:
		refresh_button.visible = _last_cmd in REDO_CMDS


func _hide_refresh() -> void:
	if refresh_button != null:
		refresh_button.visible = false


func _on_refresh() -> void:
	if _busy or _last_cmd.is_empty():
		return
	current_cmd = _last_cmd
	_apply_cmd(current_cmd)
	editor.text = _last_text
	_set_hint("")
	_submit()


func _set_height(h: float) -> void:
	_target_height = h
	custom_minimum_size = Vector2(480.0, h)
	size = Vector2(maxf(size.x, 480.0), h)


func _process(_delta: float) -> void:
	if not visible:
		return
	if absf(size.y - _target_height) > 0.5:
		custom_minimum_size = Vector2(480.0, _target_height)
		size = Vector2(maxf(size.x, 480.0), _target_height)


func get_input_text() -> String:
	return editor.text.strip_edges()


static func escape_bbcode(text: String) -> String:
	return text.replace("[", "[lb]")


func _focus_editor() -> void:
	editor.grab_focus()


func _on_voice_down() -> void:
	if _busy or not _voice_ready:
		return
	voice_started.emit()


func _on_voice_up() -> void:
	if not _voice_ready:
		return
	voice_stopped.emit()


func _on_cmd_button(cmd: String) -> void:
	if _busy:
		return
	if cmd == "chat" and not _chat_ready:
		_set_hint("闲聊还在加载，先用点题/对句吧喵。")
		return
	if cmd != "chat" and not _poetry_ready:
		_set_hint("诗模型还没醒，稍等喵。")
		return
	current_cmd = cmd
	_apply_cmd(cmd)
	_submit()


func _submit() -> void:
	var text := editor.text.strip_edges()
	if text.is_empty():
		_set_hint("先写点什么再落笔吧喵。")
		return
	if current_cmd == "chat" and not _chat_ready:
		_set_hint("闲聊还在加载，先用点题/对句吧喵。")
		return
	if current_cmd != "chat" and not _poetry_ready:
		_set_hint("诗模型还没醒，稍等喵。")
		return
	_set_hint("")
	clear_result()
	_last_cmd = current_cmd
	_last_text = text
	submitted.emit(current_cmd, text)


func _on_text_changed() -> void:
	if not hint_label.text.is_empty():
		_set_hint("")


func _apply_cmd(cmd: String) -> void:
	var meta: Dictionary = CMD_META.get(cmd, CMD_META["theme"])
	title_label.text = str(meta.title)
	prompt_label.text = str(meta.prompt)
	editor.placeholder_text = str(meta.placeholder)
	for key in buttons.keys():
		var b := buttons[key] as Button
		b.modulate = Color(1.0, 0.9, 0.9) if key == cmd else Color.WHITE


func _show_result_text(bbcode: String) -> void:
	result_label.text = bbcode
	result_label.visible = true


func _set_hint(text: String) -> void:
	hint_label.text = text
	hint_label.visible = not text.is_empty()


func _input(event: InputEvent) -> void:
	if not visible:
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
		close()
		get_viewport().set_input_as_handled()
