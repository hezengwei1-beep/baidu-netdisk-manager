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
