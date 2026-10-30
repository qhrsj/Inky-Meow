extends SceneTree

## 无编辑器跑测试：发现 res://tests/test_*.gd，交给 godot-ai 自带的 McpTestRunner。
## 用法：
##   Godot_v4.7.2-stable_win64.exe --headless --path <项目目录> \
##       --script res://tools/run_tests_headless.gd
## 退出码 0 = 全绿，1 = 有失败，2 = 没有找到测试。

const TESTS_DIR := "res://tests/"


func _initialize() -> void:
	var suites: Array = []
	var errors: Array[String] = []
	var dir := DirAccess.open(TESTS_DIR)
	if dir == null:
		printerr("打不开 %s" % TESTS_DIR)
		quit(2)
		return

	dir.list_dir_begin()
	var file_name := dir.get_next()
	while not file_name.is_empty():
		if file_name.begins_with("test_") and file_name.ends_with(".gd"):
			var script: Script = ResourceLoader.load(
				TESTS_DIR + file_name, "", ResourceLoader.CACHE_MODE_IGNORE)
			if script == null or not script.can_instantiate():
				errors.append("%s：加载失败（多半是语法错误）" % file_name)
			else:
				var instance: Variant = script.new()
				if instance is McpTestSuite:
					suites.append(instance)
				else:
					errors.append("%s：不是 McpTestSuite 子类" % file_name)
		file_name = dir.get_next()
	dir.list_dir_end()

	suites.sort_custom(func(a, b) -> bool: return a.suite_name() < b.suite_name())

	var runner := McpTestRunner.new()
	for suite in suites:
		runner.run_suite(suite)

	var summary: Dictionary = runner.get_results(true)
	var failed: Array = []
	for r in summary.get("results", []):
		if not bool(r.get("passed", false)) and not bool(r.get("skipped", false)):
			failed.append(r)
			printerr("FAIL %s.%s — %s" % [
				str(r.get("suite", "")), str(r.get("test", "")),
				str(r.get("message", ""))])

	for e in errors:
		printerr("LOAD %s" % e)

	print("==== %d 项，失败 %d，加载错误 %d ====" % [
		int(summary.get("total", 0)), failed.size(), errors.size()])
	for suite in suites:
		print("  · %s" % suite.suite_name())

	if failed.is_empty() and errors.is_empty():
		quit(0)
	else:
		quit(1)
