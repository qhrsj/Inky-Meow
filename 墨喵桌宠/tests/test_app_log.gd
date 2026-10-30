@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const LOG := preload("res://scripts/util/app_log.gd")

const MARK := "test_log_marker"


func suite_name() -> String:
	return "app_log"


func test_write_and_read_back() -> void:
	LOG.info("%s hello=%d" % [MARK, 42])
	var path := LOG.log_path()
	assert_true(FileAccess.file_exists(path), "日志文件应存在")
	var raw := FileAccess.get_file_as_string(path)
	assert_contains(raw, MARK)
	assert_contains(raw, "hello=42")
	assert_contains(raw, "[INFO]")


func test_levels_and_multiline_path() -> void:
	LOG.warn("%s w" % MARK)
	LOG.error("%s e" % MARK)
	var raw := FileAccess.get_file_as_string(LOG.log_path())
	assert_contains(raw, "[WARN]")
	assert_contains(raw, "[ERROR]")
	assert_true(LOG.log_path().begins_with("user://logs/client-"), "路径格式")
