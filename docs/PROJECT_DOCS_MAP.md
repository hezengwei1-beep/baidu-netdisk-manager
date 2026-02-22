# Cloud Code 项目文档地图（百度网盘视频转逐字稿 + 多Agent萃取）

更新时间: 2026-02-22

## 1. 项目定位与检索结论

全量检索命中的主项目为：
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager`

它满足你描述的完整链路：
- 百度网盘视频/音频 -> 逐字稿（SRT/TXT）
- 基于 Cloud Code 历史萃取做对照
- 多 Agent 梳理与二次精修

同目录下还存在一个相关但更轻量的实验项目：
- `/Users/mac/Projects/experiments/my-project/video-to-text`
- 主要是“下载+转写”，不包含你说的“Cloud Code 历史萃取 + 多 agent 知识加工”闭环。

## 2. 文档分层（按阅读优先级）

### A. 先看（总览）

1. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/README.md`
- 最基础使用入口与命令概览。

2. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/PROJECT_REPORT.md`
- 系统分层、命令面、工作原理、执行流程建议。

3. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/PROJECT_ARCHITECTURE.md`
- Mermaid 架构图 + 数据流图 + 流程图。

4. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/transcription_plan.md`
- 转写规模评估、优先级与批次计划。

### B. 再看（与你当前目标最相关）

1. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/data/knowledge/AI-写文工作流-从逐字稿到可发布文章.md`
- 逐字稿 -> 可发布文章的内容生产工作流。

2. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/data/knowledge/国学-菩提道次第.md`
- 菩提道知识汇总长文（当前产物中的典型代表）。

3. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/data/subtitles/A学科库/国学/2024吴/菩提道（视）/多Agent梳理/00_总览_多Agent梳理.md`
- 多 Agent 第一轮梳理总览。

4. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/data/subtitles/A学科库/国学/2024吴/菩提道（视）/多Agent梳理/二次精修/00_批次报告_二次精修.md`
- 二次精修批次总览。

5. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/data/subtitles/A学科库/国学/2024吴/菩提道（视）/多Agent梳理/二次精修/00_术语词典_标准化.md`
- 精修阶段的统一术语口径。

6. `/Users/mac/Projects/active-projects/baidu-netdisk-manager/data/subtitles/A学科库/国学/2024吴/菩提道（视）/多Agent梳理/二次精修/00_跨讲主题地图.md`
- 跨讲知识串联视图。

### C. 对照历史 Cloud Code 结果

- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/data/subtitles/A学科库/国学/24菩提道/知识萃取`
- 这里是 Cloud Code 的历史萃取资产，供多Agent对照与补全。

## 3. 关键脚本与它们对应的文档

1. `subtitle_extractor.py`
- 对应: 浏览器态字幕提取与导入。

2. `audio_transcript.py`
- 对应: 音频逐字稿提取（在线/本地 whisper）。

3. `whisper_transcribe.py`
- 对应: M3U8 批量离线转写。

4. `multi_agent_bodhi_pipeline.py`
- 对应: 多 Agent 一轮梳理。

5. `refine_bodhi_analysis.py`
- 对应: 二次精修与主题地图。

## 4. 当前产物规模快照（2026-02-22）

- 根文档（项目根目录 `.md`）: 4
- 菩提道逐字稿 `.txt`: 38
- 菩提道字幕 `.srt`: 38
- 多Agent逐讲文档: 38
- Agent 定义文档: 10
- 二次精修逐讲文档: 38
- Cloud 历史萃取文档: 20
- Cloud 二次精修同步文档: 20
- `data/knowledge` 文档: 58

## 5. 推荐使用顺序（从“找到”到“复用”）

1. 先读 `PROJECT_REPORT.md` 和 `PROJECT_ARCHITECTURE.md`，建立系统模型。
2. 进入 `multi_agent_bodhi_pipeline.py` 和 `refine_bodhi_analysis.py` 看加工逻辑。
3. 在 `多Agent梳理/00_总览_多Agent梳理.md` 看输出覆盖率。
4. 在 `二次精修/00_跨讲主题地图.md` 看复用价值。
5. 最后对照 `AI-写文工作流-从逐字稿到可发布文章.md`，把内容产出链路跑通。

## 6. 一条可执行的端到端链路（菩提道示例）

1. 逐字稿准备: `subtitle_extractor.py` 或 `audio_transcript.py` 产出 `.srt + .txt`
2. 一轮加工: 运行 `multi_agent_bodhi_pipeline.py`
3. 二轮加工: 运行 `refine_bodhi_analysis.py`
4. 内容发布: 使用 `data/knowledge/AI-写文工作流-从逐字稿到可发布文章.md` 的模板二次创作

