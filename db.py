"""SQLite 文件索引模块"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "index.db"


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            fsid        INTEGER PRIMARY KEY,
            path        TEXT NOT NULL UNIQUE,
            filename    TEXT NOT NULL,
            size        INTEGER NOT NULL DEFAULT 0,
            isdir       INTEGER NOT NULL DEFAULT 0,
            md5         TEXT DEFAULT '',
            server_mtime INTEGER DEFAULT 0,
            local_mtime  INTEGER DEFAULT 0,
            category    INTEGER DEFAULT 0,
            extension   TEXT DEFAULT '',
            parent_dir  TEXT DEFAULT '',
            scanned_at  INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
        CREATE INDEX IF NOT EXISTS idx_files_md5 ON files(md5);
        CREATE INDEX IF NOT EXISTS idx_files_parent ON files(parent_dir);
        CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension);
        CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);

        CREATE TABLE IF NOT EXISTS scan_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_dir    TEXT NOT NULL,
            file_count  INTEGER DEFAULT 0,
            started_at  INTEGER NOT NULL,
            finished_at INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS classifications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path     TEXT NOT NULL,
            target_path     TEXT NOT NULL,
            confidence      REAL DEFAULT 0,
            confidence_level TEXT DEFAULT '',
            rule_name       TEXT DEFAULT '',
            reason          TEXT DEFAULT '',
            file_count      INTEGER DEFAULT 0,
            total_size      INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'pending',
            created_at      INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_class_source ON classifications(source_path);
        CREATE INDEX IF NOT EXISTS idx_class_status ON classifications(status);
        CREATE INDEX IF NOT EXISTS idx_class_confidence ON classifications(confidence);

        CREATE TABLE IF NOT EXISTS migration_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id        TEXT NOT NULL,
            phase           INTEGER DEFAULT 0,
            source_path     TEXT NOT NULL,
            target_path     TEXT DEFAULT '',
            status          TEXT DEFAULT '',
            error_message   TEXT DEFAULT '',
            executed_at     INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_migration_batch ON migration_log(batch_id);
        CREATE INDEX IF NOT EXISTS idx_migration_phase ON migration_log(phase);
    """)
    conn.commit()
    conn.close()


def upsert_file(conn: sqlite3.Connection, file_info: dict):
    """插入或更新文件记录"""
    path = file_info.get("path", "")
    if path == "/":
        filename = ""
        parent_dir = "/"
    elif "/" in path:
        filename = path.rsplit("/", 1)[-1]
        parent_dir = path.rsplit("/", 1)[0] or "/"
    else:
        filename = path
        parent_dir = "/"
    ext = Path(filename).suffix.lower() if not file_info.get("isdir", 0) else ""

    conn.execute("""
        INSERT INTO files (fsid, path, filename, size, isdir, md5, server_mtime,
                          local_mtime, category, extension, parent_dir, scanned_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fsid) DO UPDATE SET
            path=excluded.path, filename=excluded.filename, size=excluded.size,
            md5=excluded.md5, server_mtime=excluded.server_mtime,
            local_mtime=excluded.local_mtime, category=excluded.category,
            extension=excluded.extension, parent_dir=excluded.parent_dir,
            scanned_at=excluded.scanned_at
    """, (
        file_info.get("fs_id", 0),
        path,
        filename,
        file_info.get("size", 0),
        file_info.get("isdir", 0),
        file_info.get("md5", ""),
        file_info.get("server_mtime", 0),
        file_info.get("local_mtime", 0),
        file_info.get("category", 0),
        ext,
        parent_dir if parent_dir else "/",
        int(time.time()),
    ))


def batch_upsert(file_list: list[dict]):
    """批量插入/更新文件记录"""
    conn = get_connection()
    for f in file_list:
        upsert_file(conn, f)
    conn.commit()
    conn.close()


def get_all_files(include_dirs: bool = False) -> list[dict]:
    """获取所有文件记录"""
    conn = get_connection()
    if include_dirs:
        rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()
    else:
        rows = conn.execute("SELECT * FROM files WHERE isdir=0 ORDER BY path").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_duplicates() -> dict[str, list[dict]]:
    """查找重复文件（相同 MD5 且非空）"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT md5, COUNT(*) as cnt FROM files
        WHERE isdir=0 AND md5 != '' AND size > 0
        GROUP BY md5 HAVING cnt > 1
    """).fetchall()

    duplicates = {}
    for row in rows:
        md5 = row["md5"]
        files = conn.execute(
            "SELECT * FROM files WHERE md5=? AND isdir=0 ORDER BY path",
            (md5,),
        ).fetchall()
        duplicates[md5] = [dict(f) for f in files]

    conn.close()
    return duplicates


def find_large_files(threshold_bytes: int) -> list[dict]:
    """查找大文件"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM files WHERE isdir=0 AND size >= ? ORDER BY size DESC",
        (threshold_bytes,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_expired_files(expire_seconds: int) -> list[dict]:
    """查找过期文件（根据 server_mtime）"""
    cutoff = int(time.time()) - expire_seconds
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM files WHERE isdir=0 AND server_mtime < ? AND server_mtime > 0 ORDER BY server_mtime",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_empty_dirs() -> list[dict]:
    """查找空目录"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT d.* FROM files d
        WHERE d.isdir=1
        AND NOT EXISTS (
            SELECT 1 FROM files f WHERE f.parent_dir = d.path
        )
        ORDER BY d.path
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_records(paths: list[str]):
    """从索引中删除记录"""
    conn = get_connection()
    for path in paths:
        conn.execute("DELETE FROM files WHERE path=?", (path,))
    conn.commit()
    conn.close()


def get_stats() -> dict:
    """获取索引统计"""
    conn = get_connection()
    total_files = conn.execute("SELECT COUNT(*) as c FROM files WHERE isdir=0").fetchone()["c"]
    total_dirs = conn.execute("SELECT COUNT(*) as c FROM files WHERE isdir=1").fetchone()["c"]
    total_size = conn.execute("SELECT COALESCE(SUM(size), 0) as s FROM files WHERE isdir=0").fetchone()["s"]
    last_scan = conn.execute("SELECT MAX(scanned_at) as t FROM files").fetchone()["t"]
    conn.close()
    return {
        "total_files": total_files,
        "total_dirs": total_dirs,
        "total_size": total_size,
        "last_scan": last_scan or 0,
    }


def log_scan(scan_dir: str, file_count: int, started_at: int, finished_at: int):
    """记录扫描日志"""
    conn = get_connection()
    conn.execute(
        "INSERT INTO scan_log (scan_dir, file_count, started_at, finished_at) VALUES (?, ?, ?, ?)",
        (scan_dir, file_count, started_at, finished_at),
    )
    conn.commit()
    conn.close()


# ===== 分类与迁移相关 =====

def get_directory_stats(parent_path: str = None) -> list[dict]:
    """获取目录统计信息（文件数、总大小）"""
    conn = get_connection()
    if parent_path:
        rows = conn.execute("""
            SELECT parent_dir, COUNT(*) as file_count, COALESCE(SUM(size), 0) as total_size
            FROM files WHERE isdir=0 AND parent_dir LIKE ?
            GROUP BY parent_dir ORDER BY total_size DESC
        """, (parent_path.rstrip("/") + "/%",)).fetchall()
    else:
        rows = conn.execute("""
            SELECT parent_dir, COUNT(*) as file_count, COALESCE(SUM(size), 0) as total_size
            FROM files WHERE isdir=0
            GROUP BY parent_dir ORDER BY total_size DESC
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_top_level_dirs() -> list[dict]:
    """获取顶级目录及其递归统计"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            CASE
                WHEN path LIKE '/%/%' THEN '/' || SUBSTR(path, 2, INSTR(SUBSTR(path, 2), '/') - 1)
                ELSE path
            END as top_dir,
            COUNT(*) as file_count,
            COALESCE(SUM(size), 0) as total_size
        FROM files WHERE isdir=0
        GROUP BY top_dir ORDER BY total_size DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_classifications(classifications: list[dict]):
    """保存分类结果（先清空旧数据）"""
    conn = get_connection()
    conn.execute("DELETE FROM classifications")
    now = int(time.time())
    for c in classifications:
        conn.execute("""
            INSERT INTO classifications
            (source_path, target_path, confidence, confidence_level, rule_name, reason,
             file_count, total_size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c["source_path"], c["target_path"], c["confidence"],
            c.get("confidence_level", ""), c.get("rule_name", ""),
            c.get("reason", ""), c.get("file_count", 0),
            c.get("total_size", 0), c.get("status", "pending"), now,
        ))
    conn.commit()
    conn.close()


def get_classifications(status: str = None, min_confidence: float = None) -> list[dict]:
    """查询分类结果"""
    conn = get_connection()
    sql = "SELECT * FROM classifications WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if min_confidence is not None:
        sql += " AND confidence>=?"
        params.append(min_confidence)
    sql += " ORDER BY confidence DESC, total_size DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_classification_status(source_path: str, status: str):
    """更新分类状态"""
    conn = get_connection()
    conn.execute(
        "UPDATE classifications SET status=? WHERE source_path=?",
        (status, source_path),
    )
    conn.commit()
    conn.close()


def log_migration(batch_id: str, phase: int, source_path: str,
                  target_path: str, status: str, error_message: str = ""):
    """记录迁移日志"""
    conn = get_connection()
    conn.execute("""
        INSERT INTO migration_log (batch_id, phase, source_path, target_path, status, error_message, executed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (batch_id, phase, source_path, target_path, status, error_message, int(time.time())))
    conn.commit()
    conn.close()
