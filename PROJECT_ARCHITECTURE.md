# baidu-netdisk-manager 架构图版

生成时间：2026-02-19  
用途：配合 `PROJECT_REPORT.md` 快速理解系统结构、数据流和执行流程。

## 1. 系统总览

```mermaid
flowchart LR
    A["CLI Entrypoints"] --> B["网盘治理层"]
    A --> C["逐字稿生产层"]
    A --> D["知识加工层"]

    B --> B1["auth.py / api.py"]
    B --> B2["manager.py"]
    B --> B3["db.py (SQLite)"]
    B --> B4["classifier.py / migration.py / cleaner.py / dedup.py / sync.py"]

    C --> C1["subtitle_extractor.py"]
    C --> C2["audio_transcript.py"]
    C --> C3["whisper_transcribe.py"]
    C --> C4["auto_extract.py / batch_extract.py / srt_receiver.py"]

    D --> D1["data/subtitles/*.txt"]
    D --> D2["data/knowledge/*.md"]
    D --> D3["multi_agent_bodhi_pipeline.py"]
    D --> D4["refine_bodhi_analysis.py"]

    B3 --> E["data/index.db"]
    C --> F["data/*progress*.json"]
    C --> G["data/subtitles/"]
    D --> H["data/knowledge/"]
```

## 2. 主模块依赖图

```mermaid
flowchart TB
    M["manager.py"] --> AU["auth.py"]
    M --> API["api.py"]
    M --> DB["db.py"]
    M --> CL["classifier.py"]
    M --> MG["migration.py"]
    M --> ORG["organizer.py"]
    M --> DED["dedup.py"]
    M --> CLEAN["cleaner.py"]
    M --> SYNC["sync.py"]
    M --> TAX["taxonomy.py"]

    AT["audio_transcript.py"] --> API
    AT --> AU
    AT --> DB
    SE["subtitle_extractor.py"] --> DB
    WT["whisper_transcribe.py"] --> GFS["ffmpeg + faster-whisper"]

    CL --> TAX
    CL --> DB
    MG --> API
    MG --> DB
    DED --> DB
    CLEAN --> DB
```

## 3. 数据层结构

```mermaid
flowchart LR
    IDX["data/index.db"] --> T1["files"]
    IDX --> T2["scan_log"]
    IDX --> T3["classifications"]
    IDX --> T4["migration_log"]

    P1["data/audio_transcript_progress.json"]
    P2["data/video_extract_progress.json"]
    P3["data/subtitle_progress.json"]

    SUB["data/subtitles/**/*.srt|txt"]
    KNOW["data/knowledge/**/*.md"]

    SE["subtitle_extractor.py"] --> P3
    AT["audio_transcript.py"] --> P1
    BE["auto_extract.py / batch_extract.py"] --> P2

    P1 --> SUB
    P2 --> SUB
    P3 --> SUB
    SUB --> KNOW
```

## 4. 网盘治理流程（scan -> classify -> migrate）

```mermaid
flowchart TD
    S0["manager.py scan"] --> S1["auth.ensure_token()"]
    S1 --> S2["api.list_all()"]
    S2 --> S3{"列表是否完整?"}
    S3 -- "否" --> S4["api.walk_dir() 回退遍历"]
    S3 -- "是" --> S5["批量 file_meta() 获取 md5"]
    S4 --> S5
    S5 --> S6["db.batch_upsert(files)"]
    S6 --> S7["index.db 更新完成"]

    C0["manager.py classify"] --> C1["load taxonomy + directory_mappings"]
    C1 --> C2["规则1: 精确/前缀映射"]
    C2 --> C3["规则2: 关键词匹配"]
    C3 --> C4["规则3: 内容分析"]
    C4 --> C5["db.save_classifications()"]

    M0["manager.py migrate"] --> M1["phase1: 创建目录"]
    M1 --> M2["phase2: 高置信度自动迁移"]
    M2 --> M3["phase3: 中低置信度交互审核"]
    M3 --> M4["phase4: 清理空目录"]
    M4 --> M5["migration_log 可回滚"]
```

## 5. 逐字稿生产流程（浏览器态抓取）

```mermaid
sequenceDiagram
    participant U as "User / Puppeteer"
    participant JS as "Browser JS Script"
    participant PAN as "pan.baidu.com 内部接口"
    participant IMP as "import-results 命令"
    participant FS as "data/subtitles/"
    participant PG as "progress.json"

    U->>JS: "执行 generate-js 输出的脚本"
    JS->>PAN: "请求 streaming/listennote 接口"
    PAN-->>JS: "返回 SRT / M3U8 / 状态码"
    JS-->>U: "window.__subtitleResults 或 JSON 文件"
    U->>IMP: "python ... import-results --file <json>"
    IMP->>FS: "保存 .srt + .txt"
    IMP->>PG: "写 completed/failed/not_transcoded"
```

## 6. Whisper 流式转写流程（M3U8）

```mermaid
flowchart TD
    W0["whisper_transcribe.py"] --> W1["读取 batch_json: path + m3u8"]
    W1 --> W2["ffmpeg: m3u8 -> 16k wav"]
    W2 --> W3["faster-whisper 转录"]
    W3 --> W4["生成 srt + txt"]
    W4 --> W5["写入 data/subtitles/"]
    W5 --> W6["更新 audio_transcript_progress.json"]
```

## 7. 菩提道专题加工流程（新增）

```mermaid
flowchart LR
    T0["data/subtitles/.../菩提道次第*.txt"] --> T1["multi_agent_bodhi_pipeline.py"]
    T1 --> T2["10 Agent 逐讲梳理"]
    T2 --> T3["多Agent梳理/00_总览_多Agent梳理.md"]
    T2 --> T4["24菩提道/知识萃取/*.md (补齐)"]
    T2 --> T5["refine_bodhi_analysis.py"]
    T5 --> T6["二次精修: 逐讲精修 + 术语词典 + 跨讲主题地图"]
```

## 8. 运行建议（架构视角）

1. 先稳定 `scan -> classify -> migrate` 主链，再扩大逐字稿批量任务。
2. 将“浏览器抓取”和“本地加工”明确分层，避免耦合在一个脚本里。
3. 对 `data/*progress*.json` 建立定期快照，防止中断后状态污染。
4. 大规模文本加工前先做目录级 dry-run，降低误写风险。

