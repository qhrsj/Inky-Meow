class_name CharacterView
extends Node2D

## 雌版精灵角色视图：只使用 4 张 8×8 精灵表，无卡片占位。
## 对外接口：play(clip) / play_sequence(steps) / play_drag(dir) / get_tray_icon() /
## get_passthrough_polygons()。
##
## 13 个动作的去处（不许浪费）：
##   read        闲置默认、读诗/体检
##   poem        思考、成诗
##   write       提笔（对句/续写）
##   look        张望、思索（闲聊等待）、微剧场
##   happy       摸头、成功、吹完笛子
##   confused    出错、看懵
##   yawn        睡醒、读书读困
##   sleep       困倦状态
##   walk_flute  微剧场（边走边吹）
##   front_flute 微剧场（站着吹）
##   run_front   拖动（向右/向前）
##   run_back    拖动（向左/向后）、追蝶跑回
##   chase       追蝶彩蛋、微剧场

signal clip_changed(clip: String)
signal walk_move(vec: Vector2)        # 微剧场走路步：请求窗口按方向平移

const MANIFEST_PATH := "res://assets/sprites/manifest.json"
const TARGET_HEIGHT := 450.0
const MICRO_MIN := 8.0
const MICRO_MAX := 18.0

## 空闲微剧场：10 组编排（2026-10-30 状态机设计 PDF 第 3 页），每组回到读书。
## 规则：仅播放动画，不调 AI、不切业务状态；循环动作按 loops 计时长；
## getup（跌倒起身）单次播完接下一项；旧 fall 保留素材但编排不再使用。
const MICRO_SCENES := [
	{"weight": 0.10, "steps": ["read", {"clip": "poem", "loops": 1}, {"clip": "write", "loops": 1}, {"clip": "happy", "loops": 1}, "read"]},
	{"weight": 0.09, "steps": ["read", "look", "head_tilt", "read"]},
	{"weight": 0.10, "steps": ["look", {"clip": "walk_flute", "loops": 1, "move": "h"}, {"clip": "front_flute", "loops": 1}, {"clip": "happy", "loops": 1}, "read"]},
	{"weight": 0.10, "steps": ["read", "look", "steal_fish", {"clip": "happy", "loops": 1}, "read"]},
	{"weight": 0.09, "steps": ["look", {"clip": "chase", "loops": 1, "move": "h"}, {"clip": "run_front", "loops": 1, "move": [0, 1]}, "getup", "look", "tsundere", "read"]},
	{"weight": 0.10, "steps": ["read", "confused", {"clip": "poem", "loops": 1}, {"clip": "write", "loops": 1}, {"clip": "happy", "loops": 1}, "read"]},
	{"weight": 0.09, "steps": ["angry", "tsundere", "head_tilt", "read"]},
	{"weight": 0.10, "steps": ["read", "yawn", "stretch", "look", "read"]},
	{"weight": 0.11, "steps": ["look", {"clip": "run_back", "loops": 1, "move": [0, -1]}, {"clip": "walk_flute", "loops": 1, "move": "h"}, "getup", "head_tilt", "read"]},
	{"weight": 0.12, "steps": ["read", "yawn", {"clip": "sleep", "loops": 4}, "stretch", "read"]},
]

@onready var sprite: AnimatedSprite2D = $AnimatedSprite2D
@onready var missing_label: Label = $MissingLabel

var current_clip := "read"

var _manifest: Dictionary = {}
var _animations: Dictionary = {}
var _state_map: Dictionary = {}
var _sheets: Dictionary = {}
var _sheet_textures: Dictionary = {}
var _frames: SpriteFrames = null
var _sprite_ready := false
var _cell := 512
var _rng := RandomNumberGenerator.new()

# idle 微行为
var _idle_micro_enabled := false
var micro_allowed := true            # 统一调度：反馈/语音/移动占用时由 main 关闸
var _micro_timer := 0.0
var _micro_interval := 10.0
var _micro_active := false
var _last_scene_idx := -1             # 上一组编号（防连续重演）

# 动作序列（微剧场 / 事件编排共用）
var _seq: Array = []
var _seq_index := -1
var _seq_wait := 0.0
var _seq_one_shot := false
var _seq_active := false
var _seq_return := "idle"
var _seq_move := Vector2.ZERO         # 当前步的位移方向（ZERO = 原地表演）
const SCENE_WALK_SPEED := 40.0


func _ready() -> void:
	_rng.randomize()
	_micro_interval = _rng.randf_range(MICRO_MIN, MICRO_MAX)
	set_process(true)
	_load_manifest()
	if _manifest.is_empty():
		_show_missing("精灵素材缺失")
		return
	_build_sprite_frames()
	if not _sprite_ready:
		_show_missing("精灵表缺失")
		return
	sprite.sprite_frames = _frames
	sprite.centered = true
	sprite.position = Vector2.ZERO
	sprite.scale = Vector2.ONE * (TARGET_HEIGHT / float(_cell))
	sprite.texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR
	sprite.animation_finished.connect(_on_animation_finished)
	_show_missing("")
	play("idle")


func _process(delta: float) -> void:
	if not _sprite_ready:
		return
	if _seq_active:
		if _seq_one_shot:
			return          # 等 animation_finished
		if _seq_move != Vector2.ZERO:
			walk_move.emit(_seq_move * SCENE_WALK_SPEED * delta)
		_seq_wait -= delta
		if _seq_wait <= 0.0:
			_advance_seq()
		return
	if not _idle_micro_enabled:
		return
	if not micro_allowed:
		return
	if current_clip != "read":
		return
	_micro_timer += delta
	if _micro_timer >= _micro_interval:
		_pick_micro()


## 状态名由 state_map 映射到动画名；也可以直接传动画名。
func play(clip: String) -> void:
	if not _sprite_ready:
		return
	var anim := clip
	if _state_map.has(clip):
		anim = str(_state_map[clip])
	if not _animations.has(anim):
		anim = "read"
	_cancel_seq()
	current_clip = anim
	sprite.play(anim)
	clip_changed.emit(anim)


## 按顺序播一串动作，播完回到 return_clip。
## steps 里可以是动画名，也可以是 {"clip": "write", "loops": 2}。
func play_sequence(steps: Array, return_clip: String = "idle") -> void:
	if not _sprite_ready or steps.is_empty():
		return
	_cancel_seq()
	_seq = steps.duplicate()
	_seq_return = return_clip
	_seq_index = -1
	_seq_active = true
	_advance_seq()


## 拖动方向：朝右用正面跑，朝左用背面跑（run_back 也有正经用处了）。
func play_drag(dir: int) -> void:
	var clip := "run_front" if dir >= 0 else "run_back"
	if current_clip == clip:
		return
	play(clip)


## 闲逛朝向：侧视帧（walk_flute）原始朝左，向右走时水平镜像；竖直方向不翻转
func set_facing(dir: Vector2) -> void:
	if not _sprite_ready or dir.x == 0:
		return
	sprite.flip_h = dir.x > 0


func set_idle_micro_enabled(enabled: bool) -> void:
	_idle_micro_enabled = enabled
	if not enabled:
		if _micro_active:
			_cancel_seq()
			play("idle")
		_micro_timer = 0.0
	else:
		_micro_timer = 0.0
		_micro_interval = _rng.randf_range(MICRO_MIN, MICRO_MAX)


## 统一调度闸门（状态机设计 PDF 第 2 页）：false 只拦「新开一场」，
## 不打断正在演的组合（硬打断走 set_idle_micro_enabled(false)）。
## 注意：组合内部的 getup 不能当门控条件，否则演到跌倒起身必被自砍（已修的重复 bug）。
func set_micro_allowed(allowed: bool) -> void:
	micro_allowed = allowed
	if not allowed:
		_micro_timer = 0.0


## 微剧场组合是否正在演（含组合内 getup）：闲逛须让位，不得打断
func is_scene_active() -> bool:
	return _micro_active and _seq_active


func get_passthrough_polygons() -> Array[PackedVector2Array]:
	var out: Array[PackedVector2Array] = []
	if not _sprite_ready:
		return out
	var d: Dictionary = _animations.get(current_clip, {})
	var b: Array = d.get("bbox", [0.0, 0.0, 1.0, 1.0])
	if b.size() < 4:
		b = [0.0, 0.0, 1.0, 1.0]
	var cell := float(_cell)
	var rect := Rect2(
		(float(b[0]) - 0.5) * cell,
		(float(b[1]) - 0.5) * cell,
		(float(b[2]) - float(b[0])) * cell,
		(float(b[3]) - float(b[1])) * cell
	)
	var xf := sprite.get_global_transform_with_canvas()
	var pts := PackedVector2Array([
		xf * rect.position,
		xf * Vector2(rect.end.x, rect.position.y),
		xf * rect.end,
		xf * Vector2(rect.position.x, rect.end.y),
	])
	var offset := Geometry2D.offset_polygon(pts, 30.0)
	if offset.is_empty():
		out.append(pts)
	else:
		for poly in offset:
			out.append(poly)
	return out


func get_bubble_anchor_global() -> Vector2:
	return get_global_transform_with_canvas().origin + Vector2(0.0, -225.0)


func get_tray_icon() -> Texture2D:
	var tex: Texture2D = _sheet_textures.get("sheet_happy", _sheet_textures.get("c", null))
	if tex == null:
		return null
	var at := AtlasTexture.new()
	at.atlas = tex
	# 开心帧头部：512 格内取中上部 320×320
	at.region = Rect2(96, 0, 320, 320)
	return at


func _load_manifest() -> void:
	if not FileAccess.file_exists(MANIFEST_PATH):
		return
	var raw := FileAccess.get_file_as_string(MANIFEST_PATH)
	var parsed: Variant = JSON.parse_string(raw)
	if not parsed is Dictionary:
		return
	_manifest = parsed
	_animations = parsed.get("animations", {})
	_state_map = parsed.get("state_map", {})
	_sheets = parsed.get("sheets", {})
	_cell = int(parsed.get("cell", 512))


func _build_sprite_frames() -> void:
	for key in _sheets.keys():
		var path := str(_sheets[key])
		if ResourceLoader.exists(path):
			var tex := load(path) as Texture2D
			if tex != null:
				_sheet_textures[key] = tex
	if _sheet_textures.is_empty():
		return

	var sf := SpriteFrames.new()
	if sf.has_animation("default"):
		sf.remove_animation("default")
	var built := 0
	for anim_name in _animations.keys():
		var d: Dictionary = _animations[anim_name]
		var sheet_key := str(d.get("sheet", ""))
		var tex: Texture2D = _sheet_textures.get(sheet_key, null)
		if tex == null:
			continue
		var segments: Array = d.get("segments", [])
		if segments.is_empty():
			continue
		sf.add_animation(anim_name)
		sf.set_animation_speed(anim_name, float(d.get("fps", 8.0)))
		sf.set_animation_loop(anim_name, bool(d.get("loop", true)))
		for seg in segments:
			var row := int(seg.get("row", 0))
			var start := int(seg.get("start", 0))
			var count := int(seg.get("count", 0))
			for i in range(count):
				var at := AtlasTexture.new()
				at.atlas = tex
				at.region = Rect2((start + i) * _cell, row * _cell, _cell, _cell)
				at.filter_clip = true
				sf.add_frame(anim_name, at)
		built += 1
	if built == 0:
		return
	_frames = sf
	_sprite_ready = true


func _show_missing(text: String) -> void:
	var show_it := not text.is_empty()
	missing_label.visible = show_it
	sprite.visible = not show_it
	if show_it:
		missing_label.text = text


func _on_animation_finished() -> void:
	# Godot 4.7 的 AnimatedSprite2D.animation_finished 不带参数
	if _seq_active and _seq_one_shot:
		_advance_seq()


# ----- 动作序列 -----

func _advance_seq() -> void:
	_seq_index += 1
	if _seq_index >= _seq.size():
		_finish_seq()
		return
	var step: Variant = _seq[_seq_index]
	var clip := ""
	var loops := 1
	if step is Dictionary:
		clip = str(step.get("clip", "read"))
		loops = int(step.get("loops", 1))
	else:
		clip = str(step)
	if not _animations.has(clip):
		_advance_seq()
		return
	current_clip = clip
	sprite.play(clip)
	clip_changed.emit(clip)
	# 走路步：解析位移方向（"h"=随机左右；[x,y]=指定方向），播放期间真实平移窗口
	_seq_move = _resolve_move(step)
	if _seq_move != Vector2.ZERO:
		set_facing(_seq_move)
	_seq_one_shot = not bool(_animations.get(clip, {}).get("loop", true))
	if _seq_one_shot:
		_seq_wait = 0.0
	else:
		_seq_wait = maxf(0.1, _clip_duration(clip) * maxf(1.0, float(loops)))


## 步骤位移标记："h" 随机左右；[x, y] 指定方向；缺省原地
func _resolve_move(step: Dictionary) -> Vector2:
	var mv: Variant = step.get("move", null)
	if mv == null:
		return Vector2.ZERO
	if mv is String and mv == "h":
		return Vector2(-1.0 if _rng.randf() < 0.5 else 1.0, 0.0)
	if mv is Array and (mv as Array).size() == 2:
		return Vector2(float(mv[0]), float(mv[1])).normalized()
	return Vector2.ZERO


func _finish_seq() -> void:
	_seq_active = false
	_seq = []
	_seq_one_shot = false
	_seq_index = -1
	var back := _seq_return
	if _micro_active:
		_micro_active = false
		_micro_timer = 0.0
		_micro_interval = _rng.randf_range(MICRO_MIN, MICRO_MAX)
	play(back)


func _cancel_seq() -> void:
	_seq_active = false
	_seq = []
	_seq_one_shot = false
	_seq_index = -1
	_micro_active = false


func _clip_duration(clip: String) -> float:
	if _frames == null or not _frames.has_animation(clip):
		return 1.0
	var fps := maxf(1.0, _frames.get_animation_speed(clip))
	return float(_frames.get_frame_count(clip)) / fps


func _pick_micro() -> void:
	if MICRO_SCENES.is_empty():
		_micro_timer = 0.0
		_micro_interval = _rng.randf_range(MICRO_MIN, MICRO_MAX)
		return
	var total := 0.0
	for s in MICRO_SCENES:
		total += float(s.get("weight", 1.0))
	var picked_idx := 0
	for attempt in 2:                      # 防连续重演同组：重掷一次
		var r := _rng.randf() * total
		var idx := 0
		for s in MICRO_SCENES:
			r -= float(s.get("weight", 1.0))
			if r <= 0.0:
				break
			idx += 1
		picked_idx = idx
		if idx != _last_scene_idx or attempt == 1:
			break
	_last_scene_idx = picked_idx
	var picked: Dictionary = MICRO_SCENES[picked_idx]
	_micro_timer = 0.0
	play_sequence(picked.get("steps", ["read"]), "idle")
	_micro_active = true
