class_name Pomodoro
extends RefCounted

## 专注陪伴（番茄钟）。纯逻辑、不碰节点树；由 main.gd 每帧调用 tick(delta)。
## 节奏：专注 focus_minutes → 短休 break_minutes；每完成 long_every 个专注进长休。
## 时间全部用累积 dt，方便测试时 debug_advance()。

enum Phase { OFF, FOCUS, BREAK, LONG_BREAK }

signal phase_changed(new_phase: int, old_phase: int)

const DEFAULT_FOCUS_MINUTES := 25.0
const DEFAULT_BREAK_MINUTES := 5.0
const DEFAULT_LONG_BREAK_MINUTES := 15.0
const DEFAULT_LONG_EVERY := 4

var focus_minutes := DEFAULT_FOCUS_MINUTES
var break_minutes := DEFAULT_BREAK_MINUTES
var long_break_minutes := DEFAULT_LONG_BREAK_MINUTES
var long_every := DEFAULT_LONG_EVERY

var phase: int = Phase.OFF
var completed_focus := 0

var _phase_left := 0.0


func start() -> void:
	_enter(Phase.FOCUS)


func stop() -> void:
	_enter(Phase.OFF)


func toggle() -> bool:
	if phase == Phase.OFF:
		start()
		return true
	stop()
	return false


func tick(delta: float) -> void:
	if delta <= 0.0 or phase == Phase.OFF:
		return
	_phase_left -= delta
	if _phase_left <= 0.0:
		_advance()


func is_running() -> bool:
	return phase != Phase.OFF


func is_focusing() -> bool:
	return phase == Phase.FOCUS


func get_phase_left() -> float:
	return maxf(_phase_left, 0.0)


func phase_name(value: int = -1) -> String:
	var p := phase if value < 0 else value
	match p:
		Phase.OFF: return "off"
		Phase.FOCUS: return "focus"
		Phase.BREAK: return "break"
		Phase.LONG_BREAK: return "long_break"
	return "unknown"


## 测试/验收用：按 0.05s 步进推进时间。
func debug_advance(seconds: float) -> void:
	var remaining := maxf(seconds, 0.0)
	var guard := 0
	while remaining > 0.0 and guard < 100000:
		var step := minf(0.05, remaining)
		tick(step)
		remaining -= step
		guard += 1


func _advance() -> void:
	match phase:
		Phase.FOCUS:
			completed_focus += 1
			if long_every > 0 and completed_focus % long_every == 0:
				_enter(Phase.LONG_BREAK)
			else:
				_enter(Phase.BREAK)
		Phase.BREAK, Phase.LONG_BREAK:
			_enter(Phase.FOCUS)


func _enter(new_phase: int) -> void:
	var old := phase
	phase = new_phase
	match new_phase:
		Phase.FOCUS:
			_phase_left = focus_minutes * 60.0
		Phase.BREAK:
			_phase_left = break_minutes * 60.0
		Phase.LONG_BREAK:
			_phase_left = long_break_minutes * 60.0
		Phase.OFF:
			_phase_left = 0.0
	if new_phase != old:
		phase_changed.emit(new_phase, old)
