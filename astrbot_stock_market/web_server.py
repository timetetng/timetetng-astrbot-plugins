import ipaddress
import json
import random
import re
import time
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import aiohttp_jinja2
import jwt
import pandas as pd
from aiohttp import web
from jinja2 import FileSystemLoader, select_autoescape

from astrbot.api import logger

from .config import (
    JWT_ALGORITHM,
    JWT_EXPIRATION_MINUTES,
    JWT_SECRET_KEY,
    RATE_LIMIT_WHITELIST,
    SERVER_BASE_URL,
    SERVER_PORT,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from .utils import generate_user_hash, jwt_required, pwd_context

if TYPE_CHECKING:
    from .main import StockMarketRefactored


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    """
    一个原生的 aiohttp 速率限制中间件 (升级版：支持正则表达式路径匹配和IP白名单)。
    """
    server_instance = request.app["server_instance"]

    # 检查IP白名单
    # 在执行任何速率限制逻辑之前，首先检查请求的IP是否在白名单中。
    remote_ip = request.remote
    if remote_ip:
        try:
            # 将请求的IP地址字符串转换为ip_address对象
            request_ip_obj = ipaddress.ip_address(remote_ip)
            # 遍历白名单中的每一个规则 (IP或网段)
            for whitelisted_net in RATE_LIMIT_WHITELIST:
                # 检查请求IP是否属于该规则定义的网络范围
                if request_ip_obj in ipaddress.ip_network(
                    whitelisted_net, strict=False
                ):
                    # 如果IP在白名单内，则直接放行，不执行后续的速率限制检查
                    return await handler(request)
        except ValueError as e:
            # 如果配置中的白名单格式错误或IP地址无效，记录日志但服务不中断
            logger.error(
                f"处理速率限制白名单时出错: {e}. 请检查 config.py 中的 RATE_LIMIT_WHITELIST 配置。"
            )

    # (原有的速率限制逻辑保持不变)
    for rule in server_instance.rate_limit_rules:
        if re.match(rule["path_regex"], request.path):
            key = rule["get_key_func"](request)
            limit = rule["limit"]
            period = rule["period"]

            current_time = time.monotonic()
            timestamps: deque[float] = server_instance.rate_limit_storage.setdefault(
                key, deque()
            )

            while timestamps and timestamps[0] <= current_time - period:
                timestamps.popleft()

            if len(timestamps) >= limit:
                logger.warning(
                    f"速率限制触发！Key: '{key}', Path: '{request.path}', Rule: {rule['path_regex']}"
                )
                return web.Response(
                    status=429,
                    text=json.dumps(
                        {
                            "error": "Too Many Requests",
                            "message": "Rate limit exceeded.",
                        }
                    ),
                    content_type="application/json",
                )

            timestamps.append(current_time)
            break

    return await handler(request)


class WebServer:
    def _get_ip_key(self, request: web.Request) -> str:
        """根据请求者的 IP 地址生成 Key"""
        return request.remote or "127.0.0.1"

    def _get_user_key(self, request: web.Request) -> str:
        """优先根据已登录用户的ID进行限速，否则根据IP"""
        if "jwt_payload" in request and "sub" in request["jwt_payload"]:
            return str(request["jwt_payload"]["sub"])
        return self._get_ip_key(request)

    def __init__(self, plugin: "StockMarketRefactored"):
        self.plugin = plugin

        self.app = web.Application(middlewares=[rate_limit_middleware])
        self.app["server_instance"] = self

        self.rate_limit_storage: dict[str, deque[float]] = {}

        self.rate_limit_rules = [
            {
                "path_regex": r"^/api/auth/.*",
                "limit": 10,
                "period": 60,
                "get_key_func": self._get_ip_key,
            },
            {
                "path_regex": r"^/api/v1/trade/.*",
                "limit": 30,
                "period": 60,
                "get_key_func": self._get_user_key,
            },
            {
                "path_regex": r"^/api/v1/stock/[^/]+/details$",
                "limit": 5,
                "period": 60,
                "get_key_func": self._get_ip_key,
            },
            {
                "path_regex": r"^/api/.*",
                "limit": 60,
                "period": 60,
                "get_key_func": self._get_ip_key,
            },
        ]

        self.runner = None
        self._setup_jinja_and_routes()

    def _setup_jinja_and_routes(self):
        """配置Jinja2环境和所有Web路由。"""

        def tojson_filter(obj):
            """一个更强大的 tojson 过滤器，用于模板渲染。"""
            return json.dumps(obj, ensure_ascii=False)

        aiohttp_jinja2.setup(
            self.app,
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html", "xml"]),
            enable_async=True,
            context_processors=[aiohttp_jinja2.request_processor],
            filters={"tojson": tojson_filter},
        )

        self.app.router.add_static("/static/", path=STATIC_DIR, name="static")

        api_v1 = web.Application()
        api_v1.router.add_get("/stock/{stock_id}", self._api_get_stock_info)
        api_v1.router.add_get(
            "/stock/{identifier}/details", self._api_get_stock_details
        )
        api_v1.router.add_get("/stocks", self._api_get_all_stocks)
        api_v1.router.add_get("/market/overview", self._api_get_market_overview)
        api_v1.router.add_get("/portfolio", self._api_get_user_portfolio)
        api_v1.router.add_post("/trade/buy", self._api_trade_buy)
        api_v1.router.add_post("/trade/sell", self._api_trade_sell)
        api_v1.router.add_post("/trade/buy_all_in", self._api_trade_buy_all_in)
        api_v1.router.add_post("/trade/sell_all_stock", self._api_trade_sell_all_stock)
        api_v1.router.add_post(
            "/trade/sell_all_portfolio", self._api_trade_sell_all_portfolio
        )
        api_v1.router.add_get("/ranking", self._api_get_ranking)
        self.app.add_subapp("/api/v1", api_v1)

        auth_app = web.Application()
        auth_app.router.add_post("/register", self._api_auth_register)
        auth_app.router.add_post("/login", self._api_auth_login)
        auth_app.router.add_post("/forgot-password", self._api_auth_forgot_password)
        auth_app.router.add_post("/reset-password", self._api_auth_reset_password)
        auth_app.router.add_get("/me/token", self._api_get_my_token)
        self.app.add_subapp("/api/auth", auth_app)

        # ▼▼▼ 新增根目录路由 ▼▼▼
        # 将根目录 / 指向新的游客模式处理函数
        self.app.router.add_get("/", self._handle_root_page)
        # ▲▲▲ 新增路由结束 ▲▲▲

        self.app.router.add_get("/charts/{user_hash}", self._handle_user_charts_page)
        self.app.router.add_get("/api/kline/{stock_id}", self._handle_kline_api)
        self.app.router.add_get("/api/get_user_hash", self._handle_get_user_hash)

        async def handle_favicon(request):
            return web.HTTPFound("/static/favicon.png")

        self.app.router.add_get("/favicon.ico", handle_favicon)

    async def start(self):
        """启动Web服务器。"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", SERVER_PORT)
        await site.start()
        logger.info(f"Web服务及API已在 {SERVER_BASE_URL} 上启动。")

    async def stop(self):
        """停止Web服务器。"""
        if self.runner:
            await self.runner.cleanup()
            logger.info("Web服务已关闭。")

    @aiohttp_jinja2.template("charts_page.html")
    async def _handle_root_page(self, request: web.Request):
        """处理根目录 / 的请求，展示游客模式的图表页面。"""
        # 1. 获取所有股票列表，用于下拉菜单
        stocks_list = sorted(
            [
                {"stock_id": s.stock_id, "name": s.name}
                for s in self.plugin.stocks.values()
            ],
            key=lambda x: x["stock_id"],
        )

        # 2. 在游客模式下，user_hash 和 user_portfolio_data 均为 None
        # 模板 charts_page.html 会自动处理 user_hash 为 None 的情况（即游客模式）
        user_hash = None
        user_portfolio_data = None

        return {
            "stocks": stocks_list,
            "user_hash": user_hash,
            "user_portfolio_data": user_portfolio_data,
        }

    @aiohttp_jinja2.template("charts_page.html")
    async def _handle_user_charts_page(self, request: web.Request):
        user_hash = request.match_info.get("user_hash")
        stocks_list = sorted(
            [
                {"stock_id": s.stock_id, "name": s.name}
                for s in self.plugin.stocks.values()
            ],
            key=lambda x: x["stock_id"],
        )
        user_id = None
        all_user_ids = await self.plugin.db_manager.get_all_user_ids_with_holdings()
        for uid in all_user_ids:
            if generate_user_hash(uid) == user_hash:
                user_id = uid
                break
        user_portfolio_data = None
        if user_id:
            asset_summary = await self.plugin.get_user_total_asset(user_id)
            user_portfolio_data = {
                "user_name": asset_summary.get("user_name", user_id),
                "holdings": asset_summary.get("holdings_detailed", []),
                "total": {
                    "market_value": asset_summary.get("stock_value", 0),
                    "pnl": asset_summary.get("total_pnl", 0),
                    "pnl_percent": asset_summary.get("total_pnl_percent", 0),
                },
            }
        return {
            "stocks": stocks_list,
            "user_hash": user_hash,
            "user_portfolio_data": user_portfolio_data,
        }

    async def _handle_kline_api(self, request: web.Request):
        stock_id = request.match_info.get("stock_id", "").upper()
        user_hash = request.query.get("user_hash")
        period = request.query.get("period", "1d")

        try:
            padding = int(request.query.get("padding", "0"))
        except (ValueError, TypeError):
            padding = 0

        stock = await self.plugin.find_stock(stock_id)
        if not stock or len(stock.kline_history) < 2:
            return web.json_response({"error": "not found"}, status=404)

        points_map = {"1d": 288, "7d": 288 * 7, "30d": 288 * 30}
        num_points = points_map.get(period, 288)
        total_points = num_points + padding
        kline_history_slice = list(stock.kline_history)[-total_points:]

        final_kline_data = kline_history_slice

        resample_rule = None
        if period == "30d":
            resample_rule = "H"
        elif period == "7d":
            resample_rule = "30T"

        if resample_rule and len(kline_history_slice) > 0:
            logger.info(
                f"为 {stock_id} 请求 {period} 数据 (padding={padding})，开始聚合为 {resample_rule} K线..."
            )

            df = pd.DataFrame(kline_history_slice)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

            df_resampled = (
                df.resample(resample_rule)
                .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                .dropna()
            )

            # ▼▼▼ 核心修复逻辑 ▼▼▼
            # 判断聚合和丢弃空值后，是否还有剩余数据
            if df_resampled.empty:
                # 如果聚合后数据为空，说明历史数据太稀疏，无法支撑当前时间维度的聚合。
                # 作为备用方案，我们返回未聚合的原始数据，确保前端不会收到空列表。
                logger.warning(
                    f"聚合后数据为空！为 {stock_id} 在 {period} 周期回退到原始K线数据。"
                )
                final_kline_data = (
                    kline_history_slice  # final_kline_data 已默认为此，这里为清晰起见
                )
            else:
                # 如果有数据，则正常处理
                aggregated_kline_history = [
                    {
                        "date": index.isoformat(),
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                    }
                    for index, row in df_resampled.iterrows()
                ]
                final_kline_data = aggregated_kline_history
                logger.info(
                    f"聚合完成，数据点从 {len(kline_history_slice)} 减少到 {len(final_kline_data)}。"
                )
            # ▲▲▲ 修复结束 ▲▲▲

        target_user_id = None
        if user_hash:
            # ... (后续代码无需修改) ...
            all_user_ids = await self.plugin.db_manager.get_all_user_ids_with_holdings()
            for uid in all_user_ids:
                if generate_user_hash(uid) == user_hash:
                    target_user_id = uid
                    break

        user_holdings = []
        if target_user_id:
            asset_info = await self.plugin.get_user_total_asset(target_user_id)
            for holding in asset_info.get("holdings_detailed", []):
                if holding["stock_id"] == stock_id:
                    user_holdings.append(
                        {
                            "stock_id": stock_id,
                            "quantity": holding["quantity"],
                            "avg_cost": holding["avg_cost"],
                        }
                    )

        return web.json_response(
            {"kline_history": final_kline_data, "user_holdings": user_holdings}
        )

    async def _handle_get_user_hash(self, request: web.Request):
        qq_id = request.query.get("qq_id")
        if not qq_id or not qq_id.isdigit():
            return web.json_response({"error": "无效的QQ号"}, status=400)
        return web.json_response({"user_hash": generate_user_hash(qq_id)})

    async def _api_get_stock_info(self, request: web.Request):
        stock_id = request.match_info.get("stock_id", "").upper()
        stock = await self.plugin.find_stock(stock_id)
        if not stock:
            return web.json_response({"error": "Stock not found"}, status=404)
        return web.json_response(
            {
                "stock_id": stock.stock_id,
                "name": stock.name,
                "current_price": stock.current_price,
                "previous_close": stock.previous_close,
                "industry": stock.industry,
                "volatility": stock.volatility,
            }
        )

    async def _api_get_stock_details(self, request: web.Request):
        """[API][Public] 获取单支股票的详细信息。"""
        identifier = request.match_info.get("identifier", "")
        stock_details = await self.plugin.get_stock_details_for_api(identifier)
        if not stock_details:
            return web.json_response(
                {"error": f'Stock with identifier "{identifier}" not found'}, status=404
            )
        return web.json_response(stock_details)

    async def _api_get_all_stocks(self, request: web.Request):
        stock_list = [
            {"stock_id": s.stock_id, "name": s.name, "current_price": s.current_price}
            for s in sorted(self.plugin.stocks.values(), key=lambda x: x.stock_id)
        ]
        return web.json_response(stock_list)

    async def _api_get_market_overview(self, request: web.Request):
        """[API][Public] 获取市场所有股票的详细行情概览。"""
        market_data = []

        for stock in self.plugin.stocks.values():
            kline = list(stock.kline_history)

            high_1h = None
            low_1h = None
            ma5 = None
            change_5m_value = None
            change_5m_percent = None
            trend = "数据不足"

            if kline:
                candles_last_hour = kline[-12:]
                high_1h = max(p["high"] for p in candles_last_hour)
                low_1h = min(p["low"] for p in candles_last_hour)

            if len(kline) >= 5:
                last_5_closes = [p["close"] for p in kline[-5:]]
                ma5 = sum(last_5_closes) / len(last_5_closes)

                if stock.current_price > ma5:
                    trend = "上涨"
                elif stock.current_price < ma5:
                    trend = "下跌"
                else:
                    trend = "震荡"

            if kline:
                price_5m_ago = kline[-1]["close"]
                if price_5m_ago > 0:
                    change_5m_value = stock.current_price - price_5m_ago
                    change_5m_percent = (change_5m_value / price_5m_ago) * 100

            stock_info = {
                "股票名称": stock.name,
                "代码": stock.stock_id,
                "当前价格": stock.current_price,
                "1小时内最高价": high_1h,
                "1小时内最低价": low_1h,
                "5周期均线": ma5,
                "较5分钟前涨跌": {
                    "value": change_5m_value,
                    "percent": change_5m_percent,
                },
                "短期趋势": trend,
            }
            market_data.append(stock_info)

        sorted_market_data = sorted(market_data, key=lambda x: x["代码"])
        return web.json_response(sorted_market_data)

    @jwt_required
    async def _api_trade_buy_all_in(self, request: web.Request):
        """[API][Private] 执行梭哈买入操作。"""
        try:
            data = await request.json()
            user_id = request["jwt_payload"]["sub"]
            identifier = data["stock_identifier"]
            success, message = await self.plugin.trading_manager.perform_buy_all_in(
                user_id, identifier
            )
            status = 200 if success else 400
            return web.json_response(
                {"success": success, "message": message}, status=status
            )
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return web.json_response(
                {"error": f'无效的请求体: {e}. 需要 {"stock_identifier": "..."}'},
                status=400,
            )

    @jwt_required
    async def _api_trade_sell_all_stock(self, request: web.Request):
        """[API][Private] 执行全抛单支股票的操作。"""
        try:
            data = await request.json()
            user_id = request["jwt_payload"]["sub"]
            identifier = data["stock_identifier"]
            (
                success,
                message,
            ) = await self.plugin.trading_manager.perform_sell_all_for_stock(
                user_id, identifier
            )
            status = 200 if success else 400
            return web.json_response(
                {"success": success, "message": message}, status=status
            )
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return web.json_response(
                {"error": f'无效的请求体: {e}. 需要 {"stock_identifier": "..."}'},
                status=400,
            )

    @jwt_required
    async def _api_trade_sell_all_portfolio(self, request: web.Request):
        """[API][Private] 执行清仓操作。"""
        user_id = request["jwt_payload"]["sub"]
        success, message = await self.plugin.trading_manager.perform_sell_all_portfolio(
            user_id
        )
        status = 200 if success else 400
        return web.json_response(
            {"success": success, "message": message}, status=status
        )

    @jwt_required
    async def _api_get_user_portfolio(self, request: web.Request):
        try:
            user_id = request["jwt_payload"]["sub"]
            display_name = await self.plugin.get_display_name(user_id)
            asset_summary = await self.plugin.get_user_total_asset(user_id)
            asset_summary["user_name"] = display_name
            return web.json_response(asset_summary)
        except Exception as e:
            user_id_for_log = request.get("jwt_payload", {}).get("sub", "未知用户")
            logger.error(f"获取用户 {user_id_for_log} 持仓时出错: {e}", exc_info=True)
            return web.json_response(
                {"error": "获取持仓信息时发生内部错误"}, status=500
            )

    async def _api_get_ranking(self, request: web.Request):
        limit = int(request.query.get("limit", 10))
        ranking_data = await self.plugin.get_total_asset_ranking(limit)
        return web.json_response(ranking_data)

    @jwt_required
    async def _api_trade_buy(self, request: web.Request):
        try:
            data = await request.json()
            user_id, stock_id, quantity = (
                request["jwt_payload"]["sub"],
                data["stock_id"].upper(),
                int(data["quantity"]),
            )
            success, message = await self.plugin.trading_manager.perform_buy(
                user_id, stock_id, quantity
            )
            status = 200 if success else 400
            return web.json_response(
                {"success": success, "message": message}, status=status
            )
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return web.json_response({"error": f"无效的请求体: {e}"}, status=400)

    @jwt_required
    async def _api_trade_sell(self, request: web.Request):
        try:
            data = await request.json()
            user_id, stock_id, quantity = (
                request["jwt_payload"]["sub"],
                data["stock_id"].upper(),
                int(data["quantity"]),
            )
            success, message, _ = await self.plugin.trading_manager.perform_sell(
                user_id, stock_id, quantity
            )
            status = 200 if success else 400
            return web.json_response(
                {"success": success, "message": message}, status=status
            )
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return web.json_response({"error": f"无效的请求体: {e}"}, status=400)

    async def _api_auth_register(self, request: web.Request):
        try:
            data = await request.json()
            login_id, password = data.get("user_id"), data.get("password")
            if not login_id or not password:
                return web.json_response({"error": "登录名和密码不能为空"}, status=400)

            existing_user = await self.plugin.db_manager.get_user_by_login_id(login_id)
            if existing_user:
                return web.json_response({"error": "该登录名已被使用"}, status=409)

            code = f"{random.randint(100000, 999999)}"
            while code in self.plugin.pending_verifications:
                code = f"{random.randint(100000, 999999)}"

            self.plugin.pending_verifications[code] = {
                "login_id": login_id,
                "password_hash": pwd_context.hash(password),
                "timestamp": datetime.now(),
            }
            return web.json_response({"success": True, "verification_code": code})
        except Exception as e:
            logger.error(f"发起注册时发生错误: {e}", exc_info=True)
            return web.json_response({"error": "服务器内部错误"}, status=500)

    async def _api_auth_login(self, request: web.Request):
        try:
            data = await request.json()
            login_id, password = data.get("user_id"), data.get("password")

            user_record = await self.plugin.db_manager.get_user_by_login_id(login_id)

            if not user_record or not pwd_context.verify(
                password, user_record["password_hash"]
            ):
                return web.json_response({"error": "登录名或密码错误"}, status=401)

            qq_user_id = user_record["user_id"]
            expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRATION_MINUTES)
            payload = {"sub": qq_user_id, "login_id": login_id, "exp": expire}
            token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

            return web.json_response(
                {
                    "access_token": token,
                    "token_type": "bearer",
                    "user_id": qq_user_id,
                    "login_id": login_id,
                }
            )
        except Exception as e:
            logger.error(f"登录时发生错误: {e}", exc_info=True)
            return web.json_response({"error": "服务器内部错误"}, status=500)

    @jwt_required
    async def _api_get_my_token(self, request: web.Request):
        """
        [API][Private] 获取当前认证用户正在使用的JWT。
        用户必须在请求的Authorization头中提供一个有效的Bearer Token。
        """
        try:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                # 理论上jwt_required已经检查过，这里是双重保险
                return web.json_response({"error": "无效的Authorization头"}, status=401)

            # 从 "Bearer <token>" 中提取 <token>
            token = auth_header.split(" ")[1]

            return web.json_response({"access_token": token, "token_type": "bearer"})
        except Exception as e:
            logger.error(
                f"为用户 {request.get('jwt_payload', {}).get('sub', '未知')} 获取Token时出错: {e}",
                exc_info=True,
            )
            return web.json_response({"error": "获取Token时发生内部错误"}, status=500)

    async def _api_auth_forgot_password(self, request: web.Request):
        """API: 发起忘记密码请求，返回验证码。"""
        try:
            data = await request.json()
            login_id = data.get("user_id")
            if not login_id:
                return web.json_response({"error": "用户ID不能为空"}, status=400)

            user_record = await self.plugin.db_manager.get_user_by_login_id(login_id)
            if not user_record:
                # 出于安全考虑，不明确提示用户是否存在
                return web.json_response(
                    {"error": "如果该用户存在，重置指令已发送"}, status=200
                )

            qq_user_id = user_record["user_id"]
            code = f"{random.randint(100000, 999999)}"
            while code in self.plugin.pending_password_resets:
                code = f"{random.randint(100000, 999999)}"

            self.plugin.pending_password_resets[code] = {
                "login_id": login_id,
                "qq_user_id": qq_user_id,
                "timestamp": datetime.now(),
                "verified": False,
            }
            logger.info(
                f"为登录ID '{login_id}' (QQ: {qq_user_id}) 生成了密码重置码: {code}"
            )
            # 返回给前端，用于后续验证
            return web.json_response({"success": True, "reset_code": code})
        except Exception as e:
            logger.error(f"发起忘记密码请求时出错: {e}", exc_info=True)
            return web.json_response({"error": "服务器内部错误"}, status=500)

    async def _api_auth_reset_password(self, request: web.Request):
        """API: 使用验证码和新密码完成密码重置。"""
        try:
            data = await request.json()
            login_id = data.get("user_id")
            code = data.get("reset_code")
            new_password = data.get("new_password")

            if not all([login_id, code, new_password]):
                return web.json_response({"error": "所有字段均为必填项"}, status=400)

            pending_request = self.plugin.pending_password_resets.get(code)

            if not pending_request or (
                datetime.now() - pending_request["timestamp"]
            ) > timedelta(minutes=5):
                return web.json_response({"error": "无效或已过期的重置码"}, status=400)

            if not pending_request.get("verified"):
                return web.json_response(
                    {"error": "该重置码尚未通过QQ验证"}, status=403
                )

            if pending_request.get("login_id") != login_id:
                return web.json_response({"error": "重置码与用户ID不匹配"}, status=403)

            new_password_hash = pwd_context.hash(new_password)
            await self.plugin.db_manager.update_user_password(
                login_id, new_password_hash
            )

            del self.plugin.pending_password_resets[code]
            logger.info(f"登录ID '{login_id}' 的密码已成功重置。")
            return web.json_response({"success": True, "message": "密码重置成功！"})
        except Exception as e:
            logger.error(f"重置密码时出错: {e}", exc_info=True)
            return web.json_response({"error": "服务器内部错误"}, status=500)
