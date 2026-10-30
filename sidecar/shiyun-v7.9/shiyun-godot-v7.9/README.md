# 诗韵 · 模型源码包（给 Godot 做桌宠用）

一个 21.5M 参数的字符级古诗模型 + 一只会说话的墨喵（Qwen2.5 + LoRA）。
**不含桌宠程序**——桌宠你自己用 Godot 做，这里提供模型与 HTTP 接口。

---

## 一、能力清单（共 10 项 + 2 道保护）

| # | 能力 | 接口 | 说明 |
|---|---|---|---|
| 1 | **点题成诗** | `/api/theme` | 给一个词（1~4 字），写成四句绝句，关键词必在诗中 |
| 2 | **对下句** | `/api/couplet` | 给上句，出多个候选，按 NLL + 对仗（字级/义类/虚实）排序 |
| 3 | **格律体检** | `/api/check` | 报句式/平仄/押韵违规，出韵给可换字 |
| 4 | **续写整首** | `/api/write` | 给开头续写，可选绝句(2联)/律诗(4联) |
| 5 | **基础对话** | `/api/chat` | 少女音人设：寒暄、被夸被骂、超纲卖萌回避 |
| 6 | **语音合成** | `/api/tts` | 童声/少女音，**念诗自动按联停顿、对句放慢** |
| 7 | **记忆** | `/api/memory` | 可以给它起名、记你的称呼与偏好，重启不丢 |
| 8 | **诵古诗原文** | `/api/recite` | 按题目/作者/诗句找到原诗并照读——**古籍一字不改** |
| 9 | **古诗搜索** | `/api/search` | 29.4 万首唐宋诗（带作者题目），搜题目/作者/诗句 |
| 10 | **语音识别** | `/api/asr/start`、`/api/asr/stop` | 按住说话；faster-whisper small / CPU int8，全程本机，不联网 |

**诗库规模**：29.4 万首（唐 + 宋），带标题与作者，繁转简。
注意古诗**多异文**：库里《静夜思》是宋代原版「床前**看**月光…举头望**山**月」，
课本通行版「床前明月光」通过别名表也能检索到。

**两道内容保护（重要）**
- **名句不改**：拿「空山新雨后」这类成句来对句/续写会被拦下，并告以先贤原对
- **输出查重**：生成句与 37 万首诗比对，整句撞或**只改一个字**都算脏，自动换种子重生成

---

## 二、快速开始

```bash
# 1) 依赖（只需这些）
pip install torch edge-tts pillow
#   语音识别另需：pip install faster-whisper sounddevice
#   另需系统里有 ffmpeg（语音合成转 wav 用）

# 2) 拉对话底座（约 1GB 或 3GB，二选一）
python models/download_base.py --size 0.5b     # 省显存，CPU 也能跑
python models/download_base.py --size 1.5b     # 质量更好（默认）

# 3) 起服务
python serve.py                                 # 默认 127.0.0.1:8100
python serve.py --no-chat                       # 只起诗模型（省 3GB 显存）
#    想查重更严 / 用完整语料：
python verify.py -t "你的诗"                   # 发布前深度核验（不漏改字）

python serve.py --chat-model models/Qwen2.5-0.5B-Instruct \
                --adapter models/mo-miao-1.5b   # 换 0.5B 底座（adapter 需对应底座）

# 4) 验证
curl http://127.0.0.1:8100/api/health
```

首次启动会加载：诗模型（83MB，约 2 秒）+ 对话模型（LoRA 47MB + 底座）。

---

## 三、Godot 接入

1. 把 `godot_example.gd` 复制进项目，挂到桌宠根节点
2. 调 `await wait_ready()` 等服务起来
3. 常用调用：

```gdscript
await chat("你好呀")                      # 闲聊，返回 {reply, tool, poem}
await theme("孤舟")                        # 点题作诗
await couplet("小院灯初上")                 # 对下句
await check("床前明月光，疑是地上霜。")        # 体检
await speak("小院灯初上，闲阶月正明。")        # 念出来
```

桌宠行为（动画/表情）你自己接 `replied`、`spoke` 信号即可。
（截屏/看屏幕能力已移除，示例脚本里不再有 `poem_about_screen()`。）

> 接口细节见 `API.md`。换 Unity 也一样，把 HTTPRequest 换成 UnityWebRequest。

---

## 四、文件说明

```
serve.py                 ← 服务入口（Godot 调它）
API.md                   ← 接口文档
godot_example.gd         ← Godot 4.x 示例（含 wav 解码）
selftest.py              ← 17 条自检（改代码后跑这个）

model.py  constraints.py  prosody.py  duizhang.py  pos.py
theme.py  couplet.py  checker.py  generate.py  poem.py
gushi_model.pt           ← 诗模型权重（21.5M）
rhyme_table.json  tone_table.json  duizhang_table.json  duizhang_class.json

poems.jsonl.gz            ← 诗库 29.4 万首（33MB，搜索/诵诗用）
corpus_clauses.txt        ← 210 万条诗句（40MB，查重用）
corpus.txt                ← 原始语料（67MB，仅 --rag/重建索引用，可删）

chat/
  library.py   诗库：搜诗 + 诵原文
  persona.py   人设 prompt + 台词
  router.py    意图路由（规则，不走 function calling）
  engine.py    Qwen 推理
  service.py   对话总入口（闲聊 ↔ 工具）
  memory.py    记忆（起名/称呼/偏好）
  tts.py       语音（按联合成 + 气调链）
  classics.py  古文保护（名句拦截 + 原创性校验）

models/
  download_base.py       ← 拉对话底座
  mo-miao-1.5b/          ← LoRA adapter（47MB，需配 1.5B 底座）
```

---

## 五、已知边界（不夸大）

| 项 | 现状 |
|---|---|
| **视觉** | 已整体移除：不截屏、不看屏幕（隐私 + 能耗） |
| **对话** | 1.5B 底座 + LoRA，能寒暄、能拒绝超纲（"吾一窍不通"）；多轮 3 轮后开始重复 |
| **诗歌质量** | 主推「对一句」「体检」；整首诗的意境连贯是 21M 参数的天花板 |
| **语音** | 合成需联网（edge-tts）+ ffmpeg；断网时只回文字 |
| **语音识别** | small 模型 / CPU int8，安静环境下普通话还行；远场、口音重、背景吵会听错；静音会被过滤成空文本 |
| **查重** | 运行时用 4-gram 快筛（改在句子中间的会漏，实测 72%）；<br>`verify.py` 用枚举替换做**不漏**的深度核验（70ms/句，发布前跑） |
| **显存** | 诗模型 + 1.5B 对话约 4GB；`--no-chat` 则只要 1GB |

---

## 六、许可

MIT。语料来自 [chinese-poetry](https://github.com/chinese-poetry/chinese-poetry)，其许可以原项目为准。
**使用时请遵守**：先贤诗文只可照读，不可改写；生成内容发布前请自行核验原创性。
