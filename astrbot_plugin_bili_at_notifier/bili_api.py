import asyncio
import json

import aiohttp

from astrbot.api import logger

API_BASE_URL = "https://api.bilibili.com"
AT_FEED_URL = f"{API_BASE_URL}/x/msgfeed/at"

class BiliApiClient:
    """简化的 Bilibili API 客户端，用于获取 @ 消息"""

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        user_agent: str,
        timeout: int = 30,
    ):
        if not sessdata or not bili_jct:
            raise ValueError("请提供 SESSDATA 和 bili_jct。")

        self._cookies = {
            "SESSDATA": sessdata,
            "bili_jct": bili_jct,
        }
        self._headers = {
            "User-Agent": user_agent,
            "Referer": "https://message.bilibili.com/", # 增加 Referer
            "Origin": "https://message.bilibili.com", # 增加 Origin
        }
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookies=self._cookies,
                headers=self._headers,
                timeout=self._timeout,
                raise_for_status=False, # 手动处理HTTP错误
            )
        return self._session

    async def _safe_json_from_response(self, response: aiohttp.ClientResponse) -> dict | None:
        """安全解析 JSON"""
        try:
            text = await response.text()
            return json.loads(text)
        except (json.JSONDecodeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            preview = text[:200] if isinstance(text, str) else str(text)[:200]
            logger.error(f"解析 B站 API JSON 失败: status={response.status}, url='{response.url}', 片段='{preview}', 错误: {e}")
            return None

    async def get_at_mentions(self, cursor_id: int | None = None, cursor_time: int | None = None) -> dict | None:
        """获取 @ 我 的消息"""
        session = await self._get_session()
        params = {
            "platform": "web",
            "build": "0",
            "mobi_app": "web",
            "web_location": "333.40164" # 根据抓包结果固定
        }
        if cursor_id is not None and cursor_time is not None:
            params["id"] = cursor_id
            params["time"] = cursor_time
            logger.debug(f"请求下一页 @ 消息: cursor_id={cursor_id}, cursor_time={cursor_time}")
        else:
            logger.debug("请求第一页 @ 消息")

        try:
            async with session.get(AT_FEED_URL, params=params) as response:
                if response.status == 200:
                    data = await self._safe_json_from_response(response)
                    if isinstance(data, dict) and data.get("code") == 0:
                        logger.debug(f"成功获取 @ 消息，数量: {len(data.get('data', {}).get('items', []))}")
                        return data.get("data")
                    elif isinstance(data, dict) and data.get("code") == -101:
                         logger.error(f"获取 Bilibili @ 消息失败: Cookie 失效或未登录 ({data})")
                         return None # Cookie失效
                    else:
                        logger.error(f"获取 Bilibili @ 消息 API 返回错误: {data}")
                        return None
                else:
                    body_preview = await response.text()
                    logger.error(f"获取 Bilibili @ 消息 HTTP 错误: {response.status}, message='{response.reason}', url='{response.url}', body='{body_preview[:200]}'")
                    return None
        except asyncio.CancelledError:
             raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"请求 Bilibili @ 消息时发生网络错误: {e}")
            return None
        except Exception as e:
            logger.error(f"获取 Bilibili @ 消息时发生未知错误: {e}", exc_info=True)
            return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Bilibili API Client session closed.")
