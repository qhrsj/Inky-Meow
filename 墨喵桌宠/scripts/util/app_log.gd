class_name AppLog
extends RefCounted

## 轻量文件日志：user://logs/client-YYYY-MM-DD.log，每行即写即落盘。
## 用途：命令输入/结果/耗时、番茄钟、诗笺、语音等事件留痕，出问题可翻日志复现。
## 用法：const Log := preload("res://scripts/util/app_log.gd"); Log.info("...")

const DIR := "user://logs"

static var _date := ""


static func info(msg: String) -> void:
	_write("INFO", msg)


static func warn(msg: String) -> void:
	_write("WARN", msg)


static func error(msg: String) -> void:
	_write("ERROR", msg)


static func log_path() -> String:
	return "%s/client-%s.log" % [DIR, Time.get_date_string_from_system()]


static func _write(level: String, msg: String) -> void:
	var line := "%s [%s] %s" % [Time.get_time_string_from_system(), level, msg]
	print(line)                       # 控制台同步可见
	var path := log_path()
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(DIR))
	var f := FileAccess.open(path, FileAccess.READ_WRITE if FileAccess.file_exists(path) else FileAccess.WRITE)
	if f == null:
		return
	f.seek_end()
	f.store_line(line)
	f.flush()
