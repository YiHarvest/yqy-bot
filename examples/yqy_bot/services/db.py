"""SQLite 数据库连接管理，启动时自动创建表和索引。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock


DB_DIR = Path(__file__).resolve().parents[1] / "data"
DB_PATH = DB_DIR / "chat.db"

_write_lock = Lock()


def get_db_path() -> Path:
    """返回数据库文件路径，确保目录存在。"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    return DB_PATH


def get_connection() -> sqlite3.Connection:
    """创建并返回一个新的 SQLite 连接（每次调用都是新连接）。"""
    conn = sqlite3.connect(str(get_db_path()))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """初始化数据库：创建表结构和索引（幂等操作）。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT    NOT NULL,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_session ON chat_history(session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_time ON chat_history(session_id, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                importance  REAL    DEFAULT 0.5,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_user ON memory(user_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_state (
                id          INTEGER PRIMARY KEY CHECK(id = 1),
                mood        INTEGER DEFAULT 50,
                energy      INTEGER DEFAULT 80,
                loneliness  INTEGER DEFAULT 20,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # 确保有且只有一行
        conn.execute(
            "INSERT OR IGNORE INTO persona_state (id, mood, energy, loneliness) VALUES (1, 50, 80, 20)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship (
                user_id         TEXT PRIMARY KEY,
                nickname        TEXT    NOT NULL,
                identity        TEXT    NOT NULL,
                favorability    INTEGER DEFAULT 50,
                intimacy        INTEGER DEFAULT 50,
                trust           INTEGER DEFAULT 50,
                last_chat_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS behavior_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_behavior_user ON behavior_log(user_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reflection (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content     TEXT    NOT NULL,
                importance  REAL    DEFAULT 0.5,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_memory (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_user    TEXT    NOT NULL,
                target_user     TEXT    NOT NULL,
                relation        TEXT    NOT NULL,
                content         TEXT    NOT NULL,
                importance      REAL    DEFAULT 0.5,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_subject ON social_memory(subject_user)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_target ON social_memory(target_user)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorite_memes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                file_path   TEXT    NOT NULL,
                source_file TEXT,
                source_url  TEXT,
                emotion     TEXT    DEFAULT 'default',
                tags        TEXT    DEFAULT '',
                usage_count INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT,
                meme_type   TEXT    DEFAULT 'image'
            )
            """
        )
        # 兼容迁移：对已存在的表补加 meme_type 列（SQLite ALTER TABLE ADD COLUMN 不支持 IF NOT EXISTS，忽略报错）
        try:
            conn.execute("ALTER TABLE favorite_memes ADD COLUMN meme_type TEXT DEFAULT 'image'")
        except Exception:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_favorite_memes_user_emotion ON favorite_memes(user_id, emotion)"
        )
        conn.commit()
    finally:
        conn.close()
