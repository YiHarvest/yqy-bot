from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - optional dependency for smoke tests
    from openai import AsyncOpenAI, OpenAIError
except ModuleNotFoundError:  # pragma: no cover - optional dependency for smoke tests
    AsyncOpenAI = Any  # type: ignore[assignment]

    class OpenAIError(RuntimeError):
        pass


ROLE_ENV_MAP = {
    "chat": "CHAT",
    "intent": "INTENT",
    "reason": "REASON",
    "vision": "VISION",
    "background": "BACKGROUND",
    "long_context": "LONG_CONTEXT",
}


@dataclass(slots=True)
class RoleSettings:
    base_url: str | None
    api_key: str | None
    model: str | None
    temperature: float = 0.7
    max_tokens: int = 512

    def is_configured(self) -> bool:
        """检查角色设置是否已配置完整的 API 密钥和模型。

        Returns:
            如果配置了 API 密钥和模型则返回 True，否则返回 False
        """
        return bool(self.api_key and self.model)


class LLMRouter:
    def __init__(self) -> None:
        self._clients: dict[str, AsyncOpenAI] = {}

    def settings_for(self, role: str) -> RoleSettings:
        """获取指定角色的 LLM 设置。

        Args:
            role: 角色名称，如 "chat"、"intent"、"reason" 等

        Returns:
            该角色的设置对象，包含 base_url、api_key、model 等字段
        """
        prefix = ROLE_ENV_MAP[role]
        return RoleSettings(
            base_url=_env(f"{prefix}_OPENAI_BASE_URL"),
            api_key=_env(f"{prefix}_OPENAI_API_KEY"),
            model=_env(f"{prefix}_OPENAI_MODEL"),
            temperature=float(_env(f"{prefix}_OPENAI_TEMPERATURE", "0.7")),
            max_tokens=int(_env(f"{prefix}_OPENAI_MAX_TOKENS", "512")),
        )

    def available(self, role: str) -> bool:
        """检查指定角色的 LLM 是否可用。

        Args:
            role: 角色名称

        Returns:
            如果该角色已配置且可用则返回 True，否则返回 False
        """
        return self.settings_for(role).is_configured()

    async def chat_text(
        self,
        role: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """使用指定角色的 LLM 进行文本对话。

        Args:
            role: 角色名称
            messages: 消息列表，每条消息包含 role 和 content 字段
            temperature: 温度参数，控制回复的随机性，可选
            max_tokens: 最大 token 数，可选

        Returns:
            LLM 生成的文本回复内容

        Raises:
            RuntimeError: 当模型请求失败或未配置时抛出
        """
        settings = self.settings_for(role)
        client = self._client_for(role, settings)
        try:
            response = await client.chat.completions.create(
                model=settings.model,
                messages=messages,
                temperature=settings.temperature if temperature is None else temperature,
                max_tokens=settings.max_tokens if max_tokens is None else max_tokens,
            )
        except OpenAIError as exc:  # pragma: no cover - network/runtime dependent
            raise RuntimeError(f"{role} model request failed: {exc}") from exc
        content = response.choices[0].message.content or ""
        return content.strip()

    async def chat_json(
        self,
        role: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """使用指定角色的 LLM 进行 JSON 对话，强制返回 JSON 格式。

        Args:
            role: 角色名称
            messages: 消息列表
            temperature: 温度参数，可选
            max_tokens: 最大 token 数，可选

        Returns:
            LLM 生成的 JSON 对象（字典形式）

        Raises:
            RuntimeError: 当模型请求失败、未配置或返回无效 JSON 时抛出
        """
        settings = self.settings_for(role)
        client = self._client_for(role, settings)
        try:
            response = await client.chat.completions.create(
                model=settings.model,
                messages=messages,
                temperature=settings.temperature if temperature is None else temperature,
                max_tokens=settings.max_tokens if max_tokens is None else max_tokens,
                response_format={"type": "json_object"},
            )
        except OpenAIError as exc:  # pragma: no cover - network/runtime dependent
            raise RuntimeError(f"{role} model request failed: {exc}") from exc
        content = response.choices[0].message.content or "{}"
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{role} model returned invalid JSON: {content}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{role} model returned non-object JSON")
        return payload

    def _client_for(self, role: str, settings: RoleSettings) -> AsyncOpenAI:
        """获取或创建指定角色的 OpenAI 客户端实例。

        Args:
            role: 角色名称
            settings: 该角色的设置对象

        Returns:
            AsyncOpenAI 客户端实例

        Raises:
            RuntimeError: 当角色未配置时抛出
        """
        if not settings.is_configured():
            raise RuntimeError(
                f"{role} model is not configured; set {ROLE_ENV_MAP[role]}_OPENAI_* env vars"
            )
        client = self._clients.get(role)
        if client is None:
            kwargs: dict[str, Any] = {"api_key": settings.api_key}
            if settings.base_url:
                kwargs["base_url"] = settings.base_url
            client = AsyncOpenAI(**kwargs)
            self._clients[role] = client
        return client


def _env(name: str, default: str | None = None) -> str | None:
    """获取环境变量值，空字符串视为未设置。

    Args:
        name: 环境变量名
        default: 默认值，可选

    Returns:
        环境变量值，如果未设置或为空则返回默认值
    """
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value
