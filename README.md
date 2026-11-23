# 剪映自动剪辑（Jianying Auto Editor）

一个基于 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) 的命令行工具：
把「同一系列」的视频结构（片头、过渡图、转场、角标、点评样式等）与多段原始录屏组合起来，
**自动生成剪映专业版草稿**，适合批量产出同模板的多集内容。

每集只需要填写：用哪段录屏、从第几秒到第几秒、演唱人标注、点评文字、定格帧与高光 BGM 起点；
其余「每集都一样」的东西统一放在**系列配置**里，换一集不用重写脚本。

> 本仓库只包含**通用工具代码**与**配置字段说明**。
> 不包含任何素材、成品、具体节目内容、字体/花字预设或点评文案。

## 功能

- `analyze`：从参考成片反推模板（节奏、字幕区间、花字样式）
- `template-from-draft`：从「明文草稿」反推模板
- `build`：按模板 + 新素材 + 文案批量生成草稿
- `probe`：从原始录屏找出 DEMO / 舞台 / 高光候选片段
- `series` + `episode` 两级配置：`compose` 装配剪辑清单并生成草稿，`verify` 校验是否符合同系列规则

## 环境

- Python 3.10+
- 依赖见 `requirements.txt`
- 剪映专业版（用于打开生成的草稿、预览、导出）
- 剪映草稿目录默认取 `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`，
  也可用环境变量 `JY_DRAFT_DIR` 指定

## 安装

```bash
pip install -r requirements.txt
python run.py check
```

`run.py check` 会打印工作台路径、剪映草稿目录，以及 pyJianYingDraft / opencv / ffmpeg 是否就绪。

## 配合 AI 编程工具使用

本项目是纯命令行 + JSON 配置的结构，**不依赖任何特定 IDE**，可以直接交给 AI 编程工具驱动：
把仓库目录作为工作区打开，让 AI 帮你写 `series/<系列名>.json` 和 `episodes/<集名>.json`、执行 `compose` / `verify`、
根据报错修正配置即可。字段说明都在本 README 里，AI 可直接读取。

已验证 / 适用的工具：

| 工具 | 使用方式 |
|---|---|
| [Trae](https://www.trae.ai/) | 打开本目录，直接在对话里描述要做的集数与段落，由 AI 生成配置并运行命令（本工具即在此基础上开发） |
| Cursor | 打开目录，用 Agent / Composer 模式读取 README 字段表生成配置 |
| VS Code + GitHub Copilot（Agent 模式） | 同上，让 Copilot 编辑 JSON 并在集成终端执行 `run.py` |
| Claude Code / Codex CLI 等命令行 Agent | 在仓库根目录启动，直接让它按 README 生成配置、跑 `compose` |
| Windsurf、Cline、Roo Code 等 | 打开目录后按同样方式使用 |

几点建议：

- 让 AI 先跑 `python run.py check` 确认环境和草稿目录，再开始写配置。
- 每集都先 `compose --plan-only` 或 `verify`，确认剪辑清单无误再真正写草稿。
- 素材（录屏、相框图、预设原型）需要自己准备并放到工作台目录，AI 只负责配置与命令行调用。

## 目录结构

```
run.py            命令行入口
jys/              工具代码
  cli.py            子命令与参数
  config.py         路径推导（全部从本文件位置推导，可整体搬移）
  analyze.py        从成片反推模板
  draft_reader.py   从明文草稿反推模板
  build.py          按模板生成草稿
  probe.py          录屏候选探测
  series.py         系列配置加载与校验
  episode.py        单集配置加载与校验
  compose.py        系列 + 单集 → 剪辑清单 → 草稿
  plan.py           剪辑清单 → 草稿
  verify.py         剪辑清单/草稿校验
  overlays.py       花字渲染
  presets.py        原生预设文本注入
  media.py          定格、抽音频、朗读
  template.py       模板读写
examples/         配置骨架（仅字段示例，不含真实内容）
```

工作台运行时会自动创建这些数据目录（均在仓库根下，已被 `.gitignore` 忽略）：

```
templates/   clips/   series/   episodes/   assets/   plans/   output/
```

## 基础用法（模板模式）

```bash
# 检查环境
python run.py check

# 从参考成片反推模板（会输出校验截图）
python run.py analyze "参考成片.mp4" --preview

# 按模板生成新草稿（新素材放 clips/ 或 clips/<段落名>/）
python run.py build --template "templates/xxx.json" --script 文案.txt --name 新一期

# 只想看排布、不写草稿
python run.py build --template "templates/xxx.json" --script 文案.txt --name 新一期 --dry-run
```

## 进阶用法（系列 + 单集工作流，推荐）

一条视频 = 一个「系列」下的一个「单集」。

```bash
# 1) 写系列配置 series/<系列名>.json（见下文字段说明）
# 2) 按系列结构生成单集骨架
python run.py episode-new --series <系列名> --name <集名>

# 3) 编辑 episodes/<集名>.json，填素材时间、标注、点评
# 4) 装配并生成剪映草稿
python run.py compose --series <系列名> --episode "episodes/<集名>.json"

# 5) 校验剪辑清单是否符合系列规则
python run.py verify --plan "plans/<集名>.json"
```

`compose` 只生成清单不写草稿可加 `--plan-only`；草稿目录非默认时用 `--draft-root` 指定。

## 段落结构与顺序规则

段落角色（`role`）：`intro`（片头）、`demo`（DEMO/首唱）、`stage`（舞台）、`review`（点评）、`ending`（片尾）。
过渡图由系列配置自动插入到「片头 → DEMO」之间，**不写进单集 segments**。

校验规则（`episode.py`）：

- 第一段必须是 `intro`，最后一段必须是 `ending`
- 必须**恰好一个** `demo`
- `stage` 只能在 `demo` 之后
- 每个 `review` 的前一段必须是 `demo` 或 `stage`
- 舞台数量不限：单舞台、双舞台、多舞台都可，只能增删 `stage`/`review` 配对

## 系列配置字段（`series/<系列名>.json`）

系列描述「每集都一样」的东西：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 系列名 |
| `canvas.width` / `canvas.height` | number 或 `"auto"` | 画布分辨率；`"auto"` 时取第一段录屏的实际分辨率 |
| `canvas.fps` | number | 帧率 |
| `structure` | string[] | 默认段落序列，如 `["intro","demo","review","stage","review","ending"]`；单集可增删舞台 |
| `fixed.intro` | object | 固定片头：`src`（素材）、`start`/`end`（秒）、可选 `speed`（倍速） |
| `fixed.ending` | object | 固定片尾，字段同上 |
| `transition.duration` | number | 片头→DEMO 过渡图时长（1.7–3.0 秒）；抽哪一帧由单集 `transition.frame` 决定 |
| `label.preset` | number | 段前标注使用的预设文本编号 |
| `label.offset` / `label.duration` | number | 标注延后多少秒出现、显示多久 |
| `review.preset` | number | 点评花字/文本使用的预设编号 |
| `review.frame_src` | string | 点评段用的相框/背景图 |
| `review.bgm_volume` | number | 点评高光 BGM 音量 |
| `review.generate_voice` | bool | 是否自动朗读点评；`false` 时朗读留空，由剪映里手动加 |
| `review.voice` / `tail` / `voice_offset` | - | 仅在 `generate_voice=true` 时需要 |
| `presets` | string | 原生预设文本原型 JSON 的路径（从剪映草稿导出的文本轨道原型） |
| `badges` | object[] | 角标：`[{ "preset": 11, "track": "badge_a" }, ...]` |
| `badge_from` / `badge_to` | string | 角标覆盖范围（段落角色名，如 `demo` → `ending`） |
| `transitions` | object | 段落间转场：`{ "demo→review": { "name": "相片切换", "duration": 2.0 }, ... }`；`name` 用 pyJianYingDraft 支持的转场名 |
| `caption_style` | object | 字幕样式（字号/颜色/描边等） |
| `title_style` | object | 标题样式 |

> `presets` 文件本身通常是「从自己的草稿里导出的文本轨道原型」，属于个人素材，本仓库不附带，需要自行生成或替换。

## 单集配置字段（`episodes/<集名>.json`）

单集只填这一集独有的内容：

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | number | 配置版本，当前为 `1` |
| `series` | string | 所属系列名 |
| `name` | string | 单集名 / 草稿名 |
| `transition.frame` | number | 过渡图抽帧秒数（从 DEMO 录屏里取） |
| `segments` | object[] | 段落数组，顺序需符合上面「顺序规则」 |

**`demo` / `stage` 段：**

| 字段 | 说明 |
|---|---|
| `src` | 录屏文件路径（绝对路径原样用；相对路径相对工作台根目录） |
| `start` / `end` | 取用区间（秒） |
| `clips` | 多片段写法：`[{ "src": "...", "start": 0, "end": 0 }, ...]`；给了 `clips` 就忽略扁平的 `src/start/end` |
| `label` | 段前标注文本，如 `"<演唱人>版"` |
| `captions` | 字幕条目数组，元素为 `[起, 止, "文本"]`；留空则不加字幕 |

**`review` 段：**

| 字段 | 说明 |
|---|---|
| `src` | 取素材的录屏（通常与相邻演唱段同一个文件） |
| `frame` | 定格帧秒数，用于生成点评背景图/**必须落在紧邻上一段演唱的 `[start, end]` 区间内** |
| `bgm` | 高光 BGM 起点秒数，**同样必须落在上一段演唱区间内** |
| `duration` | 点评段时长（秒）；`generate_voice=false` 时必填 |
| `text` | 点评文字数组，每行一条 |

> `compose` 会把 `review.frame` / `review.bgm` 钳制到上一段演唱区间并给出警告，建议提前填准。

**`intro` / `ending` 段：** 写成 `{ "role": "intro" }` 即可，素材取自系列 `fixed`；若系列没有固定素材，也可在单集里提供 `src/start/end`。

## examples/

- `examples/series.skeleton.json`：系列配置骨架，演示所有字段
- `examples/episode.skeleton.json`：单集配置骨架（单舞台 / 双舞台两种写法见注释说明）

两个文件都是**结构示意**，数值为占位，不能直接运行；复制后按需填写即可。

## 路径与可移植性

- 所有内部路径都从 `jys/config.py` 位置推导，整个工具目录可自由搬移，不写死盘符。
- 素材路径：绝对路径原样使用；相对路径相对工作台根目录（`run.py` 所在目录）。
- 剪映草稿目录优先读环境变量 `JY_DRAFT_DIR`，否则用默认安装位置。

## 免责声明

本工具仅做「按配置拼装时间轴 + 生成剪映草稿」，不附带、不下载、不分发任何音视频素材。
使用者需自行确保所用素材拥有合法授权。
