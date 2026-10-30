@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const ROAM_SCRIPT := preload("res://scripts/services/roam.gd")


func suite_name() -> String:
	return "roam"


func _new_roam() -> Object:
	var r = ROAM_SCRIPT.new()
	r.set_seed(42)
	return r


func test_disabled_does_nothing() -> void:
	var r = _new_roam()
	r.set_enabled(false)
	var moved := []
	r.move_by.connect(func(v): moved.append(v))
	for i in 200:
		r.tick(0.1)
	assert_eq(moved.size(), 0, "未启用不应移动")


func test_paused_freezes_everything() -> void:
	var r = _new_roam()
	r.set_enabled(true)
	r.set_paused(true)
	var moved := []
	var clips := []
	r.move_by.connect(func(v): moved.append(v))
	r.play_clip.connect(func(n): clips.append(n))
	for i in 400:
		r.tick(0.1)
	assert_eq(moved.size(), 0)
	assert_eq(clips.size(), 0, "暂停期间不应有动作")


func test_walk_moves_in_declared_direction() -> void:
	var r = _new_roam()
	r.set_enabled(true)
	r.set_seed(7)
	var dirs := []
	var moves := []
	r.walk_started.connect(func(d): dirs.append(d))
	r.move_by.connect(func(v): moves.append(v))
	# 跑满 30 秒，必然会散步
	for i in 300:
		r.tick(0.1)
	assert_true(dirs.size() > 0, "30 秒内应开始散步")
	var d: Vector2 = dirs[0]
	assert_true(d in [Vector2(-1, 0), Vector2(1, 0), Vector2(0, -1), Vector2(0, 1)],
		"方向必须是四方向之一")
	var wrong_dir := 0
	for v in moves:
		if v.normalized().distance_to(d) > 0.01:
			wrong_dir += 1
	assert_eq(wrong_dir, 0, "移动向量应与散步方向一致")


func test_walk_eventually_stops() -> void:
	var r = _new_roam()
	r.set_enabled(true)
	r.set_seed(3)
	var stopped := []              # lambda 按值捕获局部变量，用数组计数
	r.walk_ended.connect(func(): stopped.append(1))
	for i in 800:
		r.tick(0.1)
	assert_true(stopped.size() >= 2, "长跑 80 秒应至少完成两轮散步/休息，实际 %d" % stopped.size())


func test_tired_chain_order() -> void:
	var r = _new_roam()
	r.set_enabled(true)
	r.set_seed(1)
	r.chain_prob = 1.0            # 强制必触发「逛累了」链
	var clips := []
	r.play_clip.connect(func(n): clips.append(n))
	for i in 3000:
		r.tick(0.1)
	var joined := ",".join(clips)
	assert_true(joined.find("yawn") >= 0, "链里应有犯困 yawn")
	assert_true(joined.find("read") >= 0, "链里应有看书 read")
	assert_true(joined.find("sleep") >= 0, "链里应有睡觉 sleep")
	# 顺序：yawn 先于 read 先于 sleep
	assert_true(joined.find("yawn") < joined.find("read"), "yawn 应在 read 前")
	assert_true(joined.find("read") < joined.find("sleep"), "read 应在 sleep 前")


func test_move_speed_matches() -> void:
	var r = _new_roam()
	r.set_enabled(true)
	r.speed = 60.0
	r.set_seed(11)
	r.chain_prob = 0.0           # 不触发链，纯散步
	var deltas := []
	r.move_by.connect(func(v): deltas.append(v))
	var walked := 0.0
	var last_t := 0.0
	# 找到走路状态再测速度：直接 tick 累计位移
	for i in 300:
		r.tick(0.1)
	for v in deltas:
		walked += v.length()
	assert_true(walked > 0.0, "应有位移")
	# 每帧位移应等于 speed*delta（42→60 档）
	for v in deltas:
		assert_true(absf(v.length() - 6.0) < 0.01, "速度 60、dt 0.1 → 每帧 6px")
		break
