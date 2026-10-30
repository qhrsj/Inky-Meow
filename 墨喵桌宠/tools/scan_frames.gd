extends SceneTree

const DIR := "D:/Downloads/墨喵静态帧/精灵帧新"
const CELL := 512

func _initialize() -> void:
	var dir := DirAccess.open(DIR)
	if dir == null:
		printerr("无法打开目录")
		quit(1)
		return
	for f in dir.get_files():
		if not f.ends_with(".png"):
			continue
		var img := Image.load_from_file(DIR + "/" + f)
		if img == null:
			printerr(f + " 加载失败")
			continue
		img.convert(Image.FORMAT_RGBA8)
		var cols := img.get_width() / CELL
		var rws := img.get_height() / CELL
		var row_info := []
		for r in rws:
			var filled := []
			for c in cols:
				if _cell_has_content(img, c, r):
					filled.append(c)
			if filled.size() > 0:
				row_info.append("%d:%d-%d(%d)" % [r, filled[0], filled[filled.size() - 1], filled.size()])
		print(f, " | rows: ", " ".join(row_info))
	quit(0)

func _cell_has_content(img: Image, cx: int, cy: int) -> bool:
	var step := 16
	for y in range(cy * CELL, (cy + 1) * CELL, step):
		for x in range(cx * CELL, (cx + 1) * CELL, step):
			var px := img.get_pixel(x, y)
			if px.a > 0.02:
				return true
	return false
