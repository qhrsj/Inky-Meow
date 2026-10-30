extends Node

## 墨喵桌宠组合根：窗口、状态机、sidecar、精灵、UI、朗读、场景、记忆。

const SM_SCRIPT := preload("res://scripts/pet_state_machine.gd")
const POMODORO_SCRIPT := preload("res://scripts/services/pomodoro.gd")
const POEM_BOOK_SCRIPT := preload("res://scripts/services/poem_book.gd")
const POEM_PANEL_SCRIPT := preload("res://scripts/ui/poem_panel.gd")
const LOG := preload("res://scripts/util/app_log.gd")
const UI_THEME := preload("res://scripts/ui/ui_theme.gd")
const ROAM_SCRIPT := preload("res://scripts/services/roam.gd")

const LINES := [
	"喵～",
	"今天想读哪首诗？",
	"要不要对一句？",
	"墨已研好，等你落笔。",
	"诗在，便有远方。",
]

@onready var wc: WindowController = $WindowController
@onready var model: ModelClient = $ModelClient
@onready var brain: PetBrain = $PetBrain
@onready var sidecar: SidecarManager = $SidecarManager
@onready var voice: VoicePlayer = $VoicePlayer
@onready var voice_input: VoiceInput = $VoiceInput
@onready var ui_layer: CanvasLayer = $UILayer
@onready var ui: Control = $UILayer/UI
@onready var character: CharacterView = $UILayer/UI/CharacterAnchor/CharacterView
@onready var hit_area: PetHitArea = $UILayer/UI/PetHitArea
@onready var bubble: SpeechBubble = $UILayer/UI/Bubble
@onready var panel: InputPanel = $UILayer/UI/InputPanel
@onready var memory_panel: MemoryPanel = $UILayer/UI/MemoryPanel
@onready var confirm_panel: ConfirmPanel = $UILayer/UI/ConfirmPanel
var poem_panel: Control = POEM_PANEL_SCRIPT.new()
@onready var debug_hud: Label = $UILayer/UI/DebugHUD
@onready var tray: StatusIndicator = $Tray
@onready var tray_menu: AppMenu = $Tray/TrayMenu
@onready var app_menu: AppMenu = $AppMenu

var sm: PetStateMachine
var _pomodoro: RefCounted = POMODORO_SCRIPT.new()
var _roam: RefCounted = ROAM_SCRIPT.new()
var poem_book = POEM_BOOK_SCRIPT.new()
var _last_poem = null            # {"title", "body"}；最近一次成诗，供收藏
var _countdown_accum := 0.0
var _countdown_dismissed := false
var _debug := false
var _fast := false
var _debug_accum := 0.0
var _voice_enabled := false
var _voice_active := false            # 语音采集占用（调度闸门用）
var _confirm_action := ""


func _ready() -> void:
	randomize()
	_parse_args()
	LOG.info("墨喵启动 debug=%s fast_idle=%s" % [str(_debug), str(_fast)])
	sm = SM_SCRIPT.new()
	if _fast:
		sm.idle_to_sleepy = 5.0
		sm.sleepy_to_easter = 10.0
		sm.happy_hold = 1.2
		sm.confused_hold = 1.5
		sm.easter_hold = 3.0

	wc.setup({
		"character": character,
		"bubble": bubble,
		"panel": panel,
		"memory_panel": memory_panel,
		"confirm_panel": confirm_panel,
		"poem_panel": poem_panel,
		"tray": tray,
		"ui_layer": ui_layer,
		"debug_hud": debug_hud,
	})
	sidecar.setup(model)
	voice.setup(model)
	brain.setup(sm, model, bubble, panel, voice)
	voice_input.setup(model, panel, brain, voice, bubble)
	ui.add_child(poem_panel)
	poem_book.load_book()
	_apply_theme()
	_connect_signals()

	if _debug:
		_setup_debug_overlay()
	debug_hud.visible = _debug

	_load_settings()
	panel.set_model_ready(false, false)
	panel.set_voice_ready(false)
	bubble.set_anchor_point(character.get_bubble_anchor_global())
	_maybe_show_intro()
	character.play("idle")
	sm.set_hidden(false)
	_update_ui_state()
	wc.schedule_refresh()
	_refresh_debug_hud()
	call_deferred("_boot_sidecar")


func _process(delta: float) -> void:
	if _pomodoro != null:
		_pomodoro.tick(delta)
	_update_countdown(delta)
	if _roam != null:
		# 微剧场组合演出期间冻结闲逛（二选一：谁在占用就等谁演完）
		_roam.set_paused(_roam_paused() or character.is_scene_active())
		_roam.tick(delta)
	if sm != null:
		sm.tick(delta)
	if _debug:
		_debug_accum += delta
		if _debug_accum >= 0.25:
			_debug_accum = 0.0
			_refresh_debug_hud()


## 闲逛暂停门控：隐藏/拖动/面板打开/模型请求中/专注/反馈独占/困倦彩蛋/语音占用/跌倒起身恢复
func _roam_paused() -> bool:
	var feedback: bool = sm != null and sm.get_state() in [
		PetStateMachine.State.HAPPY, PetStateMachine.State.CONFUSED,
		PetStateMachine.State.SLEEPY, PetStateMachine.State.EASTER]
	return wc.is_hidden() or _aux_open() or _pomodoro.is_running() \
		or (sm != null and sm.is_busy()) or feedback or _voice_active \
		or (character != null and character.current_clip == "getup")


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if sm != null:
			sm.notify_user_input()


## 「水墨宣纸」主题：注入 UILayer 与两个菜单，全部面板/按钮自动继承。
func _apply_theme() -> void:
	var theme: Theme = UI_THEME.build()
	ui.theme = theme
	app_menu.theme = theme
	tray_menu.theme = theme


# ----- 开场自我介绍 -----

const INTRO_TEXT := ("吾乃墨喵，砚台边念诗的小灵猫喵。\n" +
	"点题成诗、格律体检、续写对句、闲聊都行；\n" +
	"说「飞花令，月」可开局对诗：轮流说带令字的诗句，接不上算输。\n" +
	"右键菜单还有：番茄钟专注陪伴、诗笺收藏、朗读开关、功能介绍。")


func _maybe_show_intro() -> void:
	if bool(ConfigStore.load_value("general", "intro_shown", false)):
		return
	ConfigStore.set_value("general", "intro_shown", true)
	# 等 UI 与窗口就绪后再亮气泡
	get_tree().create_timer(1.2).timeout.connect(_show_intro)


func _show_intro() -> void:
	bubble.show_text(INTRO_TEXT, 14.0)


func _connect_signals() -> void:
	sm.state_changed.connect(_on_state_changed)
	sm.wake_up.connect(_on_wake_up)
	hit_area.clicked.connect(_on_pet_clicked)
	hit_area.double_clicked.connect(_on_pet_double_clicked)
	hit_area.context_menu_requested.connect(_on_context_menu)
	hit_area.drag_begin.connect(_on_drag_begin)
	hit_area.drag_direction.connect(_on_drag_direction)
	hit_area.drag_end.connect(_on_drag_end)
	bubble.bubble_changed.connect(wc.schedule_refresh)
	bubble.dismissed.connect(func():
		_countdown_dismissed = true
		wc.schedule_refresh())
	character.clip_changed.connect(func(_clip: String): wc.schedule_refresh())
	poem_panel.opened.connect(_on_aux_opened)
	poem_panel.closed.connect(_on_aux_closed)
	poem_panel.entry_deleted.connect(_on_poem_deleted)
	_roam.move_by.connect(wc.move_window)
	_roam.walk_started.connect(func(dir: Vector2):
		character.set_facing(dir)
		character.play(_roam_clip_for(dir)))
	_roam.walk_ended.connect(func(): character.play("idle"))
	_roam.play_clip.connect(func(name: String): character.play(name))
	character.walk_move.connect(wc.move_window)   # 微剧场走路步：沿动画方向真实平移
	panel.opened.connect(_on_panel_opened)
	panel.closed.connect(_on_panel_closed)
	panel.submitted.connect(_on_panel_submitted)
	panel.voice_started.connect(_on_voice_started)
	panel.voice_stopped.connect(_on_voice_stopped)
	voice_input.transcript.connect(_on_voice_transcript)
	memory_panel.opened.connect(_on_aux_opened)
	memory_panel.closed.connect(_on_aux_closed)
	memory_panel.refresh_requested.connect(_on_memory_refresh)
	memory_panel.clear_requested.connect(_on_memory_clear)
	confirm_panel.opened.connect(_on_aux_opened)
	confirm_panel.closed.connect(_on_aux_closed)
	confirm_panel.confirmed.connect(_on_confirmed)
	app_menu.action.connect(_on_menu_action)
	tray_menu.action.connect(_on_menu_action)
	wc.drag_started.connect(func(): sm.set_dragging(true))
	wc.drag_ended.connect(func(): sm.set_dragging(false))
	wc.hidden_changed.connect(_on_hidden_changed)
	sidecar.status_changed.connect(_on_sidecar_status)
	sidecar.ready_changed.connect(_on_model_ready)
	voice.unavailable.connect(_on_voice_unavailable)
	brain.result_ready.connect(_on_result_ready)
	_pomodoro.phase_changed.connect(_on_pomodoro_phase)
	app_menu.about_to_popup.connect(_refresh_focus_menu)
	tray_menu.about_to_popup.connect(_refresh_focus_menu)


func _load_settings() -> void:
	_voice_enabled = bool(ConfigStore.load_value("voice", "enabled", false))
	voice.set_enabled(_voice_enabled)
	app_menu.set_voice_checked(_voice_enabled)
	tray_menu.set_voice_checked(_voice_enabled)
	_set_model_status("加载中…")
	if bool(ConfigStore.load_value("roam", "enabled", false)):
		_roam.set_enabled(true)
	_sync_focus_menu_checks()


func _boot_sidecar() -> void:
	_set_model_status("加载中…")
	panel.set_model_ready(false, false)
	bubble.show_waiting("研墨中…")
	var res: Dictionary = await sidecar.ensure_ready()
	if res.get("ok", false):
		var poetry := bool(res.get("poetry", false))
		var chat := bool(res.get("chat", false))
		var asr := bool(res.get("asr", false))
		panel.set_model_ready(poetry, chat)
		panel.set_voice_ready(chat and asr)
		_set_model_status("就绪" if chat else "诗模型就绪")
		if chat:
			bubble.show_text("墨喵醒了，可以作诗了喵。", 3.0)
		else:
			bubble.show_text("诗模型醒了，闲聊还在加载喵。", 3.0)
	else:
		panel.set_model_ready(false, false)
		panel.set_voice_ready(false)
		_set_model_status("离线")
		bubble.show_text("模型没醒…右键菜单可重连喵。", 5.0)


func _on_sidecar_status(state: String, _detail: String) -> void:
	LOG.info("sidecar 状态 -> %s" % state)
	_refresh_debug_hud()
	if state in [SidecarManager.STATUS_PROBING, SidecarManager.STATUS_SPAWNING,
			SidecarManager.STATUS_LOADING_POETRY]:
		_set_model_status("加载中…")
		return
	match state:
		SidecarManager.STATUS_POETRY_READY:
			_set_model_status("诗模型就绪")
		SidecarManager.STATUS_CHAT_LOADING:
			_set_model_status("闲聊加载中")
		SidecarManager.STATUS_CHAT_READY:
			_set_model_status("就绪")
		SidecarManager.STATUS_FAILED:
			_set_model_status("离线")
			panel.set_model_ready(false, false)
			panel.set_voice_ready(false)


func _on_model_ready(poetry: bool, chat: bool, _tts: bool, asr: bool) -> void:
	panel.set_model_ready(poetry, chat)
	panel.set_voice_ready(chat and asr)
	_set_model_status("就绪" if chat else ("诗模型就绪" if poetry else "加载中…"))


func _set_model_status(text: String) -> void:
	app_menu.set_model_status(text)
	tray_menu.set_model_status(text)


func _on_state_changed(new_state: int, old_state: int) -> void:
	character.play(sm.state_name(new_state))
	# 追完蝴蝶跑回来（run_back 的正经用处）
	if old_state == PetStateMachine.State.EASTER and new_state != PetStateMachine.State.EASTER:
		character.play_sequence(["run_back"], "idle")
	_update_idle_micro()
	_refresh_debug_hud()
	if new_state == PetStateMachine.State.SLEEPY and old_state != PetStateMachine.State.SLEEPY:
		bubble.show_text("有些困了…", 3.0)
	elif new_state == PetStateMachine.State.EASTER:
		bubble.show_text("蝴蝶！", 3.0)


func _on_wake_up() -> void:
	# 光是醒来（IDLE）才打哈欠；摸头唤醒时保留开心的反应
	if sm.get_state() == PetStateMachine.State.IDLE:
		character.play_sequence(["yawn"], "idle")
	bubble.show_text("喵～醒啦", 2.0)


func _on_pet_clicked() -> void:
	if _aux_open():
		return
	var was_sleeping := sm.get_state() == PetStateMachine.State.SLEEPY \
		or sm.get_state() == PetStateMachine.State.EASTER
	sm.pet_click()
	if not was_sleeping:
		bubble.show_text(LINES[randi() % LINES.size()], 2.0)
	_refresh_debug_hud()


func _on_pet_double_clicked() -> void:
	_open_panel("theme")


func _on_context_menu(screen_pos: Vector2i) -> void:
	app_menu.popup(Rect2i(screen_pos, Vector2i.ZERO))


func _on_drag_begin(screen_start: Vector2i) -> void:
	wc.begin_drag(screen_start)


func _on_drag_direction(dir: int) -> void:
	character.play_drag(dir)


func _on_drag_end() -> void:
	wc.end_drag()


func _on_panel_opened() -> void:
	sm.notify_user_input()
	_update_ui_state()


func _on_panel_closed() -> void:
	if voice_input.is_recording():
		voice_input.cancel_recording()
	_update_ui_state()


func _on_aux_opened() -> void:
	sm.notify_user_input()
	_update_ui_state()


func _on_aux_closed() -> void:
	_update_ui_state()


func _update_ui_state() -> void:
	var any := _aux_open()
	sm.set_ui_active(any)
	wc.set_keyboard_focus(any)
	_update_idle_micro()
	if any:
		bubble.hide_bubble()
	wc.schedule_refresh()


func _update_idle_micro() -> void:
	if sm == null or character == null:
		return
	# 统一调度（状态机设计 PDF 第 2 页）：仅普通 IDLE 且无占用时开新一场；
	# 正在演的组合（含组合内 getup）不打断，硬打断走 set_idle_micro_enabled(false)
	var allowed: bool = sm.get_state() == PetStateMachine.State.IDLE \
		and not _voice_active \
		and not (_roam != null and _roam.is_roaming())
	character.set_micro_allowed(allowed)
	var idle: bool = sm.get_state() == PetStateMachine.State.IDLE
	var quiet_focus: bool = _pomodoro != null and _pomodoro.is_focusing()
	character.set_idle_micro_enabled(idle and not _aux_open() and not wc.is_hidden() and not quiet_focus)


func _aux_open() -> bool:
	return panel.is_open() or memory_panel.visible or confirm_panel.visible \
		or poem_panel.visible


## 等模型时的姿势：按功能换，别总是同一张「吟诗」
const CMD_CLIPS := {
	"theme": "poem",
	"couplet": "write",
	"check": "read",
	"write": "write",
	"chat": "look",
	"recite": "read",
	"search": "look",
}


func _on_panel_submitted(cmd: String, text: String) -> void:
	brain.run_command(cmd, text)
	character.play(str(CMD_CLIPS.get(cmd, "poem")))


func _on_voice_started() -> void:
	_voice_active = true
	# 语音对话模式自动打开 TTS（文本会发微软 edge-tts）
	if not _voice_enabled:
		_voice_enabled = true
		voice.set_enabled(true)
		app_menu.set_voice_checked(true)
		tray_menu.set_voice_checked(true)
		ConfigStore.set_value("voice", "enabled", true)
	character.play("look")            # 侧耳听
	voice_input.start_recording()


func _on_voice_transcript(_text: String) -> void:
	character.play("happy")           # 听懂了


func _on_voice_stopped() -> void:
	_voice_active = false
	voice_input.stop_recording()


func _open_panel(cmd: String) -> void:
	if wc.is_hidden():
		wc.set_pet_visible(true)
	if memory_panel.visible:
		memory_panel.close()
	if confirm_panel.visible:
		confirm_panel.close()
	panel.open(cmd)


func _on_menu_action(id: int) -> void:
	match id:
		AppMenu.ACTION_COMPOSE:
			_open_panel("theme")
		AppMenu.ACTION_CHAT:
			_open_panel("chat")
		AppMenu.ACTION_MEMORY:
			_open_memory()
		AppMenu.ACTION_TOGGLE_VOICE:
			_toggle_voice()
		AppMenu.ACTION_TOGGLE_FOCUS:
			_toggle_focus()
		AppMenu.ACTION_TOGGLE_ROAM:
			_toggle_roam()
		AppMenu.ACTION_SAVE_POEM:
			_save_poem()
		AppMenu.ACTION_OPEN_BOOK:
			_open_book()
		AppMenu.ACTION_INTRO:
			_show_intro()
		AppMenu.ACTION_RETRY_MODEL:
			_retry_model()
		AppMenu.ACTION_TOGGLE_VISIBLE:
			wc.set_pet_visible(wc.is_hidden())
		AppMenu.ACTION_QUIT:
			_quit()


func _on_hidden_changed(hidden: bool) -> void:
	sm.set_hidden(hidden)
	app_menu.set_hidden_label(hidden)
	tray_menu.set_hidden_label(hidden)
	if hidden:
		bubble.hide_bubble()
	_update_idle_micro()
	wc.schedule_refresh()


# ----- 记忆 -----

func _open_memory() -> void:
	if wc.is_hidden():
		wc.set_pet_visible(true)
	if panel.is_open():
		panel.close()
	if confirm_panel.visible:
		confirm_panel.close()
	memory_panel.open()
	_refresh_memory()


func _refresh_memory() -> void:
	memory_panel.set_loading()
	var res: Dictionary = await model.memory_get()
	if res.get("ok", false):
		var data: Dictionary = res.get("data", {})
		memory_panel.show_data(data)
	else:
		var err: Dictionary = res.get("error", {})
		memory_panel.show_error(str(err.get("message", "模型未就绪")))


func _on_memory_refresh() -> void:
	_refresh_memory()


func _on_memory_clear() -> void:
	_confirm_action = "memory_clear"
	confirm_panel.ask("清空记忆", "墨喵会忘掉你的称呼和记下的偏好，确定吗？", "清空", "取消")


func _clear_memory() -> void:
	var res: Dictionary = await model.memory_reset()
	if res.get("ok", false):
		if memory_panel.visible:
			var data: Dictionary = res.get("data", {})
			memory_panel.show_data(data)
		bubble.show_text("记忆清空了喵。", 2.0)
	else:
		var err: Dictionary = res.get("error", {})
		bubble.show_text("清空失败：" + str(err.get("message", "未知错误")), 3.0)


func _on_result_ready(cmd: String, data: Dictionary) -> void:
	_capture_last_poem(cmd, data)
	if cmd == "chat" and memory_panel.visible:
		_refresh_memory()


## 记下最近一次成诗（点题/续写/闲聊成诗），供「收藏这首诗」。
func _capture_last_poem(cmd: String, data: Dictionary) -> void:
	var poem_v: Variant = data.get("poem")
	var poem := str(poem_v) if poem_v != null else ""
	if poem.strip_edges().is_empty():
		return
	var title := str(PetBrain.CMD_TITLE.get(cmd, "成诗"))
	var form := str(data.get("form", "")) if data.get("form") != null else ""
	if not form.is_empty():
		title += "·" + form
	_last_poem = {"title": title, "body": poem}
	app_menu.set_save_enabled(true)
	tray_menu.set_save_enabled(true)


# ----- 诗笺收藏 -----

func _save_poem() -> void:
	if _last_poem == null:
		bubble.show_text("还没有可以收的诗，先点题成一首喵。", 3.0)
		return
	var added: bool = poem_book.add(str(_last_poem["title"]), str(_last_poem["body"]))
	if added:
		LOG.info("诗笺收藏 title=%s 共%d首" % [str(_last_poem["title"]), poem_book.count()])
		bubble.show_text("已收入诗笺，共 %d 首喵。" % poem_book.count(), 3.0)
	else:
		bubble.show_text("这首已经在诗笺里了喵。", 3.0)


func _open_book() -> void:
	if wc.is_hidden():
		wc.set_pet_visible(true)
	if panel.is_open():
		panel.close()
	if memory_panel.visible:
		memory_panel.close()
	if confirm_panel.visible:
		confirm_panel.close()
	poem_panel.open(poem_book.entries)
	sm.notify_user_input()
	_update_ui_state()


func _on_poem_deleted(index: int) -> void:
	if poem_book.remove_at(index):
		LOG.info("诗笺删除 index=%d 剩%d首" % [index, poem_book.count()])
		poem_panel.open(poem_book.entries)
		bubble.show_text("已移出诗笺喵。", 2.0)
	else:
		poem_panel.open(poem_book.entries)


# ----- 开关 -----

func _toggle_voice() -> void:
	_voice_enabled = not _voice_enabled
	voice.set_enabled(_voice_enabled)
	app_menu.set_voice_checked(_voice_enabled)
	tray_menu.set_voice_checked(_voice_enabled)
	ConfigStore.set_value("voice", "enabled", _voice_enabled)
	if _voice_enabled and not model.tts_available:
		bubble.show_text("朗读需要联网 + ffmpeg，可能不可用喵。", 3.0)


func _on_confirmed() -> void:
	var action := _confirm_action
	_confirm_action = ""
	match action:
		"memory_clear":
			_clear_memory()


func _on_voice_unavailable(message: String) -> void:
	LOG.warn("朗读不可用：%s" % message)
	_voice_enabled = false
	voice.set_enabled(false)
	app_menu.set_voice_checked(false)
	tray_menu.set_voice_checked(false)
	ConfigStore.set_value("voice", "enabled", false)
	bubble.show_text(message, 3.0)


# ----- 专注陪伴（番茄钟）-----

func _toggle_focus() -> void:
	if not _pomodoro.is_running() and wc.is_hidden():
		wc.set_pet_visible(true)
	_countdown_dismissed = false
	_countdown_accum = 0.0
	_pomodoro.toggle()


func _on_pomodoro_phase(new_phase: int, _old_phase: int) -> void:
	LOG.info("番茄钟阶段 -> %s 已完成=%d" % [_pomodoro.phase_name(new_phase), _pomodoro.completed_focus])
	_countdown_dismissed = false
	_countdown_accum = 0.0
	_sync_focus_menu_checks()
	match new_phase:
		POMODORO_SCRIPT.Phase.FOCUS:
			sm.set_quiet_mode(true)
			character.play("read")
			_update_idle_micro()
			_announce("专注开始，%d 分钟喵，墨喵陪你读书。" % int(round(_pomodoro.focus_minutes)), 4.0)
		POMODORO_SCRIPT.Phase.BREAK:
			sm.set_quiet_mode(false)
			_update_idle_micro()
			character.play_sequence(["happy"], "idle")
			_announce("番茄完成！休息 %d 分钟喵。" % int(round(_pomodoro.break_minutes)), 4.0)
		POMODORO_SCRIPT.Phase.LONG_BREAK:
			sm.set_quiet_mode(false)
			_update_idle_micro()
			character.play_sequence(["happy"], "idle")
			_announce("完成 %d 个番茄，好好歇 %d 分钟喵！" % [
				_pomodoro.long_every, int(round(_pomodoro.long_break_minutes))], 5.0)
		POMODORO_SCRIPT.Phase.OFF:
			sm.set_quiet_mode(false)
			_update_idle_micro()
			_announce("专注模式关了喵。", 2.0)
	_refresh_focus_menu()


## 阶段公告：气泡 + 语音（force：番茄播报不受「朗读回复」总开关影响；
## TTS 不可用时会自动降级为纯气泡，连续失败会提示）。
func _announce(text: String, duration: float) -> void:
	bubble.show_text(text, duration)
	voice.speak(text, true)


## 头顶倒计时：专注/休息期间常驻气泡，每秒刷新；点掉后不再打扰（下个阶段恢复）。
func _update_countdown(delta: float) -> void:
	if _pomodoro == null or not _pomodoro.is_running() or _countdown_dismissed:
		return
	if _aux_open() or wc.is_hidden():
		return
	_countdown_accum += delta
	if _countdown_accum < 1.0:
		return
	_countdown_accum = 0.0
	var text := _countdown_text()
	if bubble.is_showing():
		if bubble.pinned_text != "" and bubble.pinned_text != text:
			bubble.show_pinned(text)
		# 正在显示公告/结果时不抢气泡，等它隐藏后下一秒自动恢复
	elif bubble.pinned_text != "":
		bubble.hide_bubble()
	else:
		bubble.show_pinned(text)
		wc.schedule_refresh()


func _countdown_text() -> String:
	match _pomodoro.phase:
		POMODORO_SCRIPT.Phase.FOCUS:
			return "专注 %s" % _fmt_clock(_pomodoro.get_phase_left())
		POMODORO_SCRIPT.Phase.BREAK:
			return "休息 %s" % _fmt_clock(_pomodoro.get_phase_left())
		POMODORO_SCRIPT.Phase.LONG_BREAK:
			return "长休 %s" % _fmt_clock(_pomodoro.get_phase_left())
	return ""


func _sync_focus_menu_checks() -> void:
	var on: bool = _pomodoro.is_running()
	app_menu.set_focus_checked(on)
	tray_menu.set_focus_checked(on)
	var roam_on: bool = _roam != null and _roam.enabled
	app_menu.set_roam_checked(roam_on)
	tray_menu.set_roam_checked(roam_on)


# ----- 桌面闲逛 -----

func _toggle_roam() -> void:
	var on: bool = not _roam.enabled
	_roam.set_enabled(on)
	if on and wc.is_hidden():
		wc.set_pet_visible(true)
	if not on:
		# 停下来时保存位置，下次启动别瞬移
		ConfigStore.set_value("window", "position", wc.get_window().position)
		character.play("idle")
	LOG.info("桌面闲逛 -> %s" % str(on))
	_sync_focus_menu_checks()


## 闲逛方向 → 动画：左右用侧视吹笛（右向镜像），上/下用背面跑/正面跑替代
func _roam_clip_for(dir: Vector2) -> String:
	if dir.y < 0:
		return "run_back"
	if dir.y > 0:
		return "run_front"
	return "walk_flute"


func _refresh_focus_menu() -> void:
	var text := ""
	match _pomodoro.phase:
		POMODORO_SCRIPT.Phase.FOCUS:
			text = "专注剩余 %s" % _fmt_clock(_pomodoro.get_phase_left())
		POMODORO_SCRIPT.Phase.BREAK:
			text = "休息剩余 %s" % _fmt_clock(_pomodoro.get_phase_left())
		POMODORO_SCRIPT.Phase.LONG_BREAK:
			text = "长休剩余 %s" % _fmt_clock(_pomodoro.get_phase_left())
		POMODORO_SCRIPT.Phase.OFF:
			text = "已专注 %d 个番茄" % _pomodoro.completed_focus
	app_menu.set_focus_status(text)
	tray_menu.set_focus_status(text)


func _fmt_clock(seconds: float) -> String:
	var total := int(round(seconds))
	return "%02d:%02d" % [floori(total / 60.0), total % 60]


func _retry_model() -> void:
	if sidecar.is_spawned():
		await sidecar.cleanup()
	_boot_sidecar()


func _quit() -> void:
	LOG.info("墨喵退出")
	if voice_input.is_recording():
		await voice_input.cancel_recording()
	await sidecar.cleanup()
	wc.quit_app()


# ----- 调试/参数 -----

func _parse_args() -> void:
	var args := OS.get_cmdline_args()
	args.append_array(OS.get_cmdline_user_args())
	_fast = args.has("--fast-idle")
	_debug = args.has("--debug-polygon") or args.has("--debug")


func _setup_debug_overlay() -> void:
	var line := Line2D.new()
	line.name = "DebugPolygon"
	line.width = 2.0
	line.default_color = Color(0.85, 0.1, 0.1, 0.9)
	line.closed = true
	ui.add_child(line)
	wc.set_debug_line(line)


func _refresh_debug_hud() -> void:
	if not _debug or sm == null:
		return
	debug_hud.text = "state=%s hidden=%s drag=%s\nmodel=%s poetry=%s chat=%s sidecar=%s" % [
		sm.state_name(),
		str(wc.is_hidden()),
		str(sm.is_dragging()),
		model.transport,
		str(model.poetry_available),
		str(model.chat_available),
		sidecar.status,
	]
