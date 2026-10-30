extends SceneTree

var _panel = null

func _initialize() -> void:
	var scene: PackedScene = load("res://scenes/components/InputPanel.tscn")
	var bubble_scene: PackedScene = load("res://scenes/components/Bubble.tscn")
	if scene == null or bubble_scene == null:
		printerr("SMOKE FAIL: 场景加载失败")
		quit(1)
		return
	_panel = scene.instantiate()
	root.add_child(_panel)
	var bubble = bubble_scene.instantiate()
	root.add_child(bubble)
	process_frame.connect(func():
		var ok: bool = _panel.refresh_button != null and _panel.send_button != null
		_panel.set_model_ready(true, true)
		_panel.open("theme")
		_panel.editor.text = "孤舟"
		_panel._submit()
		_panel.show_result("点题·五绝", "测试诗句")
		var send_ok: bool = ok and _panel.refresh_button.visible
		bubble.show_text("冒烟", 1.0)
		var tail_ok: bool = bubble.get_node_or_null("Tail") != null
		print("SMOKE %s: send=%s refresh=%s bubble_tail=%s" %
			["OK" if (send_ok and tail_ok) else "FAIL", str(ok), str(send_ok), str(tail_ok)])
		quit(0 if (send_ok and tail_ok) else 1))
