@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const POMODORO_SCRIPT := preload("res://scripts/services/pomodoro.gd")
const SM_SCRIPT := preload("res://scripts/pet_state_machine.gd")


func suite_name() -> String:
	return "pomodoro"


func _new_pomodoro(focus_min: float = 25.0, break_min: float = 5.0,
		long_break_min: float = 15.0, long_every: int = 4):
	var p = POMODORO_SCRIPT.new()
	p.focus_minutes = focus_min
	p.break_minutes = break_min
	p.long_break_minutes = long_break_min
	p.long_every = long_every
	return p


func _phases(p) -> Array:
	var out: Array = []
	p.phase_changed.connect(func(new_phase: int, _old: int): out.append(new_phase))
	return out


func test_initial_state_is_off() -> void:
	var p = _new_pomodoro()
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.OFF)
	assert_false(p.is_running())
	assert_eq(p.phase_name(), "off")


func test_start_enters_focus() -> void:
	var p = _new_pomodoro()
	var events := _phases(p)
	p.start()
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.FOCUS)
	assert_true(p.is_focusing())
	assert_eq(events, [POMODORO_SCRIPT.Phase.FOCUS])


func test_focus_finishes_to_break() -> void:
	var p = _new_pomodoro(1.0 / 60.0, 5.0)   # 1 秒专注，便于测试
	p.start()
	p.debug_advance(60.1)
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.BREAK)
	assert_eq(p.completed_focus, 1)


func test_break_finishes_back_to_focus() -> void:
	# 专注/短休/长休都是 1s，避免触发长休干扰
	var p = _new_pomodoro(1.0 / 60.0, 1.0 / 60.0, 1.0 / 60.0)
	p.start()
	p.debug_advance(1.05)    # 专注结束 → 短休
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.BREAK)
	assert_eq(p.completed_focus, 1)
	p.debug_advance(1.05)    # 短休结束 → 下一轮专注
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.FOCUS)
	assert_eq(p.completed_focus, 1)


func test_long_break_after_four_focus() -> void:
	# 专注/短休 1s，长休 2s：4 轮共 7s，第 7s 进长休，第 9s 回专注
	var p = _new_pomodoro(1.0 / 60.0, 1.0 / 60.0, 2.0 / 60.0, 4)
	p.start()
	p.debug_advance(7.05)
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.LONG_BREAK)
	assert_eq(p.completed_focus, 4)
	p.debug_advance(2.05)    # 长休结束 → 第 5 轮专注
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.FOCUS)
	assert_eq(p.completed_focus, 4)


func test_stop_resets_to_off() -> void:
	var p = _new_pomodoro()
	p.start()
	p.debug_advance(100.0)
	p.stop()
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.OFF)
	assert_false(p.is_running())
	assert_eq(p.get_phase_left(), 0.0)


func test_toggle_round_trip() -> void:
	var p = _new_pomodoro()
	assert_true(p.toggle(), "关→开应返回 true")
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.FOCUS)
	assert_false(p.toggle(), "开→关应返回 false")
	assert_eq(p.phase, POMODORO_SCRIPT.Phase.OFF)


func test_phase_left_counts_down() -> void:
	var p = _new_pomodoro()
	p.start()
	assert_eq(p.get_phase_left(), 1500.0, "25 分钟 = 1500s")
	p.debug_advance(30.0)
	assert_true(absf(p.get_phase_left() - 1470.0) < 0.5, "30s 后剩 1470s")


func test_quiet_mode_blocks_sleepy() -> void:
	var sm = SM_SCRIPT.new()
	sm.set_quiet_mode(true)
	sm.debug_advance(400.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE, "专注中 400s 不该困")


func test_quiet_mode_wakes_sleepy_quietly() -> void:
	var sm = SM_SCRIPT.new()
	sm.debug_advance(301.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.SLEEPY)
	var woke := [false]
	sm.wake_up.connect(func(): woke[0] = true)
	sm.set_quiet_mode(true)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE, "进入专注应安静唤醒")
	assert_false(woke[0], "安静唤醒不发 wake_up")
	sm.debug_advance(400.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)


func test_unquiet_resumes_sleepy_flow() -> void:
	var sm = SM_SCRIPT.new()
	sm.set_quiet_mode(true)
	sm.debug_advance(100.0)
	sm.set_quiet_mode(false)
	sm.debug_advance(301.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.SLEEPY, "退出专注后恢复困倦计时")
