"""MCP 搜索客户端，通过 stdio 调用 search-engine-tool-mcp。"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import time
from typing import Any

from yqy_bot.core.models import SearchResult, SearchMCPSettings
from yqy_bot.tools.search_policy import _sanitize_search_query

LOGGER = logging.getLogger(__name__)
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "yqy-bot"
MCP_CLIENT_VERSION = "0.1.0"


class SearchMCPClient:
    """MCP 搜索客户端，封装 web_search 和 web_extract 调用。"""

    def __init__(self, settings: SearchMCPSettings) -> None:
        """初始化 MCP 搜索客户端。

        Args:
            settings: 搜索配置对象
        """
        self.settings = settings
        self._process: asyncio.subprocess.Process | None = None

    async def search(self, query: str) -> SearchResult:
        """执行网页搜索。

        Args:
            query: 搜索查询字符串

        Returns:
            SearchResult 对象，包含搜索结果或错误信息
        """
        if not self.settings.enabled:
            return SearchResult(
                ok=False,
                type="web_search",
                query=query,
                error="search_disabled",
                message="搜索功能未启用",
            )

        query = _sanitize_search_query(query)
        start_time = time.time()
        try:
            result = await self._call_mcp_tool(
                "web_search",
                {
                    "query": query,
                    "provider": self.settings.provider,
                    "max_results": self.settings.max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                },
            )
            elapsed = time.time() - start_time

            if not result.get("ok", False):
                error = result.get("error", "unknown_error")
                message = result.get("message", "搜索失败")
                LOGGER.warning(
                    "[搜索工具] query=%s error=%s elapsed=%.2fs",
                    query[:50],
                    error,
                    elapsed,
                )
                return SearchResult(
                    ok=False,
                    type="web_search",
                    query=query,
                    error=str(error),
                    message=str(message),
                )

            results = result.get("results", [])
            provider = result.get("provider", self.settings.provider)

            LOGGER.info(
                "[搜索工具] query=%s provider=%s count=%d elapsed=%.2fs",
                query[:50],
                provider,
                len(results),
                elapsed,
            )

            return SearchResult(
                ok=True,
                type="web_search",
                query=query,
                provider=str(provider),
                results=list(results),
            )

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            LOGGER.warning(
                "[搜索工具] query=%s timeout elapsed=%.2fs", query[:50], elapsed
            )
            return SearchResult(
                ok=False,
                type="web_search",
                query=query,
                error="timeout",
                message="搜索超时，请稍后重试",
            )
        except Exception as exc:
            elapsed = time.time() - start_time
            LOGGER.warning(
                "[搜索工具] query=%s error=%s elapsed=%.2fs", query[:50], exc, elapsed
            )
            return SearchResult(
                ok=False,
                type="web_search",
                query=query,
                error=str(exc),
                message="搜索工具调用失败",
            )

    async def extract(self, url: str) -> SearchResult:
        """执行网页内容抽取。

        Args:
            url: 要抽取的 URL

        Returns:
            SearchResult 对象，包含抽取结果或错误信息
        """
        if not self.settings.enabled:
            return SearchResult(
                ok=False,
                type="web_extract",
                extract_url=url,
                error="search_disabled",
                message="搜索功能未启用",
            )

        url = url.strip()
        start_time = time.time()
        try:
            result = await self._call_mcp_tool(
                "web_extract",
                {
                    "url": url,
                    "provider": "auto",
                },
            )
            elapsed = time.time() - start_time

            if not result.get("ok", False):
                error = result.get("error", "unknown_error")
                message = result.get("message", "抽取失败")
                LOGGER.warning(
                    "[搜索工具] extract_url=%s error=%s elapsed=%.2fs",
                    url[:80],
                    error,
                    elapsed,
                )
                return SearchResult(
                    ok=False,
                    type="web_extract",
                    query=url,
                    error=str(error),
                    message=str(message),
                )

            content = result.get("content", "")
            LOGGER.info(
                "[搜索工具] extract_url=%s content_len=%d elapsed=%.2fs",
                url[:80],
                len(content),
                elapsed,
            )

            return SearchResult(
                ok=True,
                type="web_extract",
                query=url,
                provider="extract",
                results=[{"url": url, "content": content}],
            )

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            LOGGER.warning(
                "[搜索工具] extract_url=%s timeout elapsed=%.2fs", url[:80], elapsed
            )
            return SearchResult(
                ok=False,
                type="web_extract",
                query=url,
                error="timeout",
                message="网页抽取超时",
            )
        except Exception as exc:
            elapsed = time.time() - start_time
            LOGGER.warning(
                "[搜索工具] extract_url=%s error=%s elapsed=%.2fs",
                url[:80],
                exc,
                elapsed,
            )
            return SearchResult(
                ok=False,
                type="web_extract",
                query=url,
                error=str(exc),
                message="网页抽取失败",
            )

    async def search_or_extract(self, decision: Any) -> SearchResult:
        """根据决策执行搜索或抽取。

        Args:
            decision: SearchDecision 对象

        Returns:
            SearchResult 对象
        """
        if decision.need_extract and decision.extract_url:
            return await self.extract(decision.extract_url)
        if decision.need_search and decision.search_query:
            return await self.search(decision.search_query)
        return SearchResult(
            ok=False,
            type="",
            error="no_action",
            message="无需搜索或抽取",
        )

    async def _call_mcp_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """调用 MCP 工具。

        通过 stdio 与 MCP server 通信，使用 JSON-RPC 协议。

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具返回结果

        Raises:
            asyncio.TimeoutError: 调用超时
            RuntimeError: MCP 进程启动失败
        """
        command_args = self._build_command_args()
        timeout = self.settings.timeout_seconds
        process: asyncio.subprocess.Process | None = None

        try:
            process = await self._start_process(command_args)
            await self._initialize_session(process, timeout=timeout)

            request_id = str(int(time.time() * 1000))
            response = await self._send_request(
                process,
                method="tools/call",
                params={
                    "name": tool_name,
                    "arguments": arguments,
                },
                request_id=request_id,
                timeout=timeout,
            )
            return self._normalize_tool_response(response)
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "command_not_found",
                "message": f"MCP 命令未找到: {' '.join(command_args)}",
            }
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "error": "json_decode_error",
                "message": f"MCP 响应解析失败: {exc}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(type(exc).__name__),
                "message": str(exc),
            }
        finally:
            if process is not None:
                await self._shutdown_process(process)

    def _build_command_args(self) -> list[str]:
        """将配置里的 command 解析为可执行参数列表。"""
        command = (self.settings.command or "").strip()
        if not command:
            return ["search-engine-tool-mcp"]
        return shlex.split(command)

    async def _start_process(
        self, command_args: list[str]
    ) -> asyncio.subprocess.Process:
        """启动 MCP 子进程。

        只使用配置中的命令。Docker 启动时已经通过 `uv sync`
        安装依赖，自动回退到 uvx 会引入临时下载和额外网络风险。
        """
        LOGGER.info("[搜索工具] 启动 MCP command=%s", " ".join(command_args))
        return await asyncio.create_subprocess_exec(
            *command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _initialize_session(
        self, process: asyncio.subprocess.Process, *, timeout: float
    ) -> None:
        """按 MCP 生命周期完成初始化握手。"""
        init_result = await self._send_request(
            process,
            method="initialize",
            params={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "clientInfo": {
                    "name": MCP_CLIENT_NAME,
                    "version": MCP_CLIENT_VERSION,
                },
            },
            request_id="initialize",
            timeout=timeout,
        )
        if isinstance(init_result, dict) and init_result.get("error"):
            LOGGER.debug("[搜索工具] MCP initialize returned error: %s", init_result)
            return
        await self._send_notification(
            process,
            method="notifications/initialized",
        )

    async def _send_notification(
        self,
        process: asyncio.subprocess.Process,
        *,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """发送 MCP 通知消息。"""
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._write_message(process, payload)

    async def _send_request(
        self,
        process: asyncio.subprocess.Process,
        *,
        method: str,
        params: dict[str, Any],
        request_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        """发送 JSON-RPC 请求并等待匹配的响应。"""
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        await self._write_message(process, payload)
        return await self._read_response(
            process, request_id=request_id, timeout=timeout
        )

    async def _write_message(
        self, process: asyncio.subprocess.Process, payload: dict[str, Any]
    ) -> None:
        """按当前 Python MCP stdio 传输格式写入消息。"""
        if process.stdin is None:
            raise RuntimeError("MCP process stdin is unavailable")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        process.stdin.write(body + b"\n")
        await process.stdin.drain()

    async def _read_response(
        self,
        process: asyncio.subprocess.Process,
        *,
        request_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        """读取匹配 request_id 的 JSON-RPC 响应。"""
        if process.stdout is None:
            raise RuntimeError("MCP process stdout is unavailable")

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            message = await asyncio.wait_for(
                self._read_message(process), timeout=remaining
            )
            if message is None:
                raise RuntimeError("MCP 工具返回空响应")
            if str(message.get("id", "")) != str(request_id):
                # 忽略初始化期间或工具执行期间的通知消息
                if "method" in message and "id" not in message:
                    continue
                continue
            return message

    async def _read_message(
        self, process: asyncio.subprocess.Process
    ) -> dict[str, Any] | None:
        """读取一个 MCP stdio JSON 行消息。"""
        if process.stdout is None:
            return None
        while True:
            line = await process.stdout.readline()
            if not line:
                return None
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            try:
                return json.loads(decoded)
            except json.JSONDecodeError:
                LOGGER.debug("[搜索工具] 忽略非 JSON stdout: %s", decoded[:200])

    async def _shutdown_process(self, process: asyncio.subprocess.Process) -> None:
        """尽量优雅地关闭 MCP 进程。"""
        if process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _normalize_tool_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """把 MCP 响应转换成当前业务代码可消费的结构。"""
        if "error" in response:
            error = response.get("error", {})
            if isinstance(error, dict):
                return {
                    "ok": False,
                    "error": error.get("code", "rpc_error"),
                    "message": error.get("message", "MCP 调用失败"),
                }
            return {
                "ok": False,
                "error": "rpc_error",
                "message": "MCP 调用失败",
            }

        result = response.get("result", {})
        if not isinstance(result, dict):
            return {
                "ok": False,
                "error": "invalid_result",
                "message": "MCP 返回结果格式不正确",
            }

        content = result.get("content", [])
        if isinstance(content, list) and content:
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text", ""))
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        return {
                            "ok": True,
                            "content": text,
                            "provider": self.settings.provider,
                        }
                    if isinstance(parsed, dict):
                        if "ok" not in parsed:
                            parsed["ok"] = not bool(result.get("isError", False))
                        return parsed
                    if isinstance(parsed, list):
                        return {
                            "ok": True,
                            "results": parsed,
                            "provider": self.settings.provider,
                        }
                    return {
                        "ok": True,
                        "content": text,
                        "provider": self.settings.provider,
                    }

        return {
            "ok": True,
            "results": [],
            "provider": self.settings.provider,
        }
