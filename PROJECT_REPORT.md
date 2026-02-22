# baidu-netdisk-manager 项目报告

生成时间：2026-02-19  
报告目标：说明项目结构与核心工作原理，便于后续迭代和运维。

## 1. 项目定位

这是一个以 **百度网盘内容治理 + 逐字稿生产 + 知识沉淀** 为核心的本地化自动化项目。

主要能力分三层：

1. 网盘资产层：扫描、索引、清理、去重、同步、分类迁移。
2. 逐字稿生产层：通过浏览器端 API 脚本 + 本地导入，批量拉取字幕/音频转写。
3. 知识加工层：将字幕文本沉淀为知识稿，并可进一步做多 Agent 分析与专题精修。

## 2. 项目结构（关键）

### 2.1 代码入口

- `manager.py`：主 CLI，统一调度网盘管理、分类、迁移、清理、去重、同步。
- `audio_transcript.py`：音频逐字稿管线（在线听记 + 本地 Whisper）。
- `subtitle_extractor.py`：视频/音频字幕抓取（浏览器上下文抓取 SRT/M3U8）。
- `whisper_transcribe.py`：M3U8 -> ffmpeg -> faster-whisper 的离线批转写。
- `munger_agent.py`：独立的“芒格能力全集”决策 Agent（run/chat）。

### 2.2 支撑模块

- `auth.py`：OAuth 授权、token 刷新。
- `api.py`：百度网盘 API 封装（list/search/filemanager/upload/download 等）。
- `db.py`：SQLite 索引与迁移日志、分类结果持久化。
- `taxonomy.py`：分类树定义与验证。
- `classifier.py`：三级规则分类引擎（映射 -> 关键词 -> 内容分析）。
- `migration.py`：四阶段迁移执行 + 回滚。
- `cleaner.py`：空间清理报告与执行。
- `dedup.py`：保守去重策略（safe/review/manual）。
- `organizer.py`：按规则（日期/关键词/扩展名）整理。
- `sync.py`：本地/网盘单向备份。

### 2.3 数据目录

- `data/index.db`：核心本地索引库（WAL 模式）。
- `data/subtitles/`：字幕与纯文本产物（当前约 9942 文件）。
- `data/knowledge/`：知识萃取文稿（当前约 58 文件）。
- `data/*progress*.json`：任务进度状态。
- `data/*urls*.json`：浏览器端生成的 URL 批任务清单。

## 3. 命令面（实际可执行）

### 3.1 主入口

`python manager.py --help` 对应命令：

- `auth`、`scan`、`info`
- `organize`、`classify`、`taxonomy`
- `migrate`、`clean`、`dedup`、`sync`

### 3.2 逐字稿入口

`python audio_transcript.py --help`：

- `list-remote`、`generate-js`、`import-results`
- `stats`、`reset-failed`
- `whisper-transcribe`

`python subtitle_extractor.py --help`：

- `generate-js`、`generate-m3u8-js`、`import-results`
- `stats`、`audio-stats`、`reset-failed`

### 3.3 决策 Agent 入口

`python munger_agent.py --help`：

- `run`（结构化分析输出备忘录）
- `chat`（交互会话）

## 4. 核心工作原理

## 4.1 网盘索引与治理原理

1. 认证：`auth.py` 维护 access_token/refresh_token。
2. 扫描：`manager scan` 调用 `api.list_all()`，超限时回退 `walk_dir()`。
3. 元信息补齐：分批调用 `file_meta()` 获取 MD5 等字段。
4. 落库：`db.batch_upsert()` 写入 `files` 表。
5. 治理：
   - 清理：重复/过期/空目录分析后执行删除。
   - 去重：按风险级别 safe/review/manual 分层。
   - 同步：本地与网盘按 size+md5 对比增量同步。

## 4.2 分类与迁移原理

1. 分类：`classifier.py`
   - 规则1：`directory_mappings` 精确/前缀映射（高置信）。
   - 规则2：taxonomy 关键词匹配（中置信）。
   - 规则3：扩展名/内容特征推断（低置信）。
2. 迁移：`migration.py` 四阶段
   - 阶段1：创建目标目录结构。
   - 阶段2：高置信度自动迁移。
   - 阶段3：中低置信度交互审核迁移。
   - 阶段4：清理空旧目录。
3. 回滚：按 `migration_log` 批次可回滚或全量回滚。

## 4.3 逐字稿生产原理

项目采用 **“浏览器端抓取 + 本地导入”** 的双段式设计：

1. 浏览器端脚本阶段：
   - 在登录态页面调用网盘内部接口（包含 HTTP-only Cookie 上下文）。
   - 生成 SRT 或 M3U8 结果（避免 OAuth token 权限不足）。
2. 本地导入阶段：
   - `import-results` 将结果保存为 `.srt + .txt`。
   - 记录到 progress JSON（completed/failed/not_transcoded 等）。
3. 音频补链路：
   - `whisper-transcribe` 支持本地 faster-whisper，不依赖在线额度。
   - `whisper_transcribe.py` 支持 M3U8 流式解码后离线转写。

这套设计的关键是：**把“受限认证动作”放在浏览器上下文，把“可重复加工动作”放在本地脚本。**

## 4.4 知识加工原理

在 `data/subtitles/` 形成文本后，继续沉淀到 `data/knowledge/`。  
近期新增了菩提道专题流水线：

- `multi_agent_bodhi_pipeline.py`：10 Agent 全量梳理。
- `refine_bodhi_analysis.py`：二次精修、术语词典、跨讲主题地图。

## 5. 数据模型（SQLite）

核心表：

- `files`：文件索引主表（path/md5/size/mtime/extension/parent_dir）。
- `scan_log`：扫描批次记录。
- `classifications`：分类建议与置信度。
- `migration_log`：迁移执行与回滚依据。

关键索引：

- `idx_files_path`、`idx_files_md5`、`idx_files_ext`、`idx_files_parent`。

## 6. 配置模型（config.yaml）

顶层配置键：

- `auth`、`scan`、`organize`、`clean`、`dedup`、`sync`
- `classifier`、`migration`、`taxonomy`

当前 taxonomy：

- 根分类约 10 个（健康运动/语言学习/摄影影像/商业财经/人文社科/通用技能/学习平台/个人空间/系统数据/待归类）
- 总节点约 45 个。

## 7. 建议的标准执行流程

1. `python manager.py auth`
2. `python manager.py scan`
3. 逐字稿生成（`subtitle_extractor.py` 或 `audio_transcript.py`）
4. `import-results` 写入 `data/subtitles/`
5. 知识萃取写入 `data/knowledge/`
6. `python manager.py classify`
7. `python manager.py migrate --plan` -> 分阶段执行
8. `python manager.py dedup --report` / `clean --report`
9. `python manager.py sync --up|--down`（按需）

## 8. 现状评估（基于当前仓库）

- 架构完整度：高（索引、迁移、转写、知识加工闭环已形成）。
- 产物规模：`subtitles` 文件规模大（约万级），适合继续自动化分层。
- 风险点：
  - 依赖浏览器登录态脚本，接口变动时需维护。
  - ASR 文本有口语噪声，需二次精修流程兜底。
  - `config.yaml` 承载业务规则较多，建议逐步拆分配置与环境变量。

## 9. 结论

这个项目本质上是一个“**网盘内容治理 + 逐字稿生产 + 知识资产化**”的工程化流水线。  
它的工作原理不是单一脚本，而是多入口 CLI + 统一本地索引库 + 浏览器上下文抓取 + 后处理沉淀的组合系统。

