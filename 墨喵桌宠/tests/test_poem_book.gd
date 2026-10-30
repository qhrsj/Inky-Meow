@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const BOOK_SCRIPT := preload("res://scripts/services/poem_book.gd")

const TMP_PATH := "user://test_poems_book.json"


func suite_name() -> String:
	return "poem_book"


## headless 入口的 run_suite 不调用 suite_setup/teardown，且用例按字母序执行，
## 所以每个用例通过 _new_book() 自清理，保证互不影响。
func _new_book() -> Object:
	DirAccess.remove_absolute(ProjectSettings.globalize_path(TMP_PATH))
	var b = BOOK_SCRIPT.new()
	b.path = TMP_PATH
	b.load_book()
	return b


func test_empty_book_starts_zero() -> void:
	var b = _new_book()
	b.load_book()
	assert_eq(b.count(), 0)


func test_add_and_count() -> void:
	var b = _new_book()
	assert_true(b.add("点题·五绝", "春风又绿江南岸"))
	assert_eq(b.count(), 1)
	assert_eq(str(b.entries[0]["title"]), "点题·五绝")
	assert_eq(str(b.entries[0]["body"]), "春风又绿江南岸")
	assert_false(str(b.entries[0]["saved_at"]).is_empty(), "应记录收藏时间")


func test_add_rejects_empty_body() -> void:
	var b = _new_book()
	assert_false(b.add("无题", "   "))
	assert_eq(b.count(), 0)


func test_add_dedupes_same_title_and_body() -> void:
	var b = _new_book()
	b.add("点题", "床前明月光")
	assert_false(b.add("点题", "床前明月光"), "同题同文应去重")
	assert_eq(b.count(), 1)
	assert_true(b.add("点题", "疑是地上霜"), "不同文可收")


func test_newest_first() -> void:
	var b = _new_book()
	b.add("第一首", "一")
	b.add("第二首", "二")
	assert_eq(str(b.entries[0]["title"]), "第二首", "新收藏在最前")


func test_remove_at() -> void:
	var b = _new_book()
	b.add("甲", "一")
	b.add("乙", "二")
	assert_true(b.remove_at(0))
	assert_eq(b.count(), 1)
	assert_eq(str(b.entries[0]["title"]), "甲")
	assert_false(b.remove_at(5), "越界删除应失败")


func test_persistence_roundtrip() -> void:
	var b = _new_book()
	b.add("点题·七绝", "两个黄鹂鸣翠柳")
	var b2 = BOOK_SCRIPT.new()
	b2.path = TMP_PATH
	b2.load_book()
	assert_eq(b2.count(), 1)
	assert_eq(str(b2.entries[0]["body"]), "两个黄鹂鸣翠柳")
	assert_eq(str(b2.entries[0]["title"]), "点题·七绝")


func test_load_missing_file_is_empty() -> void:
	var b = _new_book()
	b.path = "user://test_poems_no_such_file.json"
	b.load_book()
	assert_eq(b.count(), 0)
