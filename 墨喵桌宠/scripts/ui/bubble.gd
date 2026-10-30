class_name SpeechBubble
extends PanelContainer

## 宣纸气泡：自动换行、等待省略号、自动隐藏、点击收起。

signal bubble_changed
signal dismissed

@onready var text_label: RichTextLabel = $Text

const UI_THEME := preload("res://scripts/ui/ui_theme.gd")

var _anchor := Vector2(260.0, 225.0)
var _hide_token := 0
var _dots_timer: Timer
var _wait_base := ""
var _dots := 0
var _waiting := false
## 常驻文本（如番茄钟倒计时）：非空时代表气泡处于常驻模式
var pinned_text := ""
var _tail: Node2D


func _ready() -> void:
	_dots_timer = Timer.new()
	_dots_timer.wait_time = 0.45
	_dots_timer.timeout.connect(_on_dots)
	add_child(_dots_timer)
	_make_tail()
	hide_bubble()


## 气泡小尾巴：指向角色头顶；随 _reposition_emit 跟随面板尺寸。
func _make_tail() -> void:
	_tail = Node2D.new()
	_tail.name = "Tail"
	var fill := Polygon2D.new()
	fill.polygon = PackedVector2Array([
		Vector2(-10, -4), Vector2(10, -4), Vector2(0, 9),
	])
	fill.color = UI_THEME.PAPER
	_tail.add_child(fill)
	for pts in [[Vector2(-10, -3), Vector2(0, 9)], [Vector2(10, -3), Vector2(0, 9)]]:
		var edge := Line2D.new()
		edge.points = PackedVector2Array([pts[0], pts[1]])
		edge.width = 1.2
		edge.default_color = UI_THEME.BORDER
		_tail.add_child(edge)
	add_child(_tail)


func set_anchor_point(anchor: Vector2) -> void:
	_anchor = anchor
	if visible:
		_reposition_emit()


func show_text(text: String, duration: float = 4.0) -> void:
	_stop_waiting()
	pinned_text = ""
	text_label.text = text
	_show_and_reposition()
	_schedule_hide(duration)


## 常驻显示（番茄钟倒计时等）：不自动隐藏，由调用方周期性更新文本。
func show_pinned(text: String) -> void:
	_stop_waiting()
	pinned_text = text
	text_label.text = text
	if visible:
		_reposition_emit()
	else:
		_show_and_reposition()


func show_result(text: String, duration: float = 8.0) -> void:
	show_text(text, duration)


func show_waiting(text: String) -> void:
	_wait_base = text
	_dots = 0
	_waiting = true
	text_label.text = text
	_show_and_reposition()
	_hide_token += 1
	_dots_timer.start()


func hide_bubble() -> void:
	_hide_token += 1
	_stop_waiting()
	pinned_text = ""
	if visible:
		visible = false
		bubble_changed.emit()


func is_showing() -> bool:
	return visible


func get_passthrough_polygons() -> Array[PackedVector2Array]:
	var out: Array[PackedVector2Array] = []
	if not visible:
		return out
	# 底部外扩 12px：小尾巴 + 阴影超出面板矩形，不扩会被穿透多边形裁掉（Windows 不绘制）
	var r := get_global_rect().grow_individual(4, 4, 4, 14)
	out.append(PackedVector2Array([
		r.position,
		Vector2(r.end.x, r.position.y),
		r.end,
		Vector2(r.position.x, r.end.y),
	]))
	return out


func _show_and_reposition() -> void:
	visible = true
	call_deferred("_reposition_emit")


func _reposition_emit() -> void:
	if not visible:
		return
	reset_size()
	var sz := get_combined_minimum_size()
	size = sz
	var viewport_w := get_viewport_rect().size.x
	var x := clampf(_anchor.x - sz.x * 0.5, 12.0, maxf(12.0, viewport_w - sz.x - 12.0))
	var y := _anchor.y - sz.y
	if y < 8.0:
		y = _anchor.y + 18.0
	position = Vector2(x, y)
	if _tail != null:
		_tail.position = Vector2(sz.x * 0.5, sz.y - 2.0)
	bubble_changed.emit()


func _schedule_hide(duration: float) -> void:
	_hide_token += 1
	var token := _hide_token
	_auto_hide(duration, token)


func _auto_hide(duration: float, token: int) -> void:
	await get_tree().create_timer(duration).timeout
	if token == _hide_token:
		hide_bubble()


func _on_dots() -> void:
	if not _waiting:
		return
	_dots = (_dots + 1) % 4
	text_label.text = _wait_base + ".".repeat(_dots)
	call_deferred("_reposition_emit")


func _stop_waiting() -> void:
	_waiting = false
	if _dots_timer != null:
		_dots_timer.stop()


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		hide_bubble()
		dismissed.emit()
		accept_event()
