from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

# 使用本地时区的当前时间
def _local_now() -> str:
    """返回本地时区的当前时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: Path) -> None:
        """初始化数据库连接并创建必要的表结构。

        Args:
            path: 数据库文件路径，父目录会自动创建
        """
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._bootstrap()

    @contextmanager
    def locked(self) -> Iterator[sqlite3.Connection]:
        """获取线程安全的数据库连接上下文管理器。

        Yields:
            加锁的数据库连接对象

        Example:
            with db.locked() as conn:
                conn.execute("SELECT * FROM table")
        """
        with self._lock:
            yield self._connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """执行单条 SQL 语句并提交事务。

        Args:
            sql: SQL 语句字符串
            params: 参数元组，可选

        Returns:
            执行结果的游标对象
        """
        with self.locked() as connection:
            cursor = connection.execute(sql, params)
            connection.commit()
            return cursor

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        """批量执行多条相同的 SQL 语句。

        Args:
            sql: SQL 语句字符串
            rows: 参数元组列表，每个元组对应一次执行
        """
        with self.locked() as connection:
            connection.executemany(sql, rows)
            connection.commit()

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        """执行查询并返回单行结果。

        Args:
            sql: SQL 查询语句字符串
            params: 参数元组，可选

        Returns:
            查询结果行（Row 对象），如果没有结果则返回 None
        """
        with self.locked() as connection:
            cursor = connection.execute(sql, params)
            return cursor.fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """执行查询并返回所有结果行。

        Args:
            sql: SQL 查询语句字符串
            params: 参数元组，可选

        Returns:
            查询结果行列表（Row 对象列表）
        """
        with self.locked() as connection:
            cursor = connection.execute(sql, params)
            return list(cursor.fetchall())

    def close(self) -> None:
        """关闭数据库连接并释放资源。"""
        with self.locked():
            self._connection.close()

    def _bootstrap(self) -> None:
        """初始化数据库表结构，创建所有必要的表和索引。

        创建的表包括：
        - chat_history: 聊天历史记录
        - intent_log: 意图决策日志
        - cooldown_state: 冷却状态
        - user_profile: 用户画像
        - group_profile: 群聊画像
        - chat_summary: 聊天摘要
        - memory: 记忆存储
        - reflection: 反思记录
        - emoji_store: 表情使用记录
        """
        statements = [
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                role TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                content_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'event',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_chat_history_chat_key_created_at
            ON chat_history(chat_key, id)
            """,
            """
            CREATE TABLE IF NOT EXISTS intent_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                should_reply INTEGER NOT NULL,
                reply_style TEXT NOT NULL,
                need_reason_model INTEGER NOT NULL,
                need_emoji INTEGER NOT NULL,
                group_mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cooldown_state (
                chat_key TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                last_message_at REAL NOT NULL,
                last_reply_at REAL NOT NULL,
                recent_10s_count INTEGER NOT NULL DEFAULT 0,
                recent_30s_count INTEGER NOT NULL DEFAULT 0,
                heat_state TEXT NOT NULL DEFAULT 'quiet',
                last_history_backfill_at REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL DEFAULT '{}',
                prompt_md TEXT NOT NULL DEFAULT '',
                dirty_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS group_profile (
                group_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL DEFAULT '{}',
                prompt_md TEXT NOT NULL DEFAULT '',
                message_count_since_update INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chat_summary (
                chat_key TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                score REAL NOT NULL,
                source_message_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS reflection (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_key TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS emoji_store (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emoji_type TEXT NOT NULL DEFAULT '',
                face_id TEXT NOT NULL DEFAULT '',
                emoji_id TEXT NOT NULL DEFAULT '',
                emoji_package_id TEXT NOT NULL DEFAULT '',
                key TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}',
                usage_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_emoji_store_face_unique
            ON emoji_store(face_id)
            WHERE emoji_type = 'face'
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_emoji_store_mface_unique
            ON emoji_store(emoji_id, emoji_package_id, key)
            WHERE emoji_type = 'mface'
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_emoji_store_image_unique
            ON emoji_store(image_url)
            WHERE emoji_type = 'image'
            """,
        ]
        with self.locked() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            for statement in statements:
                connection.execute(statement)
            connection.commit()


def json_dumps(payload: Any) -> str:
    """将对象序列化为紧凑的 JSON 字符串（无 ASCII 转义）。

    Args:
        payload: 待序列化的对象

    Returns:
        紧凑格式的 JSON 字符串，中文字符保持原样
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
