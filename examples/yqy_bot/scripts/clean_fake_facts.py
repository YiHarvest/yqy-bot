"""清理 chat.db 中包含可疑幻觉词的记录。

用法：
    python scripts/clean_fake_facts.py                     # 仅清理 memory/reflection/social_memory
    python scripts/clean_fake_facts.py --delete-history    # 同时清理 chat_history 中的幻觉内容

关键词来自 config/fact_guard.json 的 fake_fact_keywords。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT / "services"))

from db import get_connection  # noqa: E402

_GUARD_PATH = _PROJECT / "config" / "fact_guard.json"


def load_keywords() -> list[str]:
    if _GUARD_PATH.is_file():
        cfg = json.loads(_GUARD_PATH.read_text(encoding="utf-8"))
        return cfg.get("fake_fact_keywords", [])
    return []


def delete_from(conn: sqlite3.Connection, table: str, keywords: list[str]) -> int:
    total = 0
    for kw in keywords:
        cursor = conn.execute(f"DELETE FROM {table} WHERE content LIKE ?", (f"%{kw}%",))
        total += cursor.rowcount
    return total


def list_history_candidates(conn: sqlite3.Connection, keywords: list[str]):
    for kw in keywords:
        rows = conn.execute(
            "SELECT id, session_id, role, content FROM chat_history WHERE content LIKE ? ORDER BY id",
            (f"%{kw}%",),
        ).fetchall()
        for row in rows:
            print(
                f"  chat_history id={row[0]} session={row[1]} role={row[2]} content={row[3][:60]}"
            )


def delete_history(conn: sqlite3.Connection, keywords: list[str]) -> int:
    total = 0
    for kw in keywords:
        cursor = conn.execute(
            "DELETE FROM chat_history WHERE content LIKE ?", (f"%{kw}%",)
        )
        total += cursor.rowcount
    return total


def main() -> None:
    delete_history_flag = "--delete-history" in sys.argv

    keywords = load_keywords()
    if not keywords:
        print("fact_guard.json 中没有 fake_fact_keywords，无需清理。")
        return

    print(f"关键词列表: {keywords}\n")
    conn = get_connection()
    try:
        # ── 清理 memory / reflection / social_memory ──
        for table in ["memory", "reflection", "social_memory"]:
            count = delete_from(conn, table, keywords)
            if count:
                print(f"已删除 {table}: {count} 条")

        # ── chat_history：默认只列出，加参数才删 ──
        hist_count = 0
        for kw in keywords:
            rows = conn.execute(
                "SELECT COUNT(*) FROM chat_history WHERE content LIKE ?",
                (f"%{kw}%",),
            ).fetchone()
            if rows:
                hist_count += rows[0]

        if hist_count:
            print(f"\nchat_history 中存在 {hist_count} 条可疑记录：")
            list_history_candidates(conn, keywords)

            if delete_history_flag:
                deleted = delete_history(conn, keywords)
                print(f"\n已删除 chat_history: {deleted} 条")
            else:
                print("\n提示：加 --delete-history 参数可删除以上 chat_history 记录。")
        else:
            print("\nchat_history 中未发现可疑记录。")
    finally:
        conn.commit()
        conn.close()


if __name__ == "__main__":
    main()
