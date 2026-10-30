@tool
class_name ScreenUtils
extends RefCounted

## 屏幕/窗口位置工具：全部使用屏幕像素坐标，支持负坐标与多显示器。

static func screen_for_point(point: Vector2i) -> int:
	var screen := DisplayServer.get_screen_from_rect(Rect2(point, Vector2.ONE))
	if screen < 0:
		screen = DisplayServer.get_primary_screen()
	return screen


static func usable_rect_for_point(point: Vector2i) -> Rect2i:
	return DisplayServer.screen_get_usable_rect(screen_for_point(point))


static func default_window_pos(window_size: Vector2i, margin: int = 24) -> Vector2i:
	var rect := usable_rect_for_point(DisplayServer.mouse_get_position())
	return rect.position + rect.size - window_size - Vector2i(margin, margin)


static func clamp_window_pos(pos: Vector2i, window_size: Vector2i) -> Vector2i:
	var center := pos + Vector2i(int(window_size.x * 0.5), int(window_size.y * 0.5))
	var rect := usable_rect_for_point(center)
	var max_pos := rect.position + rect.size - window_size
	return Vector2i(
		clampi(pos.x, rect.position.x, maxi(rect.position.x, max_pos.x)),
		clampi(pos.y, rect.position.y, maxi(rect.position.y, max_pos.y))
	)


static func is_window_pos_visible(pos: Vector2i, window_size: Vector2i) -> bool:
	var window_rect := Rect2i(pos, window_size)
	for i in DisplayServer.get_screen_count():
		if DisplayServer.screen_get_usable_rect(i).intersects(window_rect):
			return true
	return false
