extends SceneTree

## 把 manifest 里每个动画的帧导出为 PNG 序列（256×256），供 Pillow 合成 GIF
const MANIFEST := "res://assets/sprites/manifest.json"
const OUT := "D:/AIProject/墨喵桌宠/.workbuddy/tmp_gif"
const CELL := 512
const SCALE := 256.0

func _initialize() -> void:
	var m: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(MANIFEST))
	if m == null or m.is_empty():
		printerr("manifest 解析失败")
		quit(1)
		return
	var sheets: Dictionary = m.get("sheets", {})
	var cell := int(m.get("cell", 512))
	var sheet_imgs := {}
	for key in sheets.keys():
		var res_path := str(sheets[key])
		var abs_path := ProjectSettings.globalize_path(res_path)
		var img := Image.load_from_file(abs_path)
		if img == null:
			printerr("表加载失败: " + res_path)
			continue
		img.convert(Image.FORMAT_RGBA8)
		sheet_imgs[key] = img
	var total := 0
	var anims: Dictionary = m.get("animations", {})
	for anim_name in anims.keys():
		var a: Dictionary = anims[anim_name]
		var img: Image = sheet_imgs.get(str(a.get("sheet", "")), null)
		if img == null:
			printerr("缺表: " + anim_name)
			continue
		var out_dir: String = OUT + "/" + anim_name
		DirAccess.make_dir_recursive_absolute(out_dir)
		var idx := 0
		for seg in a.get("segments", []):
			var row := int(seg.get("row", 0))
			var start := int(seg.get("start", 0))
			var count := int(seg.get("count", 0))
			for i in range(count):
				var cx := (start + i) * CELL
				var cy := row * CELL
				var frame := img.get_region(Rect2(cx, cy, CELL, CELL))
				frame.resize(int(SCALE), int(SCALE), Image.INTERPOLATE_LANCZOS)
				frame.save_png("%s/f%04d.png" % [out_dir, idx])
				idx += 1
		total += idx
		print(anim_name, " -> ", idx, " 帧")
	print("TOTAL_FRAMES=", total)
	quit(0)
