class_name WindowController
extends Node

## 窗口壳：透明/无边框/置顶/托盘/关闭拦截/拖动/穿透多边形/显示隐藏。

signal drag_started
signal drag_ended
signal hidden_changed(hidden: bool)

const WINDOW_SIZE := Vector2i(520, 700)
const PASSTHROUGH_MARGIN := 4.0
const MAX_POLYGON_POINTS := 240

var character: CharacterView
var bubble: SpeechBubble
var panel: InputPanel
var memory_panel: MemoryPanel
var confirm_panel: ConfirmPanel
var poem_panel: Control          # PoemPanel（preload 构造，弱类型避免类缓存问题）
var tray: StatusIndicator
var ui_layer: CanvasLayer
var debug_hud: Control
var debug_line: Line2D

var _window: Window
var _dragging := false
var _press_screen := Vector2i.ZERO
var _press_window := Vector2i.ZERO
var _hidden := false
var _hidden_position := Vector2i.ZERO
var _dirty := true


func _enter_tree() -> void:
	_window = get_window()
	_window.transparent = true
	_window.borderless = true
	_window.always_on_top = true
	_window.unfocusable = false
	_window.popup_wm_hint = true
	_window.unresizable = true
	_window.size = WINDOW_SIZE
	get_viewport().transparent_bg = true
	_window.mouse_passthrough = true
	_window.mouse_passthrough_polygon = PackedVector2Array()
	get_tree().set_auto_accept_quit(false)
	if not _window.close_requested.is_connected(_on_close_requested):
		_window.close_requested.connect(_on_close_requested)


func _ready() -> void:
	_restore_window()
	_apply_toolwindow_style()
	set_process(true)
	if debug_line != null:
		print("[momiao] window=", _window.size, " viewport=", get_viewport().get_visible_rect().size,
			" content_scale_factor=", _window.content_scale_factor,
			" final_xf=", get_viewport().get_final_transform(),
			" screen_scale=", DisplayServer.screen_get_scale(DisplayServer.window_get_current_screen()))


func setup(refs: Dictionary) -> void:
	character = refs.get("character", null) as CharacterView
	bubble = refs.get("bubble", null) as SpeechBubble
	panel = refs.get("panel", null) as InputPanel
	memory_panel = refs.get("memory_panel", null) as MemoryPanel
	confirm_panel = refs.get("confirm_panel", null) as ConfirmPanel
	poem_panel = refs.get("poem_panel", null) as Control
	tray = refs.get("tray", null) as StatusIndicator
	ui_layer = refs.get("ui_layer", null) as CanvasLayer
	debug_hud = refs.get("debug_hud", null) as Control
	update_tray_icon()
	schedule_refresh()


func _process(_delta: float) -> void:
	if _dragging:
		_update_drag()
	if _dirty and not _dragging and not _hidden:
		_dirty = false
		_rebuild_passthrough()


func schedule_refresh() -> void:
	_dirty = true


## 窗口保持可聚焦（否则鼠标点击/拖动会被 NOACTIVATE 阻掉）；
## 任务栏隐藏改用 Win32 WS_EX_TOOLWINDOW。
func set_keyboard_focus(enabled: bool) -> void:
	if enabled and not _hidden:
		_window.grab_focus()


func _apply_toolwindow_style() -> void:
	if OS.get_name() != "Windows":
		return
	var hwnd := DisplayServer.window_get_native_handle(DisplayServer.WINDOW_HANDLE)
	if hwnd == 0:
		return
	var script := ProjectSettings.globalize_path("res://tools/hide_taskbar.ps1")
	if not FileAccess.file_exists(script):
		push_warning("hide_taskbar.ps1 不存在：" + script)
		return
	var pid := OS.create_process("powershell", [
		"-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
		"-File", script, str(hwnd),
	])
	print("[momiao] toolwindow hwnd=%d ps_pid=%d" % [hwnd, pid])


func is_hidden() -> bool:
	return _hidden


func set_pet_visible(visible_now: bool) -> void:
	if visible_now:
		var was_hidden := _hidden
		_hidden = false
		if was_hidden:
			_window.position = ScreenUtils.clamp_window_pos(_hidden_position, _window.size)
		if ui_layer != null:
			ui_layer.show()
		_window.mouse_passthrough_polygon = PackedVector2Array()
		_window.mouse_passthrough = false
		_dirty = false
		_rebuild_passthrough()
		hidden_changed.emit(false)
		return
	if not _hidden:
		_hidden_position = _window.position
	_hidden = true
	if panel != null:
		panel.close()
	if memory_panel != null and memory_panel.visible:
		memory_panel.close()
	if confirm_panel != null and confirm_panel.visible:
		confirm_panel.close()
	if poem_panel != null and poem_panel.visible:
		poem_panel.close()
	if bubble != null:
		bubble.hide_bubble()
	if ui_layer != null:
		ui_layer.hide()
	_window.mouse_passthrough_polygon = PackedVector2Array()
	_window.mouse_passthrough = true
	# 保留窗口存活以维持托盘菜单，但移出屏幕，避免透明窗口拦住桌面点击。
	_window.position = Vector2i(-10000, -10000)
	hidden_changed.emit(true)


func begin_drag(screen_start: Vector2i) -> void:
	if _hidden:
		return
	_dragging = true
	_press_screen = screen_start
	_press_window = _window.position
	_window.mouse_passthrough = false
	_window.mouse_passthrough_polygon = PackedVector2Array()
	drag_started.emit()


func end_drag() -> void:
	if not _dragging:
		return
	_dragging = false
	ConfigStore.set_value("window", "position", _window.position)
	_dirty = true
	drag_ended.emit()


## 闲逛平移：拖动/隐藏时不响应；位置实时贴边修正
func move_window(delta: Vector2) -> void:
	if _dragging or _hidden:
		return
	_window.position = ScreenUtils.clamp_window_pos(
		_window.position + Vector2i(delta), _window.size)


func update_tray_icon() -> void:
	if tray == null:
		return
	var tex: Texture2D = null
	if character != null:
		tex = character.get_tray_icon()
	if tex == null and ResourceLoader.exists("res://icon.svg"):
		tex = load("res://icon.svg") as Texture2D
	if tex != null:
		tray.icon = tex
	tray.tooltip = "墨喵 · 右键打开诗笺"


func set_debug_line(line: Line2D) -> void:
	debug_line = line
	print("[momiao] window=", _window.size, " viewport=", get_viewport().get_visible_rect().size,
		" content_scale_factor=", _window.content_scale_factor,
		" final_xf=", get_viewport().get_final_transform(),
		" screen_scale=", DisplayServer.screen_get_scale(DisplayServer.window_get_current_screen()))


func quit_app() -> void:
	var pos := _hidden_position if _hidden else _window.position
	ConfigStore.set_value("window", "position", pos)
	get_tree().quit()


func _update_drag() -> void:
	if not Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
		end_drag()
		return
	var now := DisplayServer.mouse_get_position()
	_window.position = ScreenUtils.clamp_window_pos(
		_press_window + (now - _press_screen),
		_window.size
	)


func _rebuild_passthrough() -> void:
	if _hidden:
		_window.mouse_passthrough = true
		_window.mouse_passthrough_polygon = PackedVector2Array()
		return

	var all_points := PackedVector2Array()
	if character != null:
		_append_points(all_points, character.get_passthrough_polygons())
	if bubble != null and bubble.visible:
		_append_points(all_points, bubble.get_passthrough_polygons())
	if panel != null and panel.visible:
		_append_rect(all_points, panel.get_global_rect(), 14.0)
	if memory_panel != null and memory_panel.visible:
		_append_rect(all_points, memory_panel.get_global_rect(), 14.0)
	if confirm_panel != null and confirm_panel.visible:
		_append_rect(all_points, confirm_panel.get_global_rect(), 14.0)
	if poem_panel != null and poem_panel.visible:
		_append_rect(all_points, poem_panel.get_global_rect(), 14.0)
	if debug_hud != null and debug_hud.visible:
		_append_rect(all_points, debug_hud.get_global_rect())

	if all_points.size() < 3:
		_window.mouse_passthrough_polygon = PackedVector2Array()
		_window.mouse_passthrough = true
		return

	var hull := Geometry2D.convex_hull(all_points)
	if hull.size() < 3:
		_window.mouse_passthrough_polygon = PackedVector2Array()
		_window.mouse_passthrough = true
		return

	var expanded := Geometry2D.offset_polygon(hull, PASSTHROUGH_MARGIN)
	var canvas_poly := hull
	if not expanded.is_empty():
		canvas_poly = _largest_polygon(expanded)

	if debug_line != null:
		debug_line.points = canvas_poly
		print("[momiao] canvas_poly min/max=", _bounds(canvas_poly), " window=", _window.size,
			" vp=", get_viewport().get_visible_rect().size,
			" csf=", _window.content_scale_factor)

	var window_points := PackedVector2Array()
	for p in _decimate(canvas_poly, MAX_POLYGON_POINTS):
		window_points.append(ui_to_window(p))
	_window.mouse_passthrough_polygon = window_points
	_window.mouse_passthrough = false


func ui_to_window(p: Vector2) -> Vector2:
	var vp := get_viewport()
	var xf := vp.get_final_transform()
	var mapped := xf * p
	var vp_size := vp.get_visible_rect().size
	var win_size := Vector2(_window.size)
	if vp_size.x > 0.0 and vp_size.y > 0.0 and xf.is_equal_approx(Transform2D.IDENTITY) and vp_size != win_size:
		mapped = p * (win_size / vp_size)
	return mapped


func _append_points(target: PackedVector2Array, polys: Array[PackedVector2Array]) -> void:
	for poly in polys:
		for p in poly:
			target.append(p)


## grow_bottom：主题阴影超出面板矩形的余量，不扩会被穿透多边形裁掉。
func _append_rect(target: PackedVector2Array, rect: Rect2, grow_bottom: float = 0.0) -> void:
	if grow_bottom > 0.0:
		rect = rect.grow_individual(2, 2, 2, grow_bottom)
	target.append(rect.position)
	target.append(Vector2(rect.end.x, rect.position.y))
	target.append(rect.end)
	target.append(Vector2(rect.position.x, rect.end.y))


func _bounds(poly: PackedVector2Array) -> String:
	if poly.is_empty():
		return "(empty)"
	var min_p := poly[0]
	var max_p := poly[0]
	for p in poly:
		min_p = Vector2(minf(min_p.x, p.x), minf(min_p.y, p.y))
		max_p = Vector2(maxf(max_p.x, p.x), maxf(max_p.y, p.y))
	return "%s..%s" % [min_p, max_p]


func _largest_polygon(polys: Array[PackedVector2Array]) -> PackedVector2Array:
	var best := PackedVector2Array()
	for poly in polys:
		if poly.size() > best.size():
			best = poly
	return best


func _decimate(poly: PackedVector2Array, max_points: int) -> PackedVector2Array:
	if poly.size() <= max_points:
		return poly
	var step := float(poly.size()) / float(max_points)
	var out := PackedVector2Array()
	var i := 0.0
	while int(i) < poly.size():
		out.append(poly[int(i)])
		i += step
	return out


func _restore_window() -> void:
	var saved: Variant = ConfigStore.load_value("window", "position", null)
	var pos := ScreenUtils.default_window_pos(_window.size)
	if saved is Vector2i:
		pos = ScreenUtils.clamp_window_pos(saved, _window.size)
	elif saved is Vector2:
		pos = ScreenUtils.clamp_window_pos(Vector2i(saved), _window.size)
	_window.position = pos
	var always := bool(ConfigStore.load_value("window", "always_on_top", true))
	_window.always_on_top = always


func _on_close_requested() -> void:
	set_pet_visible(false)
