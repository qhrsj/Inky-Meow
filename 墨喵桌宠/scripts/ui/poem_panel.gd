class_name PoemPanel
extends PanelContainer

## 诗笺面板：列出收藏的诗，可逐条删除。纯代码构建（不进场景文件）。
## open(entries) 时重建列表；删除走 entry_deleted 信号由 main 落盘。

signal opened
signal closed
signal entry_deleted(index: int)

const PANEL_MIN := Vector2(460, 380)

var _list_box: VBoxContainer
var _title_label: Label


func _ready() -> void:
	custom_minimum_size = PANEL_MIN
	visible = false

	var vbox := VBoxContainer.new()
	vbox.add_theme_constant_override("separation", 8)
	add_child(vbox)

	var header := HBoxContainer.new()
	vbox.add_child(header)

	_title_label = Label.new()
	_title_label.text = "诗笺"
	_title_label.add_theme_color_override("font_color", Color(0.545, 0.118, 0.118))
	_title_label.add_theme_font_size_override("font_size", 20)
	header.add_child(_title_label)

	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(spacer)

	var close_btn := Button.new()
	close_btn.text = "收起"
	close_btn.custom_minimum_size = Vector2(64, 28)
	close_btn.pressed.connect(close)
	header.add_child(close_btn)

	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(0, 260)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	vbox.add_child(scroll)

	_list_box = VBoxContainer.new()
	_list_box.add_theme_constant_override("separation", 12)
	_list_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_list_box)


func open(entries: Array) -> void:
	_rebuild(entries)
	visible = true
	_center()
	opened.emit()


func close() -> void:
	if not visible:
		return
	visible = false
	closed.emit()


func _rebuild(entries: Array) -> void:
	for child in _list_box.get_children():
		child.queue_free()
	_title_label.text = "诗笺（%d）" % entries.size()
	for i in entries.size():
		_list_box.add_child(_make_entry(i, entries[i]))


func _make_entry(index: int, entry: Dictionary) -> Control:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 2)

	var head := HBoxContainer.new()
	box.add_child(head)

	var title := Label.new()
	title.text = str(entry.get("title", "无题"))
	title.add_theme_font_size_override("font_size", 18)
	title.add_theme_color_override("font_color", Color(0.35, 0.2, 0.1))
	head.add_child(title)

	var meta := Label.new()
	var saved := str(entry.get("saved_at", ""))
	meta.text = ("　" + saved) if not saved.is_empty() else ""
	meta.add_theme_font_size_override("font_size", 13)
	meta.add_theme_color_override("font_color", Color(0.45, 0.42, 0.38))
	head.add_child(meta)

	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(spacer)

	var del_btn := Button.new()
	del_btn.text = "删"
	del_btn.custom_minimum_size = Vector2(36, 24)
	del_btn.pressed.connect(func(): entry_deleted.emit(index))
	head.add_child(del_btn)

	var body := Label.new()
	body.text = str(entry.get("body", ""))
	body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	body.add_theme_font_size_override("font_size", 16)
	box.add_child(body)

	return box


func _center() -> void:
	reset_size()
	var sz := get_combined_minimum_size()
	var vp := get_viewport_rect().size
	position = Vector2((vp.x - sz.x) * 0.5, maxf(160.0, (vp.y - sz.y) * 0.5))
