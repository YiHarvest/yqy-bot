"""主动话题生成服务：结合记忆、反思、社交关系，生成有上下文的主动消息。
禁止生成"在吗""最近怎么样""干嘛呢""忙吗"等通用废话。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iamai import LLMClient, LLMConfig
from loguru import logger

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "active_life.json"


def _load() -> dict:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


_cfg = _load()

BANNED_PHRASES: list[str] = [
    "在吗", "最近怎么样", "干嘛呢", "忙吗", "最近还好吗",
    "吃了吗", "睡了没", "想你了", "好久不见", "今天天气不错",
]


class ConversationStarterService:
    """根据用户上下文生成一句有意义的主动消息。"""

    async def generate(
        self,
        nickname: str,
        identity: str,
        memories: list[str],
        reflections: list[str],
        history: list[dict[str, str]],
        social_memories: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """返回生成的主动消息，若 LLM 失败则返回 None。"""
        memory_block = "\n".join(f"- {m}" for m in memories) if memories else ""
        reflection_block = "\n".join(f"- {r}" for r in reflections) if reflections else ""

        history_block = ""
        if history:
            lines = []
            for h in history[-6:]:
                role_name = "对方" if h["role"] == "user" else "你"
                lines.append(f"{role_name}: {h['content']}")
            history_block = "\n".join(lines)

        social_block = ""
        if social_memories:
            lines = []
            for sm in social_memories:
                lines.append(
                    f"- {sm['subject_user']} 对 {sm['target_user']}（{sm['relation']}）：{sm['content']}"
                )
            social_block = "\n".join(lines)

        persona_hint = self._persona_hint(identity)

        prompt_parts = [
            "你叫 YHarvest，你想主动找你的朋友聊一句。",
            "",
            f"对方昵称：{nickname}",
            f"身份：{identity}",
            persona_hint,
        ]
        if memory_block:
            prompt_parts.append(f"\n## 你记得关于对方的事实\n{memory_block}")
        if social_block:
            prompt_parts.append(f"\n## 你了解的人物关系\n{social_block}")
        if reflection_block:
            prompt_parts.append(f"\n## 你最近对对方的观察\n{reflection_block}")
        if history_block:
            prompt_parts.append(f"\n## 最近聊天记录\n{history_block}")

        prompt_parts.extend([
            "",
            "## 生成规则",
            "- 必须结合上面的记忆/观察/聊天记录，说一句自然的话",
            "- 像真人朋友突然想到什么，随口问一句或吐槽一句",
            "- 只返回这一句话，不要加任何前缀或解释",
            "- 一句话不超过 20 字",
            "",
            "## 事实约束（重要）",
            "- 禁止编造不存在的过去经历、虚构事件",
            "- 吐槽只能基于记忆和聊天记录中已有的事实",
            "- 如果你没有记忆依据，不要假装你记得某事",
            "- 禁止编造对方的行为、地点、穿着等具体细节",
            "",
            "## 正确例子",
            "- Agent项目还活着吗",
            "- 考试结束没",
            "- 你那个bug搞定了吗",
            "- 今天不会又在摸鱼吧",
            "- 听说你最近熬夜了",
            "",
            "## 禁止生成（绝对不要说这些）",
            "- 在吗",
            "- 最近怎么样",
            "- 干嘛呢",
            "- 忙吗",
            "- 最近还好吗",
            "- 吃了吗",
            "- 睡了没",
            "- 想你了",
            "- 好久不见",
            "- 今天天气不错",
            "- 「上次你xxx」「我记得你xxx」「你之前xxx」等虚构回忆",
            "- 任何不依赖上下文的通用问候",
        ])

        prompt = "\n".join(prompt_parts)

        config = LLMConfig.from_mapping()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"你想对 {nickname} 说点什么？"},
        ]

        try:
            text = await LLMClient(config).chat_text(messages)
        except Exception:
            logger.warning("ConversationStarter: LLM 调用失败")
            return None

        if not text or not text.strip():
            return None

        result = text.strip()
        for phrase in BANNED_PHRASES:
            if phrase in result:
                logger.warning(f"ConversationStarter: 含禁止短语 '{phrase}'，丢弃")
                return None

        return result

    @staticmethod
    def _persona_hint(identity: str) -> str:
        if identity == "yqy":
            return "风格：可以吐槽玩梗、阴阳怪气，像老朋友一样随意。"
        if identity == "妹妹":
            return "风格：温和关心，像个靠谱的哥哥，少攻击性。"
        return "风格：普通朋友聊天。"


# 向后兼容别名
ActiveChatGenerator = ConversationStarterService
