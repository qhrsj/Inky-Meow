# 墨喵状态机（M2）

实现：`scripts/pet_state_machine.gd`（纯逻辑 RefCounted，由 `scripts/main.gd` 每帧 `tick(delta)`）。
角色渲染：`scripts/character_view.gd`（雌版 SpriteFrames）。

## 状态 → 精灵 clip

| 状态 | 精灵 clip | 动作 |
|---|---|---|
| IDLE | read | 翻书 |
| THINKING | poem | 吟诗作对 |
| HAPPY | happy | 开心 |
| CONFUSED | confused | 疑惑 |
| SLEEPY | sleep | 睡着 |
| EASTER | chase | 追蝶 |
| DRAGGING | run_front | 正面跑 |

`manifest.json` 的 `state_map` 保存该映射；空闲时 `IdleDirector`（CharacterView 内）会按权重切换吹笛/左顾右盼/打哈欠/写诗等微行为，**不改变 FSM 状态**。

## 转移表

| 当前 | 事件 | 下一状态 | 说明 |
|---|---|---|---|
| IDLE | 左键单击 | HAPPY | 摸头反应 |
| IDLE | 发起请求 | THINKING | 启动命令级看门狗 |
| IDLE | 闲置 300s | SLEEPY | 面板打开/拖动/隐藏时暂停 |
| THINKING | 请求成功 | HAPPY | 迟到结果用 token 丢弃 |
| THINKING | 请求失败/超时 | CONFUSED | 面板/气泡显示原因 |
| THINKING | 看门狗 15–60s | CONFUSED | 先于 5 分钟闲置触发 |
| HAPPY | 2.2s | IDLE | |
| CONFUSED | 3.0s | IDLE | |
| SLEEPY | 任意点击/拖拽/键盘 | IDLE（点击可为 HAPPY） | 先唤醒 |
| SLEEPY | 再闲置 600s（累计 15min） | EASTER | |
| EASTER | 任意输入 | IDLE | |
| EASTER | 8s 无输入 | IDLE | 彩蛋回落 |
| 任意（面板外） | 移动 >6px | DRAGGING | 暂停闲置计时 |
| DRAGGING | 松手 | 延迟结果 > 请求中 → THINKING > 否则 IDLE | 恢复穿透 |

## 计时与并发

- 累积 `dt`，`debug_advance(seconds)` 以 0.05s 步进。
- 请求令牌：`begin_request(cmd)` 返回 id；`finish_request` 校验 id/active；迟到响应丢弃。
- 看门狗：theme 60s、couplet 30s、check 15s、write 30s、chat 60s；HTTP 客户端超时更短 3–5s。
- 面板打开、拖动、隐藏到托盘时暂停闲置；关闭/松手/显示后重置。
- 拖动中到达的结果 deferred，松手后应用。

## 记忆 / 朗读

- 记忆、TTS 由服务端与 `VoicePlayer` 处理，不影响状态转移；请求仍受 token 保护。
- 截屏/看屏幕能力已移除（隐私 + 能耗），FSM 里没有任何与屏幕相关的触发。

## 调试

```bash
godot --path . -- --fast-idle --debug-polygon
```

游戏内：

```gdscript
var m = get_tree().current_scene
m.sm.debug_advance(301.0)   # -> sleepy
m.sm.debug_advance(601.0)   # -> easter
m.sm.notify_user_input()    # -> idle
```

## 测试

`tests/test_pet_state_machine.gd` 16 条（含 chat 60s 看门狗）；全套 46 条。
