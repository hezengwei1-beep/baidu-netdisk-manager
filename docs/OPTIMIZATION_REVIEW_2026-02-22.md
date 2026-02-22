# 优化审查（Cloud Code 转写与多Agent链路）

审查日期: 2026-02-22
审查范围:
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager`
- 重点: 逐字稿生产 + 多Agent萃取 + 二次精修

## 1. 结论摘要

当前链路已经闭环可用，但在稳定性、可复用性和可维护性上还有明显提升空间。

优先级建议:
1. P0 稳定性: 进度文件写入原子化 + 容错
2. P0 可复用: 路径参数化，去除硬编码目录
3. P1 可运维: 失败可追溯（结构化日志 + run_id）
4. P1 可持续: 增量执行，避免每次全量覆盖
5. P2 工程化: 测试与配置治理

## 2. 主要问题与优化点

### P0-1: 进度 JSON 非原子写入，存在中断损坏风险

证据:
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/audio_transcript.py:47`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/audio_transcript.py:50`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/subtitle_extractor.py:65`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/subtitle_extractor.py:68`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/whisper_transcribe.py:39`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/whisper_transcribe.py:41`

问题:
- 直接 `write_text()` 覆盖写，进程中断或并发写时可能生成半截 JSON。

建议:
1. 改为临时文件写入 + `os.replace()` 原子替换。
2. `load_progress()` 加 JSONDecodeError 容错，自动备份坏文件后回退空结构。

---

### P0-2: 多Agent/精修脚本路径硬编码，复用成本高

证据:
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/multi_agent_bodhi_pipeline.py:20`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/multi_agent_bodhi_pipeline.py:21`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/refine_bodhi_analysis.py:17`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/refine_bodhi_analysis.py:18`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/refine_bodhi_analysis.py:20`

问题:
- 目录绑定到“菩提道”固定路径，迁移到其它课程需要改代码。

建议:
1. 增加 CLI 参数 `--transcript-dir --cloud-dir --output-dir --subset-dir`。
2. 增加默认值保持向后兼容。
3. 在总览文档中回写本次运行参数。

---

### P1-1: 失败信息保留不足，排障效率受限

证据:
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/audio_transcript.py:730`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/manager.py:67`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/manager.py:95`

问题:
- 部分异常只有简短字符串，缺少阶段、输入、批次上下文。

建议:
1. 统一错误结构: `stage`, `path`, `fsid`, `error_type`, `message`, `run_id`, `ts`。
2. 在失败 JSON 里记录 `stage`（下载/转写/保存）。
3. 主入口 `manager.py` 的兜底异常按场景细分，减少“吞错误”。

---

### P1-2: 结果导入全量覆盖，缺少增量策略

证据:
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/audio_transcript.py:505`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/audio_transcript.py:509`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/subtitle_extractor.py:274`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/subtitle_extractor.py:276`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/refine_bodhi_analysis.py:435`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/refine_bodhi_analysis.py:441`

问题:
- 默认覆盖写，重复运行会反复重写所有文件。

建议:
1. 增加 `--skip-existing`（默认开）和 `--force`。
2. 对输出文件增加元数据头（source hash, generated_at, pipeline_version）。
3. 仅在源文件变化时重跑。

---

### P2-1: 复杂逻辑缺少自动化测试

证据:
- 当前仓库未见针对以下逻辑的测试目录与测试用例:
  - 文本清洗/分句/术语提取
  - 讲次编号解析与排序
  - Cloud 对照匹配规则

建议:
1. 新增 `tests/`，先覆盖纯函数。
2. 以最小样本构建回归基线（3讲样本即可）。

---

### P2-2: 运行时临时脚本生成存在可读性和可控性问题

证据:
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/subtitle_extractor.py:615`
- `/Users/mac/Projects/active-projects/baidu-netdisk-manager/subtitle_extractor.py:621`

问题:
- 每次生成/覆盖 `m3u8_receiver.py`，脚本来源和版本不透明。

建议:
1. 把接收器脚本固化为仓库文件（例如 `srt_receiver.py` 一类方式），避免运行时动态生成。
2. 若保留动态生成，增加版本标识与签名注释。

## 3. 推荐改造路线（低风险）

第 1 周:
1. 进度文件原子写 + 读取容错
2. 导入类命令加 `--skip-existing`

第 2 周:
1. 多Agent/精修脚本参数化
2. 增加 run_id 和结构化失败日志

第 3 周:
1. 纯函数测试（文本清洗、分句、匹配）
2. 小样本回归测试

## 4. 预期收益

1. 中断恢复能力更强，减少“进度文件损坏”导致的返工。
2. 同样流程可迁移到其它课程，不必改源码。
3. 排障时间缩短，失败可快速定位到具体阶段与样本。
4. 重跑成本下降，避免无意义全量覆盖。

