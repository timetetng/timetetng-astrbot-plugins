import hashlib
from datetime import datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING

import jwt
from aiohttp import web
from passlib.context import CryptContext

from .config import JWT_ALGORITHM, JWT_SECRET_KEY

# 仅用于类型提示，避免循环导入
if TYPE_CHECKING:
    from .models import VirtualStock  # <-- 这里是添加的关键导入
    from .web_server import WebServer

# --- 安全与认证 ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def jwt_required(handler):
    """JWT Token 验证装饰器"""

    @wraps(handler)
    async def wrapper(web_server_instance: "WebServer", request: web.Request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return web.json_response({"error": "未提供认证Token"}, status=401)

        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            request["jwt_payload"] = payload
        except jwt.ExpiredSignatureError:
            return web.json_response({"error": "Token已过期"}, status=401)
        except jwt.InvalidTokenError:
            return web.json_response({"error": "无效的Token"}, status=401)

        return await handler(web_server_instance, request)

    return wrapper


# --- 数据格式化与生成 ---
def generate_user_hash(user_id: str) -> str:
    """根据用户ID生成唯一的、URL友好的哈希字符串。"""
    if not isinstance(user_id, str):
        user_id = str(user_id)
    hash_object = hashlib.md5(user_id.encode("utf-8"))
    return hash_object.hexdigest()[:10]


def format_large_number(num: float) -> str:
    """将一个较大的数字格式化为带有 K, M, B, T, Q 后缀的易读字符串。"""
    if num is None:
        return "0.00"
    suffixes = {
        1_000_000_000_000_000: "Q",
        1_000_000_000_000: "T",
        1_000_000_000: "B",
        1_000_000: "M",
        1_000: "K",
    }
    for magnitude, suffix in suffixes.items():
        if abs(num) >= magnitude:
            value = num / magnitude
            return f"{value:.2f} {suffix}"
    return f"{num:,.2f}"


# --- 为 LLM Tools 新增的数据处理函数 ---


def get_price_change_percentage_30m(stock: "VirtualStock") -> float:
    """
    根据股票内存中的K线数据，计算最近30分钟的涨跌幅。
    """
    if not stock.kline_history:
        return 0.0

    now = datetime.now()
    thirty_minutes_ago = now - timedelta(minutes=30)

    # 找到30分钟前最接近的价格点
    reference_price = None
    for kline in reversed(stock.kline_history):
        kline_time = datetime.fromisoformat(kline["date"])
        if kline_time <= thirty_minutes_ago:
            reference_price = kline["close"]
            break

    # 如果没有30分钟前的数据，就用最早的数据
    if reference_price is None and stock.kline_history:
        reference_price = stock.kline_history[0]["close"]

    if reference_price is None or reference_price == 0:
        return 0.0

    return ((stock.current_price - reference_price) / reference_price) * 100


def get_stock_price_history_24h(stock: "VirtualStock") -> list[tuple[datetime, float]]:
    """
    从股票内存中的K线数据，提取过去24小时内每小时的价格点。
    """
    if not stock.kline_history:
        return []

    now = datetime.now()
    twenty_four_hours_ago = now - timedelta(hours=24)

    # 筛选出过去24小时的数据
    relevant_history = [
        k
        for k in stock.kline_history
        if datetime.fromisoformat(k["date"]) >= twenty_four_hours_ago
    ]

    if not relevant_history:
        return []

    # 按小时聚合数据
    hourly_prices = {}
    for kline in relevant_history:
        k_time = datetime.fromisoformat(kline["date"])
        hour_timestamp = k_time.replace(minute=0, second=0, microsecond=0)
        # 只记录每个小时的最后一次收盘价
        hourly_prices[hour_timestamp] = kline["close"]

    # 转换为列表并排序
    sorted_history = sorted(hourly_prices.items())

    # 将 datetime 对象和价格作为元组返回
    return [(ts, price) for ts, price in sorted_history]
