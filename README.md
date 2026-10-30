# Inky Meow · 墨喵桌宠 🐾

<p align="center">
  <img src="docs/gifs/侧视吹笛走.gif" width="160" alt="吹笛闲逛"/>
  <img src="docs/gifs/偷吃小鱼干.gif" width="160" alt="偷吃小鱼干"/>
  <img src="docs/gifs/开心.gif" width="160" alt="开心"/>
  <img src="docs/gifs/趴书堆睡.gif" width="160" alt="趴书堆睡"/>
</p>

**墨喵**是一只水墨古风的小灵猫桌面宠物（Windows）：会写格律诗、陪你闲聊、玩飞花令、
番茄钟专注陪伴，还会自己**在桌面上溜达**——追蝴蝶摔一跤爬起来，逛累了打哈欠、
坐下看书、趴在书堆上睡着。

Godot 4.7 主程序 + Python AI sidecar，全部**本地推理**，不上传任何数据。

## ✨ 功能

- **点题成诗**：自研 21.5M 格律诗引擎，五言/七言绝句律诗自动选体，题字藏于句中
- **对句 / 续写 / 格律体检**：平仄、押韵、句式逐项报告；古诗名句拒绝乱对（防伪作）
- **闲聊**：Qwen3-1.7B 本地对话 + 意图路由 + 记忆系统（记住你的称呼和喜好）
- **飞花令**：`飞花令，月` 开局，29 万首诗库里轮流接句
- **语音**：语音输入（faster-whisper）+ TTS 朗读
- **番茄钟专注陪伴**：25 分钟专注，墨喵安静陪读，气泡倒计时
- **桌面闲逛**：四方向真走位；累了打哈欠 → 看书 → 趴书堆睡 → 伸懒腰醒来
- **19 个动作微剧场**：偷吃小鱼干、追蝶、傲娇、跌倒起身、生气……随机编排
- **诗笺收藏**：满意的诗一键收藏，随时翻阅

## 🖼 更多演示

<p align="center">
  <img src="docs/gifs/追蝶.gif" width="200" alt="追蝶"/>
  <img src="docs/gifs/趴书堆睡.gif" width="200" alt="趴书堆睡"/>
</p>

## 🚀 快速开始

### 方式一：下载现成的 exe（推荐）

到 [Releases](../../releases) 下载 `墨喵桌宠-好友版`，解压后：

1. 安装 [Python 3.10/3.11](https://www.python.org/downloads/)（勾选 Add to PATH）
2. 双击 `install_deps.bat`（一次性装 AI 依赖）
3. 双击 `墨喵.exe`，等气泡显示「全部就绪」

### 方式二：从源码运行

```bash
git clone https://github.com/你的用户名/inky-meow.git
cd inky-meow

# 1) 安装 AI 依赖（建议 Python 3.10/3.11）
pip install -r sidecar/shiyun-v7.9/shiyun-godot-v7.9/requirements.txt

# 2) 下载模型（对话底座 4GB，走国内魔搭镜像）
python sidecar/shiyun-v7.9/shiyun-godot-v7.9/models/download_qwen3.py
# 语音识别模型（约 464MB，走 HuggingFace）
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small', local_dir='sidecar/shiyun-v7.9/shiyun-godot-v7.9/models/faster-whisper-small')"

# 3) 启动 AI 后端
python sidecar/launcher/start_server.py --port 8100

# 4) 用 Godot 4.7 打开 墨喵桌宠/project.godot，按 F5
```

> 打包 exe：Godot 编辑器 → 项目 → 导出 → 「Windows Desktop」预设。

## 🧠 架构

```
┌─────────────────────┐   HTTP(127.0.0.1:8100)   ┌──────────────────────┐
│  Godot 主程序        │ ◄──────────────────────► │  Python sidecar      │
│  · 透明窗口/鼠标穿透  │      JSON API            │  · 格律诗引擎 21.5M   │
│  · 状态机/动作编排    │  /api/chat               │  · Qwen3-1.7B 闲聊   │
│  · 气泡/面板/菜单    │  /api/theme  /api/check  │  · faster-whisper ASR│
│  · 桌面闲逛/番茄钟    │  /api/couplet /api/asr…  │  · Edge TTS          │
└─────────────────────┘                          └──────────────────────┘
```

- 主状态机 7 态 + 统一调度互斥（反馈独占 / 闲逛与微剧场二选一 / 睡眠链唯一）
- 19 个动作全部支持编排：动画清单与帧数据见 `墨喵桌宠/assets/sprites/manifest.json`
- 诗歌输出永不过 LLM 改写；古诗原文一字不改（红线设计）

详细的行为设计（状态机转换表、动作编排、暂停门控）见
[`墨喵桌宠/docs/HANDOVER.md`](墨喵桌宠/docs/HANDOVER.md)。

## 📦 模型说明

| 模型 | 体积 | 用途 | 获取 |
|---|---|---|---|
| Qwen3-1.7B | 3.8GB | 闲聊对话 | `models/download_qwen3.py`（魔搭镜像） |
| faster-whisper-small | 464MB | 语音识别 | HuggingFace（命令见上） |
| gushi_model.pt | 83MB | 格律诗引擎 | **仓库内含**，开箱即用 |
| poems.jsonl.gz | 34MB | 29 万首古诗语料 | **仓库内含** |

## 📄 License

[MIT](LICENSE)。第三方模型（Qwen3 / faster-whisper）遵循其各自许可；
诗歌语料整理自公版古籍，作品本身均为公有领域。

## 🙏 致谢

- [Godot Engine](https://godotengine.org/)
- [Qwen（通义千问）](https://github.com/QwenLM/Qwen3)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Edge TTS](https://github.com/rany2/edge-tts)
