@tool
class_name PetStateMachine
extends RefCounted

## 墨喵状态机。纯逻辑、不碰节点树；由 main.gd 每帧调用 tick(delta)。
## 时间相关逻辑全部用累积 dt，方便测试时 debug_advance()。

enum State { IDLE, THINKING, HAPPY, CONFUSED, SLEEPY, EASTER, DRAGGING }

signal state_changed(new_state: int, old_state: int)
signal request_started(id: int, cmd: String)
signal request_finished(id: int, cmd: String, ok: bool, reason: String)
signal request_timed_out(id: int, cmd: String)
signal wake_up()
signal sleep_started()
signal easter_started()
signal drag_state_changed(dragging: bool)

const CMD_TIMEOUT := {
	"theme": 60.0,
	"couplet": 30.0,
	"check": 15.0,
	"write": 30.0,
	"chat": 60.0,
	"ping": 5.0,
}

const DEFAULT_TIMEOUT := 30.0

var idle_to_sleepy := 300.0
var sleepy_to_easter := 600.0
var happy_hold := 2.2
var confused_hold := 3.0
var easter_hold := 8.0

var state: int = State.IDLE

var _idle_elapsed := 0.0
var _sleepy_elapsed := 0.0
var _transient_left := 0.0
var _easter_left := 0.0
var _watchdog_elapsed := 0.0
var _watchdog_limit := DEFAULT_TIMEOUT

var _request_active := false
var _request_id := 0
var _next_request_id := 1
var _request_cmd := ""

var _deferred_state := -1
var _deferred_reason := ""

var _ui_active := false
var _hidden := false
var _dragging := false
var _state_before_drag: int = State.IDLE


func is_busy() -> bool:
	return _request_active

## 专注陪伴模式：IDLE 时不累积困倦，保持安静读书。
var quiet_mode := false


func tick(delta: float) -> void:
	if delta <= 0.0:
		return
	match state:
		State.IDLE:
			if _ui_active or _hidden or quiet_mode:
				return
			_idle_elapsed += delta
			if _idle_elapsed >= idle_to_sleepy:
				_change(State.SLEEPY)
		State.THINKING:
			if _request_active:
				_watchdog_elapsed += delta
				if _watchdog_elapsed >= _watchdog_limit:
					_expire_request("timeout")
		State.HAPPY, State.CONFUSED:
			if _hidden:
				return
			_transient_left -= delta
			if _transient_left <= 0.0:
				_change(State.IDLE)
		State.SLEEPY:
			if _ui_active or _hidden:
				return
			_sleepy_elapsed += delta
			if _sleepy_elapsed >= sleepy_to_easter:
				_change(State.EASTER)
		State.EASTER:
			if _hidden:
				return
			_easter_left -= delta
			if _easter_left <= 0.0:
				_change(State.IDLE)
		State.DRAGGING:
			if _request_active:
				_watchdog_elapsed += delta
				if _watchdog_elapsed >= _watchdog_limit:
					_expire_request("timeout")


## 任意输入（按键、面板操作等）。睡着时唤醒；不改变功能状态。
func notify_user_input() -> void:
	if _hidden:
		return
	var was_sleeping := state == State.SLEEPY or state == State.EASTER
	_idle_elapsed = 0.0
	_sleepy_elapsed = 0.0
	if was_sleeping:
		_change(State.IDLE)
		wake_up.emit()


## 点击角色：睡着时唤醒并给一个开心反应；思考/拖动中忽略。
func pet_click() -> void:
	if _hidden or state == State.DRAGGING or state == State.THINKING:
		return
	var was_sleeping := state == State.SLEEPY or state == State.EASTER
	_idle_elapsed = 0.0
	_sleepy_elapsed = 0.0
	_change(State.HAPPY)
	if was_sleeping:
		wake_up.emit()


func begin_request(cmd: String) -> int:
	if _request_active or state == State.THINKING or state == State.DRAGGING:
		return -1
	_request_active = true
	_request_id = _next_request_id
	_next_request_id += 1
	_request_cmd = cmd
	_watchdog_limit = float(CMD_TIMEOUT.get(cmd, DEFAULT_TIMEOUT))
	_watchdog_elapsed = 0.0
	_deferred_state = -1
	_deferred_reason = ""
	_idle_elapsed = 0.0
	_change(State.THINKING)
	request_started.emit(_request_id, cmd)
	return _request_id


func finish_request(id: int, ok: bool, reason: String = "") -> bool:
	if not _request_active or id != _request_id:
		return false
	_request_active = false
	var cmd := _request_cmd
	var target := State.HAPPY if ok else State.CONFUSED
	request_finished.emit(id, cmd, ok, reason)
	if _dragging:
		_deferred_state = target
		_deferred_reason = reason
	else:
		_change(target)
	return true


func cancel_request(reason: String = "cancelled") -> void:
	if not _request_active:
		return
	var id := _request_id
	var cmd := _request_cmd
	_request_active = false
	request_finished.emit(id, cmd, false, reason)
	if _dragging:
		_deferred_state = State.CONFUSED
		_deferred_reason = reason
	else:
		_change(State.CONFUSED)


func set_dragging(dragging: bool) -> void:
	if dragging:
		if _dragging:
			return
		_dragging = true
		_state_before_drag = state
		_change(State.DRAGGING)
		drag_state_changed.emit(true)
		return
	if not _dragging:
		return
	_dragging = false
	drag_state_changed.emit(false)
	if _deferred_state >= 0:
		var target := _deferred_state
		_deferred_state = -1
		_deferred_reason = ""
		_change(target)
	elif _request_active:
		_change(State.THINKING)
	else:
		_change(State.IDLE)


func set_ui_active(active: bool) -> void:
	_ui_active = active
	if not active:
		_idle_elapsed = 0.0
		_sleepy_elapsed = 0.0


## 专注陪伴开关：进入时安静唤醒（不走 wake_up，不打扰）；退出时重置计时。
func set_quiet_mode(quiet: bool) -> void:
	quiet_mode = quiet
	if quiet:
		if state == State.SLEEPY or state == State.EASTER:
			_change(State.IDLE)
	else:
		_idle_elapsed = 0.0
		_sleepy_elapsed = 0.0


func set_hidden(hidden: bool) -> void:
	_hidden = hidden
	if not hidden:
		_idle_elapsed = 0.0
		_sleepy_elapsed = 0.0
		if _request_active:
			_change(State.THINKING)
		else:
			_change(State.IDLE)


func is_request_current(id: int) -> bool:
	return _request_active and id == _request_id


func is_request_active() -> bool:
	return _request_active


func get_state() -> int:
	return state


func is_dragging() -> bool:
	return _dragging


## 测试/验收用：按 0.05s 步进推进时间。
func debug_advance(seconds: float) -> void:
	var remaining := maxf(seconds, 0.0)
	var guard := 0
	while remaining > 0.0 and guard < 100000:
		var step := minf(0.05, remaining)
		tick(step)
		remaining -= step
		guard += 1


func state_name(value: int = -1) -> String:
	var s := state if value < 0 else value
	match s:
		State.IDLE: return "idle"
		State.THINKING: return "thinking"
		State.HAPPY: return "happy"
		State.CONFUSED: return "confused"
		State.SLEEPY: return "sleepy"
		State.EASTER: return "easter"
		State.DRAGGING: return "dragging"
	return "unknown"


func _change(new_state: int) -> void:
	var old := state
	state = new_state
	_reset_timer_for(new_state)
	match new_state:
		State.SLEEPY:
			sleep_started.emit()
		State.EASTER:
			easter_started.emit()
	state_changed.emit(new_state, old)


func _reset_timer_for(new_state: int) -> void:
	match new_state:
		State.IDLE:
			_idle_elapsed = 0.0
			_sleepy_elapsed = 0.0
		State.THINKING:
			_watchdog_elapsed = 0.0
		State.HAPPY:
			_transient_left = happy_hold
		State.CONFUSED:
			_transient_left = confused_hold
		State.SLEEPY:
			_sleepy_elapsed = 0.0
		State.EASTER:
			_easter_left = easter_hold


func _expire_request(reason: String) -> void:
	if not _request_active:
		return
	var id := _request_id
	var cmd := _request_cmd
	_request_active = false
	request_timed_out.emit(id, cmd)
	if _dragging:
		_deferred_state = State.CONFUSED
		_deferred_reason = reason
	else:
		_change(State.CONFUSED)
