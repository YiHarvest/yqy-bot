"""关系推理服务：基于长期记忆和反思，生成跨记忆的推理结论。
推理结果存入 reflection 表，不新增数据库表。
"""

from __future__ import annotations

from iamai import LLMClient, LLMConfig
from loguru import logger

from .config_service import MAX_REFLECTIONS_PER_DAY
from .reflection import ReflectionService
from .memory import MemoryService


class RelationshipReasoningService:
    """对长期记忆和反思做高阶推理，生成洞察。"""

    def __init__(self) -> None:
        self._reflection = ReflectionService()
        self._memory = MemoryService()

    async def generate_reasoning(self, user_id: str) -> None:
        """根据用户记忆+已有反思，生成一条推理结论。

        限制：每日最多 MAX_REFLECTIONS_PER_DAY 条（复用反思配额）。
        """
        today_count = self._reflection.count_today()
        if today_count >= MAX_REFLECTIONS_PER_DAY:
            logger.debug(f"推理：今日配额已满({MAX_REFLECTIONS_PER_DAY})，跳过")
            return

        memories = self._memory.get_memories(user_id, limit=20)
        recent_reflections = self._reflection.get_recent(limit=5)

        if len(memories) < 3:
            logger.debug("推理：长期记忆不足3条，跳过")
            return

        memory_text = "\n".join(f"- {m}" for m in memories)
        reflection_text = (
            "\n".join(f"- {r}" for r in recent_reflections)
            if recent_reflections
            else "（暂无反思）"
        )

        config = LLMConfig.from_mapping()
        prompt = (
            "你是 YHarvest，你在整理自己对朋友们的观察。\n\n"
            "## 你记得的事实：\n"
            f"{memory_text}\n\n"
            "## 你已有的观察：\n"
            f"{reflection_text}\n\n"
            "基于以上信息，推理出一条更高阶的判断。\n\n"
            "## 要求：\n"
            "- 是推理和判断，不是事实复述\n"
            "- 多条记忆交叉印证后得出结论\n"
            "- 写成第一人称内部想法\n"
            "- 禁止编造不存在的经历或事件\n"
            "- 推理只能基于上面列出的事实，不要补充你不知道的信息\n\n"
            "## 正确例子：\n"
            "- yqy最近工作强度比较大\n"
            "- 妹妹最近压力可能较大\n"
            "- yqy最近睡眠可能不太够\n\n"
            "## 错误例子：\n"
            "- yqy说他在开发Agent（这是事实复述）\n"
            "- yqy最近对Agent开发感兴趣（这是简单观察）\n"
            "- yqy上次熬夜到凌晨三点（编造的细节）\n\n"
            '如果信息不足以做推理，返回 {"save": false}。\n'
            '返回 JSON：{"save": true/false, "reflection": "推理内容", "importance": 0.1~1.0}'
        )
        try:
            result = await LLMClient(config).chat_json(
                [{"role": "user", "content": prompt}]
            )
        except Exception:
            logger.warning("推理生成失败：LLM 调用异常")
            return

        if result.get("save") and result.get("reflection"):
            reflection = str(result["reflection"]).strip()
            importance = float(result.get("importance", 0.5))
            self._reflection.save_reflection(reflection, importance)
            logger.info(f"推理保存: {reflection[:60]}")
