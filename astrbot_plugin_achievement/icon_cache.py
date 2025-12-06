import hashlib
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from astrbot.api import logger


class IconCacheManager:
    """
    负责管理网络图标的本地缓存。
    - 异步下载图片。
    - 使用 URL 的哈希值作为文件名，确保唯一性。
    - 如果下载失败，提供一个默认的失败图标路径。
    """
    def __init__(self, cache_dir: str, aiohttp_session: aiohttp.ClientSession, fallback_icon_path: str):
        self.cache_path = Path(cache_dir)
        self.session = aiohttp_session
        self.fallback_icon_path = fallback_icon_path

        # 确保缓存目录存在
        self.cache_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"图标缓存系统已初始化，缓存目录: {self.cache_path}")

    def _url_to_filename(self, url: str) -> str:
        """根据URL生成一个安全且唯一的文件名。"""
        # 提取原始文件扩展名，如果不存在则默认为 .png
        try:
            path = urlparse(url).path
            ext = Path(path).suffix or ".png"
            if len(ext) > 5: # 防止过长的后缀
                ext = ".png"
        except Exception:
            ext = ".png"

        # 使用 SHA1 哈希确保文件名唯一且长度固定
        url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return f"{url_hash}{ext}"

    async def get_local_path(self, url: str) -> str:
        """
        获取 URL 对应的本地缓存路径。
        如果本地不存在，则下载并缓存它。
        """
        filename = self._url_to_filename(url)
        local_path = self.cache_path / filename

        if local_path.exists():
            # logger.info(f"命中图标缓存: {url} -> {local_path}")
            return str(local_path)

        # 缓存未命中，开始下载
        logger.info(f"缓存未命中，正在下载图标: {url}")
        try:
            async with self.session.get(url, timeout=15) as response:
                response.raise_for_status()  # 如果状态码不是 2xx，则抛出异常
                content = await response.read()

                with open(local_path, "wb") as f:
                    f.write(content)

                logger.info(f"图标已成功缓存至: {local_path}")
                return str(local_path)

        except Exception as e:
            logger.error(f"下载或缓存图标失败: {url}。错误: {e}")
            # 下载失败，返回预设的锁图标/失败图标路径
            return self.fallback_icon_path
