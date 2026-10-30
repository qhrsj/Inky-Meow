class_name PoemBook
extends RefCounted

## 诗笺收藏：把喜欢的诗存到本地 JSON（默认 user://poems.json）。
## 纯逻辑、可注入路径便于测试；新的收藏插到最前（最近的在上面）。

const PATH := "user://poems.json"

var path := PATH
var entries: Array = []      # [{title, body, saved_at}]


func load_book() -> void:
	entries = []
	if not FileAccess.file_exists(path):
		return
	var raw := FileAccess.get_file_as_string(path)
	if raw.is_empty():
		return
	var parsed: Variant = JSON.parse_string(raw)
	if parsed is Array:
		for item in parsed:
			if item is Dictionary:
				entries.append({
					"title": str(item.get("title", "")),
					"body": str(item.get("body", "")),
					"saved_at": str(item.get("saved_at", "")),
				})


func save_book() -> bool:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		return false
	f.store_string(JSON.stringify(entries, "  "))
	return f.get_error() == OK


## 收藏一首；同题同文去重。成功返回 true，重复返回 false。
func add(title: String, body: String) -> bool:
	if body.strip_edges().is_empty():
		return false
	for e in entries:
		if str(e.get("title", "")) == title and str(e.get("body", "")) == body:
			return false
	entries.insert(0, {
		"title": title,
		"body": body,
		"saved_at": _now_text(),
	})
	return save_book()


func remove_at(index: int) -> bool:
	if index < 0 or index >= entries.size():
		return false
	entries.remove_at(index)
	return save_book()


func count() -> int:
	return entries.size()


func _now_text() -> String:
	var d := Time.get_datetime_dict_from_system()
	return "%04d-%02d-%02d %02d:%02d" % [
		int(d.get("year", 0)), int(d.get("month", 0)),
		int(d.get("day", 0)), int(d.get("hour", 0)), int(d.get("minute", 0)),
	]
