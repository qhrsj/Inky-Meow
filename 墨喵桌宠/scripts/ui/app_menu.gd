class_name AppMenu
extends PopupMenu

## 右键菜单与托盘菜单共用同一套动作 ID。

signal action(id: int)

const ACTION_COMPOSE := 100
const ACTION_CHAT := 101
const ACTION_SAVE_POEM := 131
const ACTION_OPEN_BOOK := 132
const ACTION_INTRO := 133
const ACTION_MEMORY := 130
const ACTION_TOGGLE_VOICE := 140
const ACTION_RETRY_MODEL := 142
const ACTION_MODEL_STATUS := 143
const ACTION_TOGGLE_FOCUS := 150
const ACTION_TOGGLE_ROAM := 151
const ACTION_TOGGLE_VISIBLE := 120
const ACTION_QUIT := 999


func _ready() -> void:
	popup_window = true
	if item_count == 0:
		add_item("打开诗笺", ACTION_COMPOSE)
		add_item("聊聊天", ACTION_CHAT)
		add_separator()
		add_check_item("专注陪伴（25 分钟）", ACTION_TOGGLE_FOCUS)
		add_check_item("桌面闲逛", ACTION_TOGGLE_ROAM)
		add_item("收藏这首诗", ACTION_SAVE_POEM)
		add_item("我的诗笺", ACTION_OPEN_BOOK)
		add_item("功能介绍", ACTION_INTRO)
		add_item("记忆…", ACTION_MEMORY)
		add_check_item("朗读回复", ACTION_TOGGLE_VOICE)
		add_item("模型：加载中…", ACTION_MODEL_STATUS)
		set_item_disabled(get_item_index(ACTION_MODEL_STATUS), true)
		add_item("重连模型", ACTION_RETRY_MODEL)
		add_separator()
		add_item("隐藏墨喵", ACTION_TOGGLE_VISIBLE)
		add_separator()
		add_item("退出", ACTION_QUIT)
	if not id_pressed.is_connected(_on_id_pressed):
		id_pressed.connect(_on_id_pressed)


func set_voice_checked(on: bool) -> void:
	var idx := get_item_index(ACTION_TOGGLE_VOICE)
	if idx >= 0:
		set_item_checked(idx, on)


func set_focus_checked(on: bool) -> void:
	var idx := get_item_index(ACTION_TOGGLE_FOCUS)
	if idx >= 0:
		set_item_checked(idx, on)


## 有诗可收时才允许「收藏这首诗」。
func set_save_enabled(on: bool) -> void:
	var idx := get_item_index(ACTION_SAVE_POEM)
	if idx >= 0:
		set_item_disabled(idx, not on)


func set_roam_checked(on: bool) -> void:
	var idx := get_item_index(ACTION_TOGGLE_ROAM)
	if idx >= 0:
		set_item_checked(idx, on)


## 菜单打开时刷新剩余时间显示。
func set_focus_status(text: String) -> void:
	var idx := get_item_index(ACTION_TOGGLE_FOCUS)
	if idx >= 0:
		var base := "专注陪伴（25 分钟）"
		if text.is_empty():
			set_item_text(idx, base)
		else:
			set_item_text(idx, "专注陪伴 · %s" % text)


func set_model_status(text: String) -> void:
	var idx := get_item_index(ACTION_MODEL_STATUS)
	if idx >= 0:
		set_item_text(idx, "模型：" + text)


func set_hidden_label(hidden: bool) -> void:
	var idx := get_item_index(ACTION_TOGGLE_VISIBLE)
	if idx >= 0:
		set_item_text(idx, "显示墨喵" if hidden else "隐藏墨喵")


func _on_id_pressed(id: int) -> void:
	action.emit(id)
