class_name RoamController
extends RefCounted

## 桌面闲逛状态机（纯逻辑，无节点依赖，便于单测）：
##   休息 → 四向散步 → 休息 → … →（概率）逛累了：
##   打哈欠 → 坐下看书 → 趴书堆睡 → 打哈欠醒 → 继续逛
##
## 通过信号对外请求：移动窗口、播动画、停止动画。
## 上/下方向用背面跑/正面跑帧替代（素材无竖直行走帧）。

signal move_by(vec: Vector2)          # 请求把窗口平移 delta
signal walk_started(dir: Vector2)     # 开始朝 dir 走（dir 为四方向单位向量）
signal walk_ended()                   # 本次散步结束（角色原地站定）
signal play_clip(name: String)        # 请求播放动画（链式动作/原地站立）

enum State { REST, WALK, CHAIN }

const DIRS := [Vector2(-1, 0), Vector2(1, 0), Vector2(0, -1), Vector2(0, 1)]

var enabled := false
var paused := false
var speed := 42.0                     # 桌面像素/秒
var walk_time := [2.0, 5.0]           # 每次散步时长区间
var rest_time := [4.0, 11.0]          # 每次休息时长区间
var chain_prob := 0.35                # 休息后触发「逛累了」链的概率

# 链式动作：犯困 → 坐下看书 → 趴书堆睡 → 伸懒腰醒
const CHAIN_STEPS := [
	{"clip": "yawn", "dur": 2.4},
	{"clip": "read", "dur": 10.0},
	{"clip": "sleep", "dur": 18.0},
	{"clip": "stretch", "dur": 2.6},
]

var state: int = State.REST
var dir := Vector2(-1, 0)

var _t := 0.0
var _chain_index := -1
var _rng := RandomNumberGenerator.new()


func _init() -> void:
	_rng.randomize()


func set_enabled(on: bool) -> void:
	enabled = on
	paused = false
	state = State.REST
	_t = _rand(rest_time)
	_chain_index = -1
	if not on:
		walk_ended.emit()


func set_paused(on: bool) -> void:
	paused = on


## 是否在主动移动/演累了链（二选一规则：此时微剧场让位）
func is_roaming() -> bool:
	return enabled and state != State.REST


func set_seed(seed_value: int) -> void:
	_rng.seed = seed_value


func tick(delta: float) -> void:
	if not enabled or paused:
		return
	match state:
		State.REST:
			_t -= delta
			if _t <= 0.0:
				if _rng.randf() < chain_prob:
					_start_chain()
				else:
					_start_walk()
		State.WALK:
			_t -= delta
			move_by.emit(dir * speed * delta)
			if _t <= 0.0:
				_stop_walk()
		State.CHAIN:
			_t -= delta
			if _t <= 0.0:
				_advance_chain()


func _start_walk() -> void:
	state = State.WALK
	dir = DIRS[_rng.randi_range(0, DIRS.size() - 1)]
	_t = _rand(walk_time)
	walk_started.emit(dir)


func _stop_walk() -> void:
	state = State.REST
	_t = _rand(rest_time)
	walk_ended.emit()
	play_clip.emit("idle")


func _start_chain() -> void:
	state = State.CHAIN
	_chain_index = -1
	walk_ended.emit()
	_advance_chain()


func _advance_chain() -> void:
	_chain_index += 1
	if _chain_index >= CHAIN_STEPS.size():
		state = State.REST
		_chain_index = -1
		_t = _rand(rest_time)
		play_clip.emit("idle")
		return
	var step: Dictionary = CHAIN_STEPS[_chain_index]
	_t = float(step.get("dur", 3.0))
	play_clip.emit(str(step.get("clip", "yawn")))


func _rand(range_arr: Array) -> float:
	return _rng.randf_range(float(range_arr[0]), float(range_arr[1]))
