class_name PetHitArea
extends Control

## 角色命中区：区分单击/双击/右键/拖动。
## 拖动用屏幕坐标轮询，鼠标移出窗口也能收到松手。

signal clicked
signal double_clicked
signal context_menu_requested(screen_pos: Vector2i)
signal drag_begin(screen_start: Vector2i)
signal drag_direction(dir: int)     # 1=向右/向前，-1=向左/背向
signal drag_end

const DRAG_THRESHOLD := 6.0

var _pressed := false
var _dragging := false
var _press_screen := Vector2i.ZERO
var _last_screen := Vector2i.ZERO


func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_STOP
	set_process(false)


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				if event.double_click:
					double_clicked.emit()
					accept_event()
					return
				_pressed = true
				_dragging = false
				_press_screen = DisplayServer.mouse_get_position()
				_last_screen = _press_screen
				set_process(true)
				accept_event()
			else:
				_finish_press()
				accept_event()
		elif event.button_index == MOUSE_BUTTON_RIGHT and event.pressed:
			context_menu_requested.emit(DisplayServer.mouse_get_position())
			accept_event()


func _process(_delta: float) -> void:
	if not _pressed:
		return
	var now := DisplayServer.mouse_get_position()
	if not Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
		_finish_press()
		return
	if not _dragging and Vector2(now - _press_screen).length() >= DRAG_THRESHOLD:
		_dragging = true
		drag_begin.emit(_press_screen)
	if _dragging:
		var dx := now.x - _last_screen.x
		if absi(dx) >= 3:
			drag_direction.emit(1 if dx > 0 else -1)
	_last_screen = now


func cancel_press() -> void:
	_pressed = false
	_dragging = false
	set_process(false)


func _finish_press() -> void:
	if not _pressed:
		return
	_pressed = false
	set_process(false)
	if _dragging:
		_dragging = false
		drag_end.emit()
	else:
		clicked.emit()
