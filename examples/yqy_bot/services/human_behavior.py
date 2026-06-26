"""真人行为模拟器：打字延迟、消息拆分、引用回复概率、戳一戳冷却。
所有参数从 config/human_behavior.json 加载。

消息段辅助函数：
  _image_segment(url)   → {"type": "image", "data": {"file": url}}
  _face_segment(face_id) → {"type": "face", "data": {"id": face_id}}
  _text_segment(text)    → {"type": "text", "data": {"text": text}}
  _reply_segment(msg_id) → {"type": "reply", "data": {"id": msg_id}}
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "human_behavior.json"


def _load() -> dict:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


_cfg = _load()

_delay = _cfg.get("typing_delay", {})
_split = _cfg.get("message_split", {})
_length = _cfg.get("message_length", {})

TY_DELAY_MIN: float = float(_delay.get("min_seconds", 1))
TY_DELAY_MAX: float = float(_delay.get("max_seconds", 6))
SPLIT_ENABLED: bool = bool(_split.get("split_enabled", False))
SPLIT_MIN_CHARS: int = int(_split.get("min_chars", 120))
SPLIT_MAX_PARTS: int = int(_split.get("max_parts", 2))
SPLIT_DELAY: float = float(_split.get("split_delay_seconds", 2))
QUOTE_REPLY_PROB: float = float(_cfg.get("quote_reply_probability", 0.3))
POKE_COOLDOWN_HOURS: int = int(_cfg.get("poke_cooldown_hours", 24))
PREFER_SHORT_PCT: int = int(_length.get("prefer_short_pct", 80))
SHORT_MAX_CHARS: int = int(_length.get("short_max_chars", 20))


# ═══════════════════════════════════════════
#  消息段辅助函数
# ═══════════════════════════════════════════

def _image_segment(url: str) -> dict[str, Any]:
    """创建 OneBot11 image 消息段。"""
    return {"type": "image", "data": {"file": url}}


def _face_segment(face_id: str) -> dict[str, Any]:
    """创建 OneBot11 face（QQ 内置表情）消息段。"""
    return {"type": "face", "data": {"id": face_id}}


def _text_segment(text: str) -> dict[str, Any]:
    """创建 OneBot11 text 消息段。"""
    return {"type": "text", "data": {"text": text}}


def _reply_segment(message_id: int | str) -> dict[str, Any]:
    """创建 OneBot11 reply（引用回复）消息段。"""
    return {"type": "reply", "data": {"id": str(message_id)}}


def _mface_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """从 mface 段 data 字典创建 OneBot11 mface 消息段。"""
    return {"type": "mface", "data": data}


def _split_text(text: str, max_len: int | None = None, max_parts: int = 2) -> list[str]:
    """超过 max_len 的长文本拆成多条，尽量在句末分割。

    Args:
        text: 原始文本
        max_len: 拆分阈值，默认取 SPLIT_MIN_CHARS
        max_parts: 最多拆分块数，默认 2
    """
    if max_len is None:
        max_len = SPLIT_MIN_CHARS
    if not text:
        return []
    if max_parts <= 1 or len(text) <= max_len:
        return [text]

    # 只拆第一段 + 剩余部分
    limit = min(max_parts, 2)
    mid = len(text) // 2
    for sep in "。！？\n":
        pos = text.rfind(sep, mid - 10, mid + 10)
        if pos > 0:
            return [text[: pos + 1], text[pos + 1 :]]
    return [text[:mid], text[mid:]]


# ═══════════════════════════════════════════
#  戳一戳冷却
# ═══════════════════════════════════════════

class PokeCooldown:
    """按用户记录最近一次戳一戳时间，24h 内不重复。"""

    def __init__(self) -> None:
        self._records: dict[str, datetime] = {}

    def can_poke(self, user_id: str) -> bool:
        last = self._records.get(user_id)
        if last is None:
            return True
        return datetime.now(timezone.utc) - last > timedelta(hours=POKE_COOLDOWN_HOURS)

    def record_poke(self, user_id: str) -> None:
        self._records[user_id] = datetime.now(timezone.utc)


_poke_cooldown = PokeCooldown()


def get_poke_cooldown() -> PokeCooldown:
    return _poke_cooldown


# ═══════════════════════════════════════════
#  TypingBehaviorService
# ═══════════════════════════════════════════

class TypingBehaviorService:
    """真人行为模拟：打字延迟 → 引用回复 → 图片/表情 → 文字分拆发送。"""

    async def send_human_like(
        self,
        adapter: Any,
        user_id: str,
        text: str,
        meme_url: str | None = None,
        meme_mface_data: dict[str, Any] | None = None,
        face_id: str = "",
        event_message: Any = None,
    ) -> None:
        """模拟真人发送消息。只用于私聊。

        - 随机打字延迟 1~6 秒
        - 30% 概率引用上一条消息
        - meme_url → type=image, meme_mface_data → type=mface, face_id → type=face
        - 图片/表情/引用只在第一段发送一次
        - 默认不拆分消息；仅当 split_enabled=true 且文本 > 120 字时拆分，最多 2 段
        """
        # ── 1. 打字延迟 ──
        delay = random.uniform(TY_DELAY_MIN, TY_DELAY_MAX)
        await asyncio.sleep(delay)

        # ── 2. 引用回复（30% 概率）──
        use_quote = random.random() < QUOTE_REPLY_PROB
        quote_id: str | None = None
        if use_quote and event_message is not None:
            try:
                quote_id = str(event_message.message_id)
                logger.debug(f"[真人发送] 引用回复: user={user_id} msg_id={quote_id}")
            except Exception:
                pass

        # ── 3. 日志：发送概要 ──
        logger.info(
            f"[真人发送] user={user_id} "
            f"text_len={len(text)} "
            f"has_image={meme_url is not None} "
            f"has_mface={meme_mface_data is not None} "
            f"has_face={bool(face_id)} "
            f"use_quote={use_quote}"
        )
        if meme_url:
            logger.info(f"[真人发送] 图片URL: {meme_url[:80]}")
        if meme_mface_data:
            logger.info(f"[真人发送] mface: {json.dumps(meme_mface_data, ensure_ascii=False)[:80]}")

        # ── 4. 文本分拆（仅当 split_enabled=true 时才拆）──
        if SPLIT_ENABLED:
            text_chunks = _split_text(text, max_parts=SPLIT_MAX_PARTS)
        else:
            text_chunks = [text] if text else []

        # 纯图片/mface无文字：也发一条
        if not text_chunks and (meme_url or meme_mface_data):
            text_chunks = [""]

        if not text_chunks:
            logger.warning(f"[真人发送] user={user_id} 无文字也无图片，跳过")
            return

        # ── 5. 逐段构建并发送 ──
        for i, chunk in enumerate(text_chunks):
            segments: list[dict[str, Any]] = []

            # 只在第一段附加：引用 / 图片 / mface / 表情
            if i == 0:
                if quote_id:
                    segments.append(_reply_segment(quote_id))
                if meme_url:
                    segments.append(_image_segment(meme_url))
                if meme_mface_data:
                    segments.append(_mface_from_dict(meme_mface_data))
                if face_id:
                    segments.append(_face_segment(face_id))

            # 文字段（跳过空字符串）
            if chunk.strip():
                segments.append(_text_segment(chunk))

            # 如果没有任何内容可发，跳过这一轮
            if not segments:
                continue

            logger.debug(
                f"[真人发送] chunk={i+1}/{len(text_chunks)} "
                f"segments={[s['type'] for s in segments]}"
            )

            # 构造 Message 并发送（私聊）
            from iamai import Message  # 延迟导入，避免模块级依赖 iamai
            msg = Message(segments)
            try:
                await adapter.send_message(
                    msg,
                    target={"user_id": user_id},
                )
                logger.info(
                    f"[真人发送] → {user_id} "
                    f"chunk={i+1}/{len(text_chunks)} "
                    f"types={[s['type'] for s in segments]}"
                )
            except Exception:
                logger.exception(f"[真人发送] 发送失败: user={user_id} chunk={i+1}")
                return

            # 分段间间隔
            if i < len(text_chunks) - 1:
                await asyncio.sleep(SPLIT_DELAY)


# ── 单例 + 向后兼容函数 ──

_typing_service = TypingBehaviorService()


async def send_human_like(
    adapter: Any,
    user_id: str,
    text: str,
    meme_url: str | None = None,
    meme_mface_data: dict[str, Any] | None = None,
    face_id: str = "",
    event_message: Any = None,
) -> None:
    """向后兼容的模块级函数，委托给 TypingBehaviorService。"""
    await _typing_service.send_human_like(
        adapter=adapter,
        user_id=user_id,
        text=text,
        meme_url=meme_url,
        meme_mface_data=meme_mface_data,
        face_id=face_id,
        event_message=event_message,
    )
