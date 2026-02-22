# baidu-netdisk-manager

百度网盘文件管理与清理工具。

## 功能

- 全盘文件索引与扫描（SQLite 本地缓存）
- 重复文件检测（SHA-256 哈希）
- 文件分类与智能整理
- 批量清理与空间回收
- OAuth 认证管理

## 安装

```bash
pip install -r requirements.txt
```

## 配置

复制 `config.yaml.example` 为 `config.yaml`，填入百度网盘 API 凭证。

## 使用

```bash
python manager.py --help
```

## 技术栈

- Python 3
- Click (CLI)
- Rich (终端 UI)
- SQLite (本地索引)
- 百度网盘开放平台 API

## 芒格 Agent（新增）

项目内置了一个基于“芒格能力全集”方法论的 Agent MVP：

- 文件：`munger_agent.py`
- 能力：问题卡、反向思考、模型检索、激励体检、偏误门诊、证据引用、决策备忘录输出

示例命令：

```bash
python munger_agent.py run \
  --query "我要推行总部收权策略，避免地方推诿" \
  --goal "建立统一治理并保证执行质量" \
  --constraint "不增加总编制" \
  --constraint "3个月内见效" \
  --kpi-text "考核只看当月处理时效，季度奖金与时效挂钩" \
  --output data/knowledge/芒格Agent-样例输出.md
```

交互模式：

```bash
python munger_agent.py chat
```
