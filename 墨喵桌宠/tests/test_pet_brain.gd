@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const PB_SCRIPT := preload("res://scripts/pet_brain.gd")


func suite_name() -> String:
	return "pet_brain"


func test_theme_format() -> void:
	var r: Dictionary = PB_SCRIPT.format_result("theme", {
		"poem": "我今南北游，孤舟望长安。",
		"form": "五言",
		"keyword_at": "第2句第1字",
		"note": null,
	}, "孤舟")
	assert_contains(str(r.title), "点题")
	assert_contains(str(r.title), "五言")
	assert_contains(str(r.body), "孤舟望长安")
	assert_contains(str(r.body), "第2句第1字")
	assert_contains(str(r.speech), "孤舟望长安")


func test_couplet_format() -> void:
	var r: Dictionary = PB_SCRIPT.format_result("couplet", {
		"items": [
			{"text": "空庭月正明", "score": 0.91, "义类": 1.0, "虚实": 1.0, "字级": 0.8},
			{"text": "闲阶月正明", "score": 0.88, "义类": 1.0, "虚实": 1.0, "字级": 0.4},
		],
		"note": null,
	}, "小院灯初上")
	assert_contains(str(r.body), "小院灯初上")
	assert_contains(str(r.body), "空庭月正明")
	assert_contains(str(r.body), "0.91")
	assert_contains(str(r.speech), "空庭月正明")


func test_check_format() -> void:
	var r: Dictionary = PB_SCRIPT.format_result("check", {
		"form": "五言",
		"couplets": 1,
		"jushi_ok": true,
		"jushi_bad": [],
		"pingze_ok": false,
		"pingze_bad": ["第2句第4字应平实仄"],
		"yayun_ok": true,
		"rhymes": ["霜"],
		"yayun_bad": [],
	}, "床前明月光，疑是地上霜。")
	assert_contains(str(r.title), "体检")
	assert_contains(str(r.body), "平仄")
	assert_contains(str(r.body), "霜")
	assert_eq(str(r.speech), "")


func test_write_format() -> void:
	var r: Dictionary = PB_SCRIPT.format_result("write", {
		"poem": "小院灯初上，虚堂漏未残。",
		"note": null,
	}, "小院灯初上，")
	assert_contains(str(r.body), "虚堂漏未残")
	assert_contains(str(r.speech), "虚堂漏未残")


func test_chat_format() -> void:
	var r: Dictionary = PB_SCRIPT.format_result("chat", {
		"reply": "在的，一直在砚台边。",
		"tool": null,
		"poem": null,
		"note": null,
	}, "你好")
	assert_contains(str(r.body), "砚台边")
	assert_contains(str(r.speech), "砚台边")


func test_chat_format_poem() -> void:
	var r: Dictionary = PB_SCRIPT.format_result("chat", {
		"reply": "容吾研墨……",
		"tool": "theme",
		"poem": "我今南北游，孤舟望长安。",
		"note": null,
	}, "写首孤舟")
	assert_contains(str(r.body), "我今南北游")
	assert_contains(str(r.speech), "我今南北游")


func test_error_text_business_passthrough() -> void:
	var msg := "「空山新雨后」是古人的成句。"
	assert_eq(PB_SCRIPT.error_text("business", msg), msg)


func test_error_text_offline() -> void:
	assert_contains(PB_SCRIPT.error_text("sidecar_offline", ""), "模型")
