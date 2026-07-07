from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


class NapCatAPIError(RuntimeError):
    """NapCat API 错误异常类。

    当 NapCat API 请求失败或返回错误状态时抛出此异常。
    """

    def __init__(self, message: str, *, path: str, status: str | None = None, retcode: int | None = None) -> None:
        """初始化 NapCat API 错误。

        Args:
            message: 错误消息文本
            path: API 请求路径
            status: API 返回的状态字符串，可选
            retcode: API 返回的错误码，可选
        """
        super().__init__(message)
        self.path = path
        self.status = status
        self.retcode = retcode


@dataclass(slots=True)
class NapCatClient:
    base_url: str
    access_token: str = ""
    timeout: float = 8.0

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """发送 POST 请求到 NapCat API。

        Args:
            path: API 路径（如 "send_msg"）
            payload: 请求载荷字典

        Returns:
            API 返回的 JSON 响应字典

        Raises:
            NapCatAPIError: 当请求失败或返回错误状态时抛出
        """
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        response = await asyncio.to_thread(self._post_sync, url, payload)
        self._check_response(path, response)
        return response

    async def call_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """调用 NapCat API 动作（与 post 方法等效）。

        Args:
            action: 动作名称（如 "get_msg"）
            payload: 请求载荷字典

        Returns:
            API 返回的 JSON 响应字典
        """
        return await self.post(action, payload)

    def _post_sync(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """同步发送 POST 请求（在线程池中执行）。

        Args:
            url: 完整的 API URL
            payload: 请求载荷字典

        Returns:
            API 返回的 JSON 响应字典

        Raises:
            NapCatAPIError: 当 HTTP 错误或 JSON 解析失败时抛出
        """
        import urllib.error
        import urllib.request

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            LOGGER.debug("NapCat HTTP error path=%s status=%s body=%s", url, exc.code, raw)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as err:
                raise NapCatAPIError(
                    f"NapCat HTTP error on {url}: {exc.code}",
                    path=url,
                    status=str(exc.code),
                ) from err
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NapCatAPIError(f"NapCat returned invalid JSON from {url}", path=url) from exc

    def _check_response(self, path: str, payload: dict[str, Any]) -> None:
        """检查 API 响应状态，确认请求成功。

        Args:
            path: API 请求路径
            payload: API 返回的响应字典

        Raises:
            NapCatAPIError: 当响应状态不为 "ok" 或 retcode 不为 0 时抛出
        """
        status = str(payload.get("status", ""))
        retcode = payload.get("retcode")
        retcode_int = int(retcode) if isinstance(retcode, int) or str(retcode).isdigit() else None
        if status and status != "ok":
            raise NapCatAPIError(f"NapCat request failed: {path}", path=path, status=status, retcode=retcode_int)
        if retcode_int is not None and retcode_int != 0:
            raise NapCatAPIError(f"NapCat request failed: {path}", path=path, status=status or None, retcode=retcode_int)
