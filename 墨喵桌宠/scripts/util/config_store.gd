@tool
class_name ConfigStore
extends RefCounted

## user://momiao.cfg 的极简读写封装。只保存少量 UI 状态。

const PATH := "user://momiao.cfg"


static func load_all() -> Dictionary:
	var cfg := ConfigFile.new()
	if cfg.load(PATH) != OK:
		return {}
	var out := {}
	for section in cfg.get_sections():
		var values := {}
		for key in cfg.get_section_keys(section):
			values[key] = cfg.get_value(section, key)
		out[section] = values
	return out


static func save_all(data: Dictionary) -> void:
	var cfg := ConfigFile.new()
	for section in data.keys():
		var values: Variant = data[section]
		if not values is Dictionary:
			continue
		for key in values.keys():
			cfg.set_value(str(section), str(key), values[key])
	cfg.save(PATH)


static func load_value(section: String, key: String, default: Variant) -> Variant:
	var cfg := ConfigFile.new()
	if cfg.load(PATH) != OK:
		return default
	return cfg.get_value(section, key, default)


static func set_value(section: String, key: String, value: Variant) -> void:
	var cfg := ConfigFile.new()
	cfg.load(PATH)
	cfg.set_value(section, key, value)
	cfg.save(PATH)
