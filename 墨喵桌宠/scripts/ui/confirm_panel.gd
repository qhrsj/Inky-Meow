class_name ConfirmPanel
extends PanelContainer

## 轻量确认面板：场景感知隐私、清空记忆等。

signal confirmed
signal cancelled
signal opened
signal closed

@onready var title_label: Label = $VBox/Title
@onready var body_label: RichTextLabel = $VBox/Body
@onready var confirm_button: Button = $VBox/Buttons/Confirm
@onready var cancel_button: Button = $VBox/Buttons/Cancel


func _ready() -> void:
	visible = false
	confirm_button.pressed.connect(_on_confirm)
	cancel_button.pressed.connect(_on_cancel)


func ask(title: String, body: String, confirm_text: String = "确定",
		cancel_text: String = "取消") -> void:
	title_label.text = title
	body_label.text = body
	confirm_button.text = confirm_text
	cancel_button.text = cancel_text
	visible = true
	reset_size()
	size = Vector2(maxf(size.x, 420.0), maxf(size.y, 240.0))
	var vp := get_viewport_rect().size
	position = Vector2((vp.x - size.x) * 0.5, maxf(180.0, (vp.y - size.y) * 0.5))
	var win := get_window()
	if win != null:
		win.grab_focus()
	call_deferred("_focus")
	opened.emit()


func close(emit_cancel: bool = false) -> void:
	if not visible:
		return
	visible = false
	if emit_cancel:
		cancelled.emit()
	closed.emit()


func _focus() -> void:
	confirm_button.grab_focus()


func _on_confirm() -> void:
	if not visible:
		return
	visible = false
	confirmed.emit()
	closed.emit()


func _on_cancel() -> void:
	close(true)


func _input(event: InputEvent) -> void:
	if not visible:
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
		close(true)
		get_viewport().set_input_as_handled()
