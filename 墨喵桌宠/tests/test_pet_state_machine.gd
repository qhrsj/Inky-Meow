@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const SM_SCRIPT := preload("res://scripts/pet_state_machine.gd")


func suite_name() -> String:
	return "pet_state_machine"


func _new_sm():
	var sm = SM_SCRIPT.new()
	sm.idle_to_sleepy = 300.0
	sm.sleepy_to_easter = 600.0
	sm.happy_hold = 2.2
	sm.confused_hold = 3.0
	sm.easter_hold = 8.0
	return sm


func test_initial_state_is_idle() -> void:
	var sm = _new_sm()
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)
	assert_eq(sm.state_name(), "idle")


func test_idle_become_sleepy_after_300s() -> void:
	var sm = _new_sm()
	sm.debug_advance(299.9)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE, "299.9s 还不该睡")
	sm.debug_advance(0.2)
	assert_eq(sm.get_state(), SM_SCRIPT.State.SLEEPY, "300s 后应困倦")


func test_sleepy_become_easter_after_600_more() -> void:
	var sm = _new_sm()
	sm.debug_advance(301.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.SLEEPY)
	sm.debug_advance(600.1)
	assert_eq(sm.get_state(), SM_SCRIPT.State.EASTER, "累计 15 分钟应追蝶")


func test_notify_input_wakes_from_sleepy_to_idle() -> void:
	var sm = _new_sm()
	sm.debug_advance(301.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.SLEEPY)
	var woke := [false]
	sm.wake_up.connect(func(): woke[0] = true)
	sm.notify_user_input()
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)
	assert_true(woke[0], "应发出 wake_up")


func test_click_from_sleepy_wakes_to_happy_then_idle() -> void:
	var sm = _new_sm()
	sm.debug_advance(301.0)
	sm.pet_click()
	assert_eq(sm.get_state(), SM_SCRIPT.State.HAPPY)
	sm.debug_advance(2.3)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)


func test_request_success_to_happy_then_idle() -> void:
	var sm = _new_sm()
	var id: int = sm.begin_request("couplet")
	assert_gt(id, 0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.THINKING)
	assert_true(sm.finish_request(id, true), "token 有效应接受")
	assert_eq(sm.get_state(), SM_SCRIPT.State.HAPPY)
	sm.debug_advance(2.3)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)


func test_request_failure_to_confused_then_idle() -> void:
	var sm = _new_sm()
	var id: int = sm.begin_request("check")
	assert_true(sm.finish_request(id, false, "model_error"))
	assert_eq(sm.get_state(), SM_SCRIPT.State.CONFUSED)
	sm.debug_advance(3.1)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)


func test_request_busy_while_thinking() -> void:
	var sm = _new_sm()
	var id: int = sm.begin_request("theme")
	assert_gt(id, 0)
	assert_eq(sm.begin_request("couplet"), -1, "思考中应拒绝第二个请求")
	sm.finish_request(id, true)


func test_timeout_to_confused_and_late_result_discarded() -> void:
	var sm = _new_sm()
	var id: int = sm.begin_request("check")  # 15s watchdog
	sm.debug_advance(15.1)
	assert_eq(sm.get_state(), SM_SCRIPT.State.CONFUSED, "看门狗应超时")
	assert_false(sm.finish_request(id, true), "迟到结果应被丢弃")
	assert_eq(sm.get_state(), SM_SCRIPT.State.CONFUSED, "迟到结果不得改回 happy")


func test_ui_active_pauses_idle() -> void:
	var sm = _new_sm()
	sm.set_ui_active(true)
	sm.debug_advance(400.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE, "面板打开时不应困倦")
	sm.set_ui_active(false)
	sm.debug_advance(301.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.SLEEPY)


func test_drag_pauses_idle() -> void:
	var sm = _new_sm()
	sm.set_dragging(true)
	assert_eq(sm.get_state(), SM_SCRIPT.State.DRAGGING)
	sm.debug_advance(400.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.DRAGGING, "拖动中不推进闲置计时")
	sm.set_dragging(false)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)


func test_drag_keeps_thinking_after_release() -> void:
	var sm = _new_sm()
	var id: int = sm.begin_request("write")
	sm.set_dragging(true)
	assert_eq(sm.get_state(), SM_SCRIPT.State.DRAGGING)
	sm.set_dragging(false)
	assert_eq(sm.get_state(), SM_SCRIPT.State.THINKING, "请求还在飞应回到 thinking")
	sm.finish_request(id, true)
	assert_eq(sm.get_state(), SM_SCRIPT.State.HAPPY)


func test_deferred_result_applied_after_drag() -> void:
	var sm = _new_sm()
	var id: int = sm.begin_request("theme")
	sm.set_dragging(true)
	sm.finish_request(id, true)
	assert_eq(sm.get_state(), SM_SCRIPT.State.DRAGGING, "拖动中结果应延后")
	sm.set_dragging(false)
	assert_eq(sm.get_state(), SM_SCRIPT.State.HAPPY, "松手后应用延后的结果")


func test_easter_returns_to_idle_after_hold() -> void:
	var sm = _new_sm()
	sm.debug_advance(301.0)
	sm.debug_advance(600.1)
	assert_eq(sm.get_state(), SM_SCRIPT.State.EASTER)
	sm.debug_advance(8.1)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)


func test_hidden_pauses_and_show_resets() -> void:
	var sm = _new_sm()
	sm.debug_advance(200.0)
	sm.set_hidden(true)
	sm.debug_advance(400.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE, "隐藏时暂停计时")
	sm.set_hidden(false)
	assert_eq(sm.get_state(), SM_SCRIPT.State.IDLE)
	sm.debug_advance(301.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.SLEEPY)


func test_chat_timeout_is_60s() -> void:
	var sm = _new_sm()
	var id: int = sm.begin_request("chat")
	sm.debug_advance(59.0)
	assert_eq(sm.get_state(), SM_SCRIPT.State.THINKING, "59s 还不该超时")
	sm.debug_advance(1.2)
	assert_eq(sm.get_state(), SM_SCRIPT.State.CONFUSED, "chat 60s 看门狗应触发")
	assert_false(sm.finish_request(id, true), "迟到结果应被丢弃")
