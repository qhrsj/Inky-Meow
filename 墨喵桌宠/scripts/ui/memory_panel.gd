class_name MemoryPanel
extends PanelContainer

## 查看墨喵记得的名字/称呼/小事；清空走 ConfirmPanel。

signal refresh_requested
signal clear_requested
signal opened
signal closed

@onready var body_label: RichTextLabel = $VBox/Body
@onready var refresh_button: Button = $VBox/Buttons/Refresh
@onready var clear_button: Button = $VBox/Buttons/Clear
@onready var close_button: Button = $VBox/Header/Close


func _ready() -> void:
	visible = false
	refresh_button.pressed.connect(func(): refresh_requested.emit())
	clear_button.pressed.connect(func(): clear_requested.emit())
	close_button.pressed.connect(close)


func open() -> void:
	visible = true
	set_loading()
	reset_size()
	size = Vector2(maxf(size.x, 460.0), maxf(size.y, 340.0))
	var vp := get_viewport_rect().size
	position = Vector2((vp.x - size.x) * 0.5, maxf(160.0, (vp.y - size.y) * 0.5))
	var win := get_window()
	if win != null:
		win.grab_focus()
	opened.emit()


func set_loading() -> void:
	body_label.text = "读取中…"


func show_data(data: Dictionary) -> void:
	var pet_v: Variant = data.get("pet_name", null)
	var user_v: Variant = data.get("user_name", null)
	var pet := "墨喵" if pet_v == null or str(pet_v).is_empty() else str(pet_v)
	var user := "" if user_v == null else str(user_v)
	if user.is_empty() or user == "<null>":
		user = "还不知道"
	var lines: Array[String] = ["名字：" + pet, "主人：" + user, "", "记得："]
	var facts_v: Variant = data.get("facts", [])
	var facts: Array = facts_v if facts_v is Array else []
	if facts.is_empty():
		lines.append("还没有记住别的事。")
	else:
		for f in facts:
			lines.append("· " + str(f))
	body_label.text = "\n".join(lines)


func show_error(message: String) -> void:
	body_label.text = "读取失败：" + message


func close() -> void:
	if not visible:
		return
	visible = false
	closed.emit()


func is_open() -> bool:
	return visible


func _input(event: InputEvent) -> void:
	if not visible:
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
		close()
		get_viewport().set_input_as_handled()
