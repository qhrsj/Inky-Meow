# Inky Meow 发布指南（在你自己的终端执行，任意 PowerShell / CMD）

本目录已是一个完整的 git 仓库（已提交，main 分支，489 个文件）。
只差「创建 GitHub 仓库 + 推送」两步，任选下面一种方式：

---

## 方式 A：GitHub CLI（推荐）

```powershell
# 1. 安装 GitHub CLI（或去 https://cli.github.com 下载安装包）
winget install --id GitHub.cli -e

# 2. 登录（会给你一个 8 位码 + 打开浏览器，粘贴确认即可）
gh auth login --hostname github.com --git-protocol https --web

# 3. 创建公开仓库并推送（在 D:\AIProject\inky-meow 目录下执行）
cd D:\AIProject\inky-meow
gh repo create inky-meow --public --source . --push --description "墨喵 Inky Meow · 会写格律诗的水墨小灵猫桌面宠物 | Godot + Qwen3 本地推理"

# 4. （可选）把 docs/gifs 里的动图换到 Release 附件，或编辑 README 后：
#    git add -A ; git commit -m "docs" ; git push
```

---

## 方式 B：手动（不用 gh）

1. 打开 https://github.com/new ，仓库名填 `inky-meow`，选 **Public**，**不要**勾选任何初始化选项（README/.gitignore/LICENSE 都别勾），点 Create
2. 回到本目录执行：

```powershell
cd D:\AIProject\inky-meow
git remote add origin https://github.com/你的用户名/inky-meow.git
git push -u origin main
```

推送时弹出 GitHub 登录窗口 → 用浏览器授权；或使用 Personal Access Token（密码处粘贴 Token）。

---

## 推送之后

- 仓库约 240MB（含 83MB 诗模型 + 34MB 语料，克隆即可用，无需额外下载大文件）
- 建议补一发 Release：Releases → Draft a new release → 标签 v1.0 →
  把 `D:\AIProject\墨喵桌宠\发布\墨喵桌宠-好友版` 压缩成 zip 传上去
  （不想传 4.8GB 的话，只传 墨喵.exe + 使用说明.txt 也行，模型让用户跑 install_deps + 下载脚本）
- README 里的仓库地址占位符记得替换：`你的用户名`

---

## 仓库内容速览

```
├── 墨喵桌宠/          Godot 4.7 主程序（状态机/动作编排/UI/测试 77 项）
├── sidecar/           Python AI 后端（诗引擎 21.5M + Qwen3 闲聊 + whisper 语音）
│   └── .../models/download_qwen3.py   对话模型一键下载（魔搭镜像）
├── docs/gifs/         README 演示动图
├── LICENSE            MIT（第三方模型各随其主：Qwen3=Apache-2.0）
└── README.md          已写好功能/架构/快速开始/模型说明
```

已剔除（不应入库）：模型权重（下载脚本替代）、训练语料 corpus.txt、
__pycache__、测试产物、原素材画稿。
