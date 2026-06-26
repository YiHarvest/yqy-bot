"""检查 chat.db 中是否包含可疑幻觉词。

用法：
    python scripts/check_fake_facts.py

检查的表：chat_history, memory, reflection, social_memory
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


def check_table(conn: sqlite3.Connection, table: str, keywords: list[str]) -> list[dict]:
    results: list[dict] = []
    columns = _columns_for(table)
    if not columns:
        return results
    col_str = ", ".join(columns)
    for kw in keywords:
        rows = conn.execute(
            f"SELECT {col_str} FROM {table} WHERE content LIKE ?",
            (f"%{kw}%",),
        ).fetchall()
        for row in rows:
            record = dict(zip(columns, row))
            record["matched_kw"] = kw
            record["table"] = table
            results.append(record)
    return results


def _columns_for(table: str) -> list[str]:
    common = ["id", "content", "created_at"]
    if table == "chat_history":
        return ["id", "session_id", "role", "content", "created_at"]
    if table == "memory":
        return ["id", "user_id", "content", "importance", "created_at"]
    if table == "reflection":
        return common
    if table == "social_memory":
        return ["id", "subject_user", "target_user", "relation", "content", "importance", "created_at"]
    return []


def main() -> None:
    keywords = load_keywords()
    if not keywords:
        print("fact_guard.json 中没有 fake_fact_keywords，无需检查。")
        return

    print(f"关键词列表: {keywords}\n")

    conn = get_connection()
    try:
        found_any = False
        for table in ["chat_history", "memory", "reflection", "social_memory"]:
            rows = check_table(conn, table, keywords)
            if rows:
                found_any = True
                print(f"=== {table} === ({len(rows)} 条)")
                for r in rows:
                    print(f"  id={r.get('id')} 关键词={r['matched_kw']}  content={r['content'][:60]}")
                    for key, val in r.items():
                        if key not in ("id", "content", "matched_kw", "table"):
                            print(f"    {key}={val}")
                print()
        if not found_any:
            print("未发现可疑幻觉内容。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
