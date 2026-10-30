@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const MANIFEST := "res://assets/sprites/manifest.json"


func suite_name() -> String:
	return "sprite_manifest"


func _manifest() -> Dictionary:
	if not FileAccess.file_exists(MANIFEST):
		return {}
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(MANIFEST))
	return parsed if parsed is Dictionary else {}


func test_manifest_exists_and_shape() -> void:
	var d := _manifest()
	assert_has_key(d, "cell")
	assert_has_key(d, "sheets")
	assert_has_key(d, "animations")
	assert_has_key(d, "state_map")
	assert_eq(int(d.get("cell", 0)), 512, "cell 应为 512")


func test_sheet_resources_exist() -> void:
	var d := _manifest()
	var sheets: Dictionary = d.get("sheets", {})
	assert_gt(sheets.size(), 0)
	for key in sheets.keys():
		assert_true(ResourceLoader.exists(str(sheets[key])),
			"精灵表不存在：" + str(sheets[key]))


func test_animations_are_valid() -> void:
	var d := _manifest()
	var anims: Dictionary = d.get("animations", {})
	var sheets: Dictionary = d.get("sheets", {})
	assert_true(anims.size() >= 18, "至少 18 个动画（13 旧 + 6 新 - run_front 迁移）")
	for name in anims.keys():
		var a: Dictionary = anims[name]
		assert_true(sheets.has(str(a.get("sheet", ""))), "缺少 sheet: " + str(name))
		assert_gt(float(a.get("fps", 0.0)), 0.0, "fps 应 >0: " + str(name))
		var segs: Array = a.get("segments", [])
		assert_gt(segs.size(), 0, "没有 segments: " + str(name))
		var sheet_rows_map: Dictionary = d.get("sheet_rows", {})
		var rows_of_sheet: int = int(sheet_rows_map.get(str(a.get("sheet", "")), 8))
		for seg in segs:
			var row := int(seg.get("row", -1))
			var start := int(seg.get("start", -1))
			var count := int(seg.get("count", 0))
			assert_true(row >= 0 and row < rows_of_sheet, "row 越界: " + str(name))
			assert_true(start >= 0 and count >= 1 and start + count <= 8,
				"frames 越界: " + str(name))
		var bbox: Array = a.get("bbox", [])
		assert_eq(bbox.size(), 4, "bbox 应为 4 个数: " + str(name))
		if bbox.size() == 4:
			assert_true(float(bbox[0]) >= 0.0 and float(bbox[2]) <= 1.0001,
				"bbox x 越界: " + str(name))
			assert_true(float(bbox[1]) >= 0.0 and float(bbox[3]) <= 1.0001,
				"bbox y 越界: " + str(name))
			assert_true(float(bbox[2]) > float(bbox[0]), "bbox 宽应 >0: " + str(name))
			assert_true(float(bbox[3]) > float(bbox[1]), "bbox 高应 >0: " + str(name))


func test_state_map_targets_exist() -> void:
	var d := _manifest()
	var anims: Dictionary = d.get("animations", {})
	var state_map: Dictionary = d.get("state_map", {})
	for state in ["idle", "thinking", "happy", "confused", "sleepy", "easter", "dragging"]:
		assert_has_key(state_map, state, "state_map 缺少 " + state)
		if state_map.has(state):
			assert_true(anims.has(str(state_map[state])),
				"state_map 指向不存在的动画: " + state)


func test_idle_micro_targets_exist() -> void:
	var d := _manifest()
	var anims: Dictionary = d.get("animations", {})
	var micro: Array = d.get("idle_micro", [])
	assert_gt(micro.size(), 0)
	for entry in micro:
		assert_true(anims.has(str(entry.get("anim", ""))),
			"idle_micro 指向不存在的动画")


func test_micro_scenes_targets_exist() -> void:
	# 微剧场 10 组编排（CharacterView.MICRO_SCENES）引用的动画必须都存在
	var d := _manifest()
	var anims: Dictionary = d.get("animations", {})
	var scenes: Array = preload("res://scripts/character_view.gd").MICRO_SCENES
	assert_true(scenes.size() >= 10, "微剧场应有 10 组编排")
	for scene in scenes:
		for step in scene.get("steps", []):
			var clip: String = step if step is String else str(step.get("clip", ""))
			assert_true(anims.has(clip), "微剧场引用不存在的动画: " + clip)
