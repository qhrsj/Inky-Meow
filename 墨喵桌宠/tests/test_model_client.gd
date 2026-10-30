@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const MC_SCRIPT := preload("res://scripts/services/model_client.gd")


func suite_name() -> String:
	return "model_client"


func test_invalid_theme_empty() -> void:
	var r := MC_SCRIPT.validate_payload("theme", {"text": ""})
	assert_false(r.ok, "空关键词应无效")
	assert_eq(r.code, "invalid_input")


func test_invalid_theme_too_long() -> void:
	var r := MC_SCRIPT.validate_payload("theme", {"text": "五个字的词啊"})
	assert_false(r.ok, "超过 4 字应无效")


func test_theme_valid_alias() -> void:
	assert_true(MC_SCRIPT.validate_payload("theme", {"text": "孤舟"}).ok)
	assert_true(MC_SCRIPT.validate_payload("theme", {"word": "孤舟"}).ok)


func test_chat_requires_text() -> void:
	assert_false(MC_SCRIPT.validate_payload("chat", {"text": "  "}).ok)
	assert_true(MC_SCRIPT.validate_payload("chat", {"text": "你好呀"}).ok)


func test_couplet_lengths() -> void:
	assert_true(MC_SCRIPT.validate_payload("couplet", {"upper": "空山新雨后"}).ok, "五言应有效")
	assert_true(MC_SCRIPT.validate_payload("couplet", {"upper": "两个黄鹂鸣翠柳"}).ok, "七言应有效")
	assert_false(MC_SCRIPT.validate_payload("couplet", {"upper": "六字上句啊啊"}).ok, "六字应无效")


func test_couplet_normalizes_punctuation() -> void:
	assert_eq(MC_SCRIPT.normalize_verse("空山新雨后，"), "空山新雨后")
	assert_eq(MC_SCRIPT.normalize_verse(" 空山 新雨后。"), "空山新雨后")


func test_check_and_write_require_text() -> void:
	assert_false(MC_SCRIPT.validate_payload("check", {"poem": "  "}).ok)
	assert_false(MC_SCRIPT.validate_payload("write", {"opening": ""}).ok)
	assert_true(MC_SCRIPT.validate_payload("check", {"poem": "床前明月光"}).ok)
	assert_true(MC_SCRIPT.validate_payload("write", {"opening": "空山新雨后，"}).ok)
	assert_true(MC_SCRIPT.validate_payload("write", {"prompt": "空山新雨后，"}).ok)


func test_unknown_command() -> void:
	assert_false(MC_SCRIPT.validate_payload("nope", {}).ok)


func test_mock_theme_shape() -> void:
	var d: Dictionary = MC_SCRIPT.build_mock_data("theme", {"word": "孤舟"})
	assert_has_key(d, "poem")
	assert_has_key(d, "form")
	assert_has_key(d, "keyword_at")
	assert_has_key(d, "note")
	assert_contains(d.poem, "孤舟")


func test_mock_couplet_shape() -> void:
	var d: Dictionary = MC_SCRIPT.build_mock_data("couplet", {"upper": "空山新雨后"})
	assert_has_key(d, "items")
	assert_gt(d.items.size(), 0)
	assert_has_key(d.items[0], "text")
	assert_has_key(d.items[0], "score")
	assert_has_key(d.items[0], "义类")
	assert_has_key(d, "note")


func test_mock_check_shape() -> void:
	var d: Dictionary = MC_SCRIPT.build_mock_data("check", {"text": "床前明月光"})
	assert_has_key(d, "jushi_ok")
	assert_has_key(d, "pingze_ok")
	assert_has_key(d, "yayun_ok")
	assert_has_key(d, "pingze_bad")
	assert_has_key(d, "rhymes")


func test_mock_write_shape() -> void:
	var d: Dictionary = MC_SCRIPT.build_mock_data("write", {"prompt": "空山新雨后，"})
	assert_has_key(d, "poem")
	assert_has_key(d, "note")


func test_mock_chat_shape() -> void:
	var d: Dictionary = MC_SCRIPT.build_mock_data("chat", {"text": "你好"})
	assert_has_key(d, "reply")
	assert_has_key(d, "tool")
	assert_has_key(d, "poem")
	assert_has_key(d, "pet_name")


func test_mock_ping() -> void:
	var d: Dictionary = MC_SCRIPT.build_mock_data("ping", {})
	assert_eq(d.ok, true)
	assert_eq(d.mock, true)


func _mock_client() -> ModelClient:
	var mc: ModelClient = MC_SCRIPT.new()
	mc.set_transport(MC_SCRIPT.TRANSPORT_MOCK)
	return mc


## asr_start/asr_stop 里带 await（HTTP 分支），静态看是协程；
## 测试框架是同步跑用例的，所以走 Object.call() 绕开静态检查——
## mock 分支在任何 await 之前就返回了，实际仍是同步完成。
func _asr(mc: ModelClient, method: String) -> Dictionary:
	var r: Variant = mc.call(method)
	return r if r is Dictionary else {}


func test_asr_start_stop_lifecycle() -> void:
	var mc := _mock_client()
	assert_false(mc.is_asr_recording(), "初始不录音")
	var started := _asr(mc, "asr_start")
	assert_true(started.get("ok", false), "mock 下开始录音应成功")
	assert_true(mc.is_asr_recording(), "开始后应处于录音态")
	var stopped := _asr(mc, "asr_stop")
	assert_true(stopped.get("ok", false), "停止应成功")
	assert_false(mc.is_asr_recording(), "停止后应退出录音态")
	assert_has_key(stopped.get("data", {}), "text")
	mc.free()


func test_asr_start_twice_is_busy() -> void:
	var mc := _mock_client()
	_asr(mc, "asr_start")
	var dup := _asr(mc, "asr_start")
	assert_false(dup.get("ok", false), "重复开始应被拒")
	assert_eq(dup.get("error", {}).get("code", ""), "busy")
	_asr(mc, "asr_stop")
	mc.free()


func test_asr_stop_without_start() -> void:
	var mc := _mock_client()
	var r := _asr(mc, "asr_stop")
	assert_false(r.get("ok", false), "没开始就停止应报错")
	assert_eq(r.get("error", {}).get("code", ""), "invalid_input")
	mc.free()
