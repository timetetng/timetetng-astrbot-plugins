# stock_market/main.py
import asyncio
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import matplotlib
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from matplotlib.font_manager import FontProperties
from playwright.async_api import Browser, async_playwright

from astrbot.api import logger
from astrbot.api import message_components as Comp

# --- AstrBot API 导入 ---
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from ..common.forwarder import Forwarder

try:
    from ..common.services import shared_services
except (ImportError, AttributeError):

    class MockSharedServices:
        def get(self, key):
            return None

        def register(self, key, value):
            pass

        def unregister(self, key):
            pass

    shared_services = MockSharedServices()
    logger.warning("未能从 common.services 导入共享API服务，插件功能将受限。")

# --- 内部模块导入 ---
from .api import StockMarketAPI
from .config import (
    DATA_DIR,
    IS_SERVER_DOMAIN,
    SERVER_BASE_URL,
    SERVER_DOMAIN,
    T_CLOSE,
    T_OPEN,
    TEMPLATES_DIR,
)
from .database import DatabaseManager
from .models import MarketSimulator, MarketStatus, VirtualStock
from .simulation import MarketSimulation
from .trading import TradingManager
from .treemap_generator import create_market_treemap
from .utils import (
    format_large_number,
    generate_user_hash,
    get_price_change_percentage_30m,
    get_stock_price_history_24h,
)
from .web_server import WebServer

jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True, enable_async=True
)


@register("stock_market", "timetetng", "一个功能重构的模拟炒股插件", "3.0.0")
class StockMarketRefactored(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # --- 状态管理 ---
        self.stocks: dict[str, VirtualStock] = {}
        self.market_status: MarketStatus = MarketStatus.CLOSED
        self.market_simulator = MarketSimulator()
        self.last_update_date: date | None = None
        self.broadcast_subscribers = set()
        self.pending_verifications: dict[str, dict[str, Any]] = {}

        # --- 外部服务API ---
        self.economy_api = None
        self.nickname_api = None
        self.bank_api = None
        self.forwarder = Forwarder()
        # --- 浏览器实例 ---
        self.playwright_browser: Browser | None = None

        # --- 模块化管理器 ---
        self.db_path = os.path.join(DATA_DIR, "stock_market.db")
        self.db_manager: DatabaseManager | None = None
        self.simulation_manager: MarketSimulation | None = None
        self.trading_manager: TradingManager | None = None
        self.web_server: WebServer | None = None
        self.pending_password_resets: dict[str, dict[str, Any]] = {}
        self.api = StockMarketAPI(self)
        self._ready_event = asyncio.Event()
        # --- 初始化任务 ---
        self.init_task = asyncio.create_task(self.plugin_init())

    async def terminate(self):
        logger.info("开始关闭模拟炒股插件...")
        shared_services.pop("stock_market_api", None)  # <--- 修改此行
        if self.init_task and not self.init_task.done():
            self.init_task.cancel()
        if self.simulation_manager:
            self.simulation_manager.stop()
        if self.web_server:
            await self.web_server.stop()
        await self._close_playwright_browser()
        logger.info("模拟炒股插件已成功关闭。")

    async def plugin_init(self):
        """插件的异步初始化流程（已添加错误捕获）。"""
        try:
            # 1. 等待依赖服务
            await self._wait_for_services()

            # 2. 初始化数据库
            self.db_manager = DatabaseManager(self.db_path)
            await self.db_manager.initialize()
            self.stocks = await self.db_manager.load_stocks()
            self.broadcast_subscribers = await self.db_manager.load_subscriptions()

            # 3. 启动 Playwright (如果失败不应阻断流程，内部已有 try-except)
            await self._start_playwright_browser()

            # 4. 初始化各个管理器
            self.simulation_manager = MarketSimulation(self)
            self.trading_manager = TradingManager(self)
            self.web_server = WebServer(self)

            # 5. 启动服务
            self.simulation_manager.start()

            # 【关键点】启动 Web 服务器 (容易因端口占用报错)
            try:
                await self.web_server.start()
            except Exception as e:
                logger.error(f"Web 服务器启动失败 (可能是端口被占用): {e}")
                # 即使 Web 服务失败，也让插件继续运行，不阻断其他功能

            # 6. 注册 API 并标记就绪
            shared_services["stock_market_api"] = self.api
            logger.info(f"模拟炒股插件初始化完成。数据库: {self.db_path}")

        except Exception as e:
            logger.error(f"模拟炒股插件初始化过程中发生严重错误: {e}", exc_info=True)
        finally:
            # 无论成功还是失败，都设置 ready 事件，防止命令死锁
            # (如果是失败导致，命令执行时可以再判断具体状态)
            self._ready_event.set()

    async def _start_playwright_browser(self):
        """启动并初始化 Playwright 浏览器实例"""
        try:
            p = await async_playwright().start()
            self.playwright_browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                ],  # 增加了一些常用的Linux服务器启动参数
            )
            logger.info("Playwright 浏览器实例已成功启动。")
        except Exception as e:
            logger.error(f"启动 Playwright 浏览器失败: {e}. K线图功能将不可用。")
            self.playwright_browser = None

    async def _close_playwright_browser(self):
        """安全地关闭 Playwright 浏览器实例"""
        if self.playwright_browser and self.playwright_browser.is_connected():
            await self.playwright_browser.close()
            logger.info("Playwright 浏览器实例已关闭。")

    async def _wait_for_services(self):
        """等待外部依赖的API服务加载。"""

        async def wait_for(service_name, timeout):
            start_time = asyncio.get_event_loop().time()
            while True:
                service = shared_services.get(service_name)
                if service:
                    logger.info(f"{service_name} 已成功加载。")
                    return service
                if asyncio.get_event_loop().time() - start_time > timeout:
                    logger.warning(f"等待 {service_name} 超时，相关功能将受限！")
                    return None
                await asyncio.sleep(1)

        self.economy_api = await wait_for("economy_api", 30)
        self.nickname_api = await wait_for("nickname_api", 10)
        self.bank_api = await wait_for("bank_api", 15)

    # --- 核心辅助方法 ---
    def get_market_status_and_wait(self) -> tuple[MarketStatus, int]:
        """获取当前市场状态及到下一状态的秒数。"""
        now = datetime.now()
        current_time = now.time()

        if T_OPEN <= current_time <= T_CLOSE:
            return MarketStatus.OPEN, 1
        else:
            next_open_dt = datetime.combine(now.date(), T_OPEN)
            if current_time > T_CLOSE:
                next_open_dt += timedelta(days=1)
            wait_seconds = int((next_open_dt - now).total_seconds())
            return MarketStatus.CLOSED, max(1, wait_seconds)

    async def find_stock(self, identifier: str) -> VirtualStock | None:
        """统一的股票查找器，支持编号、代码、名称。"""
        identifier = str(identifier)
        if identifier.isdigit():
            try:
                index = int(identifier) - 1
                sorted_stocks = sorted(self.stocks.values(), key=lambda s: s.stock_id)
                if 0 <= index < len(sorted_stocks):
                    return sorted_stocks[index]
            except (ValueError, IndexError):
                pass
        stock = self.stocks.get(identifier.upper())
        if stock:
            return stock
        for s in self.stocks.values():
            if s.name == identifier:
                return s
        return None

    async def get_display_name(self, user_id: str) -> str:
        """
        获取用户的最佳显示名称。
        优先级: nickname_api (自定义昵称) > economy_api (游戏内昵称) > user_id
        """
        # 1. 尝试从 nickname_api 获取最高优先级的自定义昵称
        if self.nickname_api:
            try:
                name = await self.nickname_api.get_nickname(user_id)
                if name:
                    return name
            except Exception as e:
                logger.warning(f"调用 nickname_api.get_nickname 时出错: {e}")

        # 2. 如果没有，尝试从 economy_api 获取游戏内昵称
        if self.economy_api:
            try:
                profile = await self.economy_api.get_user_profile(user_id)
                if profile and profile.get("nickname"):
                    return profile["nickname"]
            except Exception as e:
                logger.warning(f"调用 economy_api.get_user_profile 时出错: {e}")

        # 3. 如果都没有，直接返回 user_id 作为最后的保障
        return user_id

    async def get_stock_details_for_api(self, identifier: str) -> dict[str, Any] | None:
        """为 Web API 准备一支股票的详细数据。"""
        stock = await self.find_stock(identifier)
        if not stock:
            return None

        # --- 计算24小时数据 ---
        k_history_24h = list(stock.kline_history)[-288:]  # 最近24小时 (288 * 5分钟)

        day_open = k_history_24h[0]["open"] if k_history_24h else stock.previous_close
        day_close = stock.current_price

        change = day_close - day_open
        change_percent = (change / day_open) * 100 if day_open > 0 else 0

        # --- 获取趋势文本 (基于动能值转换) ---
        momentum = stock.intraday_momentum
        if momentum > 0.15:
            trend_text = "看涨"
        elif momentum < -0.15:
            trend_text = "看跌"
        else:
            trend_text = "盘整"

        # --- 获取股票编号 ---
        stock_index = -1
        try:
            sorted_stocks = sorted(self.stocks.values(), key=lambda s: s.stock_id)
            stock_index = sorted_stocks.index(stock) + 1
        except ValueError:
            pass  # 找不到就算了

        return {
            "index": stock_index,
            "stock_id": stock.stock_id,
            "name": stock.name,
            "current_price": round(stock.current_price, 2),
            "change": round(change, 2),
            "change_percent": round(change_percent, 2),
            "day_open": round(day_open, 2),
            "day_close": round(day_close, 2),
            "short_term_trend": trend_text,
            "kline_data_24h": k_history_24h,
        }

    async def _generate_kline_chart_image(
        self, kline_data: list, stock_name: str, stock_id: str, granularity: int
    ) -> str:
        """[最终整合版] 生成高度自定义样式且支持可变颗粒度的K线图。"""
        logger.info(f"开始为 {stock_name}({stock_id}) 生成 {granularity}分钟 K线图...")

        def plot_and_save_chart_in_thread():
            matplotlib.use("Agg")

            # --- 【字体加载与名称获取】 ---
            script_path = Path(__file__).resolve().parent
            # 假设字体文件在 'astrbot_stock_market/static/fonts/SimHei.ttf'
            font_path = script_path / "static" / "fonts" / "SimHei.ttf"
            if not os.path.exists(font_path):
                logger.error(f"致命错误：字体文件未找到于 '{font_path}'")
                raise FileNotFoundError(f"字体文件未找到于 '{font_path}'")

            from matplotlib import font_manager

            font_manager.fontManager.addfont(str(font_path))
            prop = font_manager.FontProperties(fname=font_path)
            font_name = prop.get_name()
            title_font = FontProperties(fname=font_path, size=32, weight="bold")

            # --- 【数据准备与聚合】 ---
            df = pd.DataFrame(kline_data)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.rename(
                columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                },
                inplace=True,
            )

            if granularity > 5:
                rule = f"{granularity}T"
                logger.info(f"开始将数据聚合为 {rule} 周期...")
                df = (
                    df.resample(rule)
                    .agg(
                        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
                    )
                    .dropna()
                )
                logger.info(f"数据聚合完成，剩余 {len(df)} 个数据点。")

            # --- 【样式与颜色设置 】 ---
            mc = mpf.make_marketcolors(up="#ff4747", down="#00b060", inherit=True)
            style = mpf.make_mpf_style(
                base_mpf_style="binance",
                marketcolors=mc,
                gridstyle="--",
                rc={
                    "font.family": font_name,
                    "xtick.labelsize": 18,
                    "ytick.labelsize": 24,
                    "axes.labelsize": 26,
                    "axes.labelweight": "bold",
                },
            )

            title = f"{stock_name} ({stock_id}) - 最近24小时 ({granularity}分钟K)"
            save_path = os.path.join(
                DATA_DIR, f"kline_{stock_id}_{random.randint(1000, 9999)}.png"
            )

            # --- 【绘图与调整 】 ---
            fig, axes = mpf.plot(
                df,
                type="candle",
                style=style,
                ylabel="Price ($)",
                figsize=(20, 12),
                datetime_format="%m/%d %H:%M",
                mav=(5, 10, 30),
                returnfig=True,
            )

            axes[0].set_title(title, fontproperties=title_font)
            fig.subplots_adjust(
                left=0.05, right=0.98, bottom=0.1, top=0.92
            )  # 使用了您更优的边距参数

            fig.savefig(save_path, dpi=150)
            plt.close(fig)  # 关键：关闭图形，防止内存泄漏
            # --- 【绘图结束】 ---

            logger.info(f"K线图已成功保存至: {save_path}")
            return save_path

        try:
            path = await asyncio.to_thread(plot_and_save_chart_in_thread)
            return path
        except Exception as e:
            logger.error(
                f"在 _generate_kline_chart_image 函数内部发生严重错误: {e}",
                exc_info=True,
            )
            raise

    async def get_user_total_asset(self, user_id: str) -> dict[str, Any]:
        """
        计算单个用户的总资产详情 (V3 - 完全使用db_manager)
        """
        stock_market_value = 0.0
        total_cost_basis = 0
        holdings_detailed = []
        holdings_count = 0

        # 1. 计算股票市值 (已修正：使用 db_manager)
        try:
            # 从数据库管理器获取聚合后的持仓数据
            aggregated_holdings = await self.db_manager.get_user_holdings_aggregated(
                user_id
            )
            holdings_count = len(aggregated_holdings)

            for stock_id, data in aggregated_holdings.items():
                stock = self.stocks.get(stock_id)
                if stock:
                    quantity = data["quantity"]
                    cost_basis = data["cost_basis"]
                    market_value = stock.current_price * quantity

                    stock_market_value += market_value
                    total_cost_basis += cost_basis

                    pnl = market_value - cost_basis
                    pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

                    holdings_detailed.append(
                        {
                            "stock_id": stock_id,
                            "name": stock.name,
                            "quantity": quantity,
                            "avg_cost": round(
                                cost_basis / quantity if quantity > 0 else 0, 2
                            ),
                            "market_value": round(market_value, 2),
                            "pnl": round(pnl, 2),
                            "pnl_percent": round(pnl_percent, 2),
                        }
                    )
                else:
                    logger.warning(
                        f"  -> 警告: 在数据库中找到持仓 {stock_id}，但在内存(self.stocks)中找不到该股票对象！"
                    )
        except Exception as e:
            logger.error(f"查询或计算持仓市值时发生错误: {e}", exc_info=True)

        # 2. 获取现金余额
        coins = 0
        if self.economy_api:
            try:
                coins = await self.economy_api.get_coins(user_id)
            except Exception as e:
                logger.error(f"调用 economy_api.get_coins 时出错: {e}", exc_info=True)
        else:
            logger.warning("economy_api 未加载，金币强制计为 0。")

        # 3. 获取公司资产
        company_assets = 0
        industry_api = shared_services.get("industry_api")
        if industry_api:
            try:
                is_public_company_owner = False
                public_company_market_cap = 0
                for stock in self.stocks.values():
                    if stock.is_listed_company and stock.owner_id == user_id:
                        is_public_company_owner = True
                        public_company_market_cap = (
                            stock.current_price * stock.total_shares
                        )
                        break

                if is_public_company_owner:
                    company_assets = public_company_market_cap
                else:
                    company_assets = await industry_api.get_company_asset_value(user_id)
            except Exception as e:
                logger.error(f"调用 industry_api 时出错: {e}", exc_info=True)

        # 4. 获取银行资产和负债
        bank_deposits = 0.0
        bank_loans = 0.0
        if self.bank_api:
            try:
                bank_deposits = await self.bank_api.get_bank_asset_value(user_id)
                loan_info = await self.bank_api.get_loan_info(user_id)
                if loan_info:
                    bank_loans = loan_info.get("amount_due", 0)
            except Exception as e:
                logger.error(f"调用 bank_api 时出错: {e}", exc_info=True)

        # 5. 计算最终总资产
        final_total_assets = round(
            coins + stock_market_value + company_assets + bank_deposits - bank_loans, 2
        )
        total_pnl = stock_market_value - total_cost_basis if total_cost_basis > 0 else 0
        total_pnl_percent = (
            (total_pnl / total_cost_basis) * 100 if total_cost_basis > 0 else 0
        )

        # 6. 返回包含所有资产成分的字典
        return {
            "user_id": user_id,
            "total_assets": final_total_assets,
            "coins": coins,
            "stock_value": round(stock_market_value, 2),
            "company_assets": company_assets,
            "bank_deposits": bank_deposits,
            "bank_loans": bank_loans,
            "holdings_count": holdings_count,
            "holdings_detailed": holdings_detailed,
            "total_pnl": total_pnl,
            "total_pnl_percent": total_pnl_percent,
        }

    async def get_total_asset_ranking(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        计算并获取总资产排行榜 (V3 - 完全使用db_manager)
        """
        if not self.economy_api:
            logger.error("无法计算总资产排行，因为经济系统API不可用。")
            return []

        candidate_user_ids = set()

        # 1. 获取所有持有股票的用户 (已修正：使用 db_manager)
        try:
            candidate_user_ids = await self.db_manager.get_all_user_ids_with_holdings()
        except Exception as e:
            logger.error(f"从 db_manager 获取持股用户时出错: {e}", exc_info=True)

        # 2. 获取现金排名前列的用户
        try:
            top_coin_users = await self.economy_api.get_ranking(limit=50)
            for user in top_coin_users:
                candidate_user_ids.add(user["user_id"])
        except Exception as e:
            logger.error(f"调用 economy_api.get_ranking 时出错: {e}", exc_info=True)

        # 3. 获取银行存款排名前列的用户
        if self.bank_api:
            try:
                top_bank_users = await self.bank_api.get_top_accounts(limit=50)
                for user in top_bank_users:
                    candidate_user_ids.add(user["user_id"])
            except Exception as e:
                logger.error(
                    f"调用 bank_api.get_top_accounts 时出错: {e}", exc_info=True
                )
        else:
            logger.warning("bank_api 未加载，总资产排行将不包含银行存款排行数据。")

        # 4. 获取公司资产价值排名前列的用户
        industry_api = shared_services.get("industry_api")
        if industry_api:
            try:
                top_companies = await industry_api.get_top_companies_by_value(limit=50)
                for company in top_companies:
                    candidate_user_ids.add(company["user_id"])
            except Exception as e:
                logger.error(
                    f"调用 industry_api.get_top_companies_by_value 时出错: {e}",
                    exc_info=True,
                )
        else:
            logger.warning("industry_api 未加载，总资产排行将不包含公司资产排行数据。")

        candidate_user_ids.discard("1902929802")
        # 为候选池中的每一位用户计算总资产
        asset_tasks = [self.get_user_total_asset(uid) for uid in candidate_user_ids]
        all_asset_data = await asyncio.gather(*asset_tasks)

        # 过滤掉总资产为0或负数的用户
        valid_asset_data = [
            data for data in all_asset_data if data and data.get("total_assets", 0) > 0
        ]

        # 按总资产排序并返回结果
        sorted_assets = sorted(
            valid_asset_data, key=lambda x: x["total_assets"], reverse=True
        )
        return sorted_assets[:limit]

    # ----------------------------
    # 用户指令 (User Commands)
    # ----------------------------
    @filter.command("股票列表", alias={"所有股票", "查询股票", "查看股票", "股票"})
    async def list_stocks(self, event: AstrMessageEvent):
        """查看当前市场所有可交易的股票"""
        if not self.stocks:
            yield event.plain_result("当前市场没有可交易的股票。")
            return

        reply = "--- 虚拟股票市场列表 ---\n"
        sorted_stocks = sorted(self.stocks.values(), key=lambda s: s.stock_id)

        for i, stock in enumerate(sorted_stocks, 1):
            price_change = 0.0
            price_change_percent = 0.0

            # 确保有足够的历史数据来计算涨跌幅
            if len(stock.price_history) > 1:
                # price_history[-1] 是当前价格的记录, price_history[-2] 是上一个周期的价格
                last_price = stock.price_history[-2]
                price_change = stock.current_price - last_price

                # 防止除以零的错误
                if last_price > 0:
                    price_change_percent = (price_change / last_price) * 100

            emoji = "📈" if price_change > 0 else "📉" if price_change < 0 else "➖"

            # 在价格后面添加格式化的涨跌幅百分比
            # :+.2f 会强制显示正负号，并保留两位小数
            reply += f"[{i}]{stock.stock_id.ljust(5)}{stock.name.ljust(6)}{emoji}${stock.current_price:<8.2f}({price_change_percent:+.2f}%)\n"

        reply += "----------------------\n"
        reply += "使用 /大盘云图 查看市场概况\n"
        reply += "使用 /行情 <编号/代码/名称> 查看详细信息"
        yield event.plain_result(reply)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("股东列表", alias={"持股查询"})
    async def stock_holders(self, event: AstrMessageEvent, stock_identifier: str):
        """
        查询指定股票的持股用户列表及其详细盈亏信息。
        用法: /股东列表 [股票代码/名称]
        """
        # 1. 验证输入并查找股票
        if not stock_identifier:
            # 直接返回纯文本错误信息
            yield event.plain_result(
                "❌ 请输入要查询的股票代码或名称。\n用法: `/股东列表 [股票代码/名称]`"
            )
            return

        stock = await self.find_stock(stock_identifier)
        if not stock:
            yield event.plain_result(
                f"❌ 找不到股票 `'{stock_identifier}'`。请检查代码或名称是否正确。"
            )
            return

        # 2. 从数据库查询该股票的所有持仓记录
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, quantity, purchase_price FROM holdings WHERE stock_id=?",
                (stock.stock_id,),
            )
            raw_holdings = await cursor.fetchall()

        if not raw_holdings:
            yield event.plain_result(f"ℹ️ 当前无人持有 **【{stock.name}】**。")
            return

        # 3. 按 user_id 聚合数据
        holders_data = {}
        for user_id, qty, price in raw_holdings:
            if user_id not in holders_data:
                holders_data[user_id] = {"quantity": 0, "cost_basis": 0.0}
            holders_data[user_id]["quantity"] += qty
            holders_data[user_id]["cost_basis"] += qty * price

        # 4. 【核心修正V2：确保自定义昵称的最高优先级】
        user_ids = list(holders_data.keys())
        final_names = {}

        if self.economy_api:
            profile_tasks = [self.economy_api.get_user_profile(uid) for uid in user_ids]
            profiles = await asyncio.gather(*profile_tasks)
            for profile in profiles:
                if profile and profile.get("nickname"):
                    final_names[profile["user_id"]] = profile["nickname"]

        if self.nickname_api:
            custom_nicknames = await self.nickname_api.get_nicknames_batch(user_ids)
            final_names.update(custom_nicknames)

        # 5. 计算每个用户的盈亏详情
        holder_details_list = []
        for user_id, data in holders_data.items():
            display_name = final_names.get(user_id) or f"用户({user_id[:6]}...)"

            quantity = data["quantity"]
            cost_basis = data["cost_basis"]
            market_value = quantity * stock.current_price
            pnl = market_value - cost_basis
            pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

            holder_details_list.append(
                {
                    "name": display_name,
                    "quantity": quantity,
                    "market_value": market_value,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                }
            )

        # 6. 按持股数量从高到低排序
        sorted_holders = sorted(
            holder_details_list, key=lambda x: x["quantity"], reverse=True
        )

        # 7. 构建包含 Markdown 表格语法的字符串
        response_lines = [
            f"### 📊 【**{stock.name}** ({stock.stock_id})】股东盈亏榜",
            f"**当前价格:** `${stock.current_price:.2f}`",
            "| 排名 | 股东 | 持仓(股) | 市值 | 盈亏 | 盈亏比例 |",
            "| :--: | :--- | :---: | :---: | :---: | :---: |",
        ]

        rank = 1
        for holder in sorted_holders:
            pnl_emoji = (
                "📈" if holder["pnl"] > 0 else "📉" if holder["pnl"] < 0 else "➖"
            )
            pnl_str = f"{holder['pnl']:+.2f}"
            pnl_percent_str = f"{holder['pnl_percent']:+.2f}%"

            line = f"| {rank} | **{holder['name']}** | {holder['quantity']} | `${holder['market_value']:.2f}` | {pnl_emoji} **{pnl_str}** | ({pnl_percent_str}) |"
            response_lines.append(line)
            rank += 1

        markdown_text = "\n".join(response_lines)

        # 8. 【核心修改】将 Markdown 文本转换为图片并发送
        url = await self.text_to_image(markdown_text)
        yield event.image_result(url)

    @filter.command("行情", alias={"查看行情"})
    async def get_stock_price(self, event: AstrMessageEvent, identifier: str):
        """查询指定股票的实时行情"""
        if identifier is None:
            yield event.plain_result(
                "🤔 请输入需要查询的股票。\n正确格式: /行情 <编号/代码/名称>"
            )
            return
        stock = await self.find_stock(str(identifier))
        if not stock:
            yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
            return

        k_history = stock.kline_history
        if len(k_history) < 2:
            yield event.plain_result(
                f"【{stock.name} ({stock.stock_id})】\n价格: ${stock.current_price:.2f}\n行情数据不足..."
            )
            return

        # --- 基础价格计算 ---
        last_price = k_history[-2]["close"]
        change = stock.current_price - last_price
        change_percent = (change / last_price) * 100 if last_price > 0 else 0
        emoji = "📈" if change > 0 else "📉" if change < 0 else "➖"

        # --- 增强信息计算 ---
        relevant_history = list(k_history)[-288:]
        day_high = (
            max(k["high"] for k in relevant_history)
            if relevant_history
            else stock.current_price
        )
        day_low = (
            min(k["low"] for k in relevant_history)
            if relevant_history
            else stock.current_price
        )
        day_open = (
            relevant_history[0]["open"] if relevant_history else stock.previous_close
        )

        sma5_text = "数据不足"
        if len(k_history) >= 5:
            recent_closes = [k["close"] for k in list(k_history)[-5:]]
            sma5 = sum(recent_closes) / 5
            sma5_text = f"${sma5:.2f}"

        # --- 获取内部趋势状态 (基于动能值转换) ---
        momentum = stock.intraday_momentum
        if momentum > 0.15:
            current_trend_text = "看涨"
        elif momentum < -0.15:
            current_trend_text = "看跌"
        else:
            current_trend_text = "盘整"

        reply = (
            f"{emoji}【{stock.name} ({stock.stock_id})】行情\n"
            f"--------------------\n"
            f"现价: ${stock.current_price:.2f}\n"
            f"涨跌: ${change:+.2f} ({change_percent:+.2f}%) (较5min前)\n"
            f"--------------------\n"
            f"24h开盘: ${day_open:.2f}\n"
            f"24h最高: ${day_high:.2f}\n"
            f"24h最低: ${day_low:.2f}\n"
            f"5周期均线: {sma5_text}\n"
            f"--------------------\n"
            f"短期趋势: {current_trend_text}\n"
            f"所属行业: {stock.industry}"
        )
        yield event.plain_result(reply)

    @filter.command("K线", alias={"k线图", "k线", "K线图"})
    async def show_kline(
        self,
        event: AstrMessageEvent,
        identifier: str,
        granularity_str: str | None = "5",
    ):
        """显示指定股票的K线图 (可指定颗粒度)"""
        await self._ready_event.wait()

        if identifier is None:
            yield event.plain_result(
                "🤔 请输入需要查询的股票。\n正确格式: /k线 <标识符> [颗粒度(分钟)]"
            )
            return

        # ▼▼▼【核心修改】处理和验证颗粒度参数 ▼▼▼
        try:
            granularity = int(granularity_str)
            if granularity < 5 or granularity % 5 != 0:
                yield event.plain_result(
                    "❌ 颗粒度必须是大于等于5, 且为5的倍数的整数 (如 5, 10, 15, 30, 60)。"
                )
                return
        except ValueError:
            yield event.plain_result("❌ 颗粒度必须是一个有效的数字。")
            return
        # ▲▲▲【修改结束】▲▲▲

        stock = await self.find_stock(str(identifier))
        if not stock:
            yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
            return

        if len(stock.kline_history) < 2:
            yield event.plain_result(f"📈 {stock.name} 的K线数据不足，无法生成图表。")
            return

        yield event.plain_result(
            f"正在为 {stock.name} 生成最近24小时的 {granularity}分钟 K线图，请稍候..."
        )

        screenshot_path = ""
        try:
            # 依然获取288个5分钟数据点作为基础数据源
            kline_data_for_image = list(stock.kline_history)[-288:]

            # 调用新的绘图函数，并传入颗粒度
            screenshot_path = await self._generate_kline_chart_image(
                kline_data=kline_data_for_image,
                stock_name=stock.name,
                stock_id=stock.stock_id,
                granularity=granularity,  # <--- 传入新参数
            )

            yield event.image_result(screenshot_path)

        except Exception as e:
            logger.error(
                f"使用mplfinance生成K线图过程中发生未知错误: {e}", exc_info=True
            )
            yield event.plain_result("❌ 生成K线图失败，请稍后重试。")
        finally:
            if screenshot_path and os.path.exists(screenshot_path):
                os.remove(screenshot_path)

    @filter.command("大盘云图", alias={"云图", "大盘"})
    async def market_treemap(self, event: AstrMessageEvent):
        """生成并显示当前市场的30分钟大盘云图"""
        await self._ready_event.wait()

        image_path = ""
        try:
            yield event.plain_result("正在生成基于30分钟行情的大盘云图，请稍候...")

            image_path = await create_market_treemap(
                db_path=self.db_path, output_dir=os.path.join(DATA_DIR)
            )

            if image_path:
                yield event.image_result(image_path)
            else:
                yield event.plain_result(
                    "抱歉，生成大盘云图失败，可能是数据不足或系统错误。"
                )

        except Exception as e:
            logger.error(f"处理 /大盘云图 命令时发生错误: {e}", exc_info=True)
            yield event.plain_result("处理您的请求时遇到内部错误，请稍后重试。")
        finally:
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except Exception as e:
                    logger.error(f"删除大盘云图临时文件 {image_path} 失败: {e}")

    @filter.command("购买股票", alias={"买入", "加仓"})
    async def buy_stock(
        self,
        event: AstrMessageEvent,
        identifier: str,
        quantity_str: str | None = None,
    ):
        """购买指定数量的股票 (T+60min)"""
        await self._ready_event.wait()  # 等待初始化
        if identifier is None or quantity_str is None:
            yield event.plain_result(
                "🤔 指令格式错误。\n正确格式: /买入 <标识符> <数量>"
            )
            return
        try:
            quantity = int(quantity_str)
            if quantity <= 0:
                yield event.plain_result("❌ 购买数量必须是一个正整数。")
                return
        except ValueError:
            yield event.plain_result("❌ 购买数量必须是一个有效的数字。")
            return

        user_id = event.get_sender_id()
        # 【修正】调用 trading_manager
        success, message = await self.trading_manager.perform_buy(
            user_id, identifier, quantity
        )
        yield event.plain_result(message)

    # 替换 main.py 中的 sell_stock 函数
    @filter.command("出售", alias={"卖出", "减仓", "抛出"})
    async def sell_stock(
        self,
        event: AstrMessageEvent,
        identifier: str,
        quantity_str: str | None = None,
    ):
        """出售指定数量的股票 (T+60min & Fee)"""
        await self._ready_event.wait()  # 等待初始化
        if identifier is None or quantity_str is None:
            yield event.plain_result(
                "🤔 指令格式错误。\n正确格式: /卖出 <标识符> <数量>"
            )
            return
        try:
            quantity_to_sell = int(quantity_str)
            if quantity_to_sell <= 0:
                yield event.plain_result("❌ 出售数量必须是一个正整数。")
                return
        except ValueError:
            yield event.plain_result("❌ 出售数量必须是一个有效的数字。")
            return

        user_id = event.get_sender_id()
        # 【修正】调用 trading_manager
        success, message, _ = await self.trading_manager.perform_sell(
            user_id, identifier, quantity_to_sell
        )
        yield event.plain_result(message)

    @filter.command("梭哈股票")
    async def buy_all_in(self, event: AstrMessageEvent, identifier: str):
        """快捷指令：用全部现金买入单支股票"""
        await self._ready_event.wait()
        user_id = event.get_sender_id()
        success, message = await self.trading_manager.perform_buy_all_in(
            user_id, identifier
        )
        yield event.plain_result(message)

    @filter.command("全抛", alias={"全部抛出"})
    async def sell_all_stock(self, event: AstrMessageEvent, identifier: str):
        """快捷指令：卖出单支股票的所有可卖持仓"""
        await self._ready_event.wait()
        if identifier is None:
            yield event.plain_result(
                "🤔 请输入需要抛售的股票。\n正确格式: /全抛 <编号/代码/名称>"
            )
            return
        user_id = event.get_sender_id()
        success, message = await self.trading_manager.perform_sell_all_for_stock(
            user_id, identifier
        )
        yield event.plain_result(message)

    @filter.command("清仓", alias={"全部卖出"})
    async def sell_all_portfolio(self, event: AstrMessageEvent):
        """快捷指令：卖出所有持仓中可卖的股票"""
        await self._ready_event.wait()
        user_id = event.get_sender_id()
        success, message = await self.trading_manager.perform_sell_all_portfolio(
            user_id
        )
        yield event.plain_result(message)

    @filter.command("持仓", alias={"文字持仓"})
    async def portfolio_text(self, event: AstrMessageEvent):
        """查看我的个人持仓详情（纯文字版）"""
        user_id = event.get_sender_id()
        name = event.get_sender_name()

        aggregated_holdings = await self.db_manager.get_user_holdings_aggregated(
            user_id
        )

        if not aggregated_holdings:
            yield event.plain_result(
                f"{name}，你当前没有持仓。使用 '/股票列表' 查看市场。"
            )
            return
        # 3. 基于聚合后的数据计算各项指标
        holdings_list_for_template = []
        total_market_value = 0
        total_cost_basis = 0

        for stock_id, data in aggregated_holdings.items():
            stock = self.stocks.get(stock_id)
            if not stock:
                continue

            qty = data["quantity"]
            cost_basis = data["cost_basis"]

            price_change = (
                stock.current_price - stock.price_history[-2]
                if len(stock.price_history) > 1
                else 0
            )
            emoji = "📈" if price_change > 0 else "📉" if price_change < 0 else "➖"

            market_value = qty * stock.current_price
            pnl = market_value - cost_basis
            pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

            holdings_list_for_template.append(
                {
                    "name": stock.name,
                    "quantity": qty,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                    "emoji": emoji,
                }
            )

            total_market_value += market_value
            total_cost_basis += cost_basis

        total_pnl = total_market_value - total_cost_basis
        total_pnl_percent = (
            (total_pnl / total_cost_basis) * 100 if total_cost_basis > 0 else 0
        )

        # 4. 格式化并返回文字信息
        response_lines = [f"📊 {name} 的持仓：\n----------------\n"]
        for holding in holdings_list_for_template:
            pnl_str = f"{holding['pnl']:+.2f}"
            pnl_percent_str = f"({holding['pnl_percent']:+.2f}%)"
            response_lines.append(
                f"{holding['emoji']} {holding['name']}: {holding['quantity']} 股, 盈亏: {pnl_str} {pnl_percent_str}"
            )

        total_pnl_str = f"{total_pnl:+.2f}"
        total_pnl_percent_str = f"({total_pnl_percent:+.2f}%)"

        response_lines.append(f"\n----------------\n总市值: {total_market_value:.2f}")
        response_lines.append(f"总成本: {total_cost_basis:.2f}")
        response_lines.append(f"总盈亏: {total_pnl_str} {total_pnl_percent_str}")

        yield event.plain_result("\n".join(response_lines))

    @filter.command("持仓图", alias={"我的持仓", "持仓图片"})
    async def my_portfolio(self, event: AstrMessageEvent):
        """查看我的个人持仓详情（以图片卡片形式，失败时自动切换为文字版）"""
        user_id = event.get_sender_id()
        name = event.get_sender_name()

        aggregated_holdings = await self.db_manager.get_user_holdings_aggregated(
            user_id
        )

        if not aggregated_holdings:
            yield event.plain_result(
                f"{name}，你当前没有持仓。使用 '/股票列表' 查看市场。"
            )
            return

        # 3. 基于聚合后的数据准备模板所需数据
        holdings_list_for_template = []
        total_market_value = 0
        total_cost_basis = 0

        for stock_id, data in aggregated_holdings.items():
            stock = self.stocks.get(stock_id)
            if not stock:
                continue

            qty = data["quantity"]
            cost_basis = data["cost_basis"]
            avg_cost = cost_basis / qty if qty > 0 else 0

            price_change = (
                stock.current_price - stock.price_history[-2]
                if len(stock.price_history) > 1
                else 0
            )
            emoji = "📈" if price_change > 0 else "📉" if price_change < 0 else "➖"

            market_value = qty * stock.current_price
            pnl = market_value - cost_basis
            pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

            holdings_list_for_template.append(
                {
                    "name": stock.name,
                    "stock_id": stock.stock_id,
                    "quantity": qty,
                    "avg_cost": avg_cost,
                    "market_value": market_value,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                    "is_positive": pnl >= 0,
                    "emoji": emoji,
                }
            )

            total_market_value += market_value
            total_cost_basis += cost_basis

        total_pnl = total_market_value - total_cost_basis
        total_pnl_percent = (
            (total_pnl / total_cost_basis) * 100 if total_cost_basis > 0 else 0
        )

        # 4. 尝试生成图片卡片
        if self.playwright_browser:
            try:
                template_data = {
                    "user_name": name,
                    "holdings": holdings_list_for_template,
                    "total": {
                        "market_value": total_market_value,
                        "pnl": total_pnl,
                        "pnl_percent": total_pnl_percent,
                        "is_positive": total_pnl >= 0,
                    },
                }
                template = jinja_env.get_template("portfolio_card.html")
                html_content = await template.render_async(template_data)

                temp_html_path = os.path.join(
                    DATA_DIR,
                    f"temp_portfolio_{user_id}_{random.randint(1000, 9999)}.html",
                )
                screenshot_path = os.path.join(
                    DATA_DIR, f"portfolio_{user_id}_{random.randint(1000, 9999)}.png"
                )

                with open(temp_html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)

                page = await self.playwright_browser.new_page()
                await page.goto(f"file://{os.path.abspath(temp_html_path)}")
                await page.locator(".card").screenshot(path=screenshot_path)
                await page.close()

                yield event.image_result(screenshot_path)
                return
            except Exception as e:
                logger.error(f"生成持仓卡片失败: {e}")
            finally:
                if "temp_html_path" in locals() and os.path.exists(temp_html_path):
                    os.remove(temp_html_path)
                if "screenshot_path" in locals() and os.path.exists(screenshot_path):
                    os.remove(screenshot_path)

        # 如果图片卡片生成失败或浏览器不可用，则返回文字版持仓信息
        response_lines = [f"📊 {name} 的持仓：\n----------------\n"]
        for holding in holdings_list_for_template:
            pnl_str = f"{holding['pnl']:+.2f}"
            pnl_percent_str = f"({holding['pnl_percent']:+.2f}%)"
            response_lines.append(
                f"{holding['emoji']} {holding['name']}: {holding['quantity']} 股, 盈亏: {pnl_str} {pnl_percent_str}"
            )

        total_pnl_str = f"{total_pnl:+.2f}"
        total_pnl_percent_str = f"({total_pnl_percent:+.2f}%)"

        response_lines.append(f"\n----------------\n总市值: {total_market_value:.2f}")
        response_lines.append(f"总成本: {total_cost_basis:.2f}")
        response_lines.append(f"总盈亏: {total_pnl_str} {total_pnl_percent_str}")

        yield event.plain_result("\n".join(response_lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("添加股票")
    async def admin_add_stock(
        self,
        event: AstrMessageEvent,
        stock_id: str,
        name: str,
        initial_price: float,
        volatility: float = 0.05,
        industry: str = "综合",
    ):
        """[管理员] 添加一支新的虚拟股票"""
        await self._ready_event.wait()
        stock_id = stock_id.upper()
        if stock_id in self.stocks:
            yield event.plain_result(f"❌ 添加失败：股票代码 {stock_id} 已存在。")
            return

        # 【修正】调用 db_manager
        await self.db_manager.add_stock(
            stock_id, name, initial_price, volatility, industry
        )

        # 更新內存
        stock = VirtualStock(
            stock_id=stock_id,
            name=name,
            current_price=initial_price,
            volatility=volatility,
            industry=industry,
        )
        stock.price_history.append(initial_price)
        self.stocks[stock_id] = stock

        yield event.plain_result(f"✅ 成功添加股票: {name} ({stock_id})")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除股票")
    async def admin_del_stock(self, event: AstrMessageEvent, identifier: str):
        """[管理员] 删除一支股票及其所有相关数据"""
        await self._ready_event.wait()
        stock = await self.find_stock(identifier)
        if not stock:
            yield event.plain_result(
                f"❌ 删除失败：找不到标识符为 '{identifier}' 的股票。"
            )
            return

        stock_id = stock.stock_id
        stock_name = stock.name

        # 【修正】调用 db_manager
        await self.db_manager.delete_stock(stock_id)

        # 更新內存
        del self.stocks[stock_id]
        yield event.plain_result(
            f"🗑️ 已成功删除股票 {stock_name} ({stock_id}) 及其所有持仓和历史数据。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("修改股票")
    async def admin_modify_stock(
        self, event: AstrMessageEvent, identifier: str, param: str, value: str
    ):
        """[管理员] 修改现有股票的参数。用法: /修改股票 <标识符> <参数> <新值>"""
        await self._ready_event.wait()
        stock = await self.find_stock(identifier)
        if not stock:
            yield event.plain_result(
                f"❌ 操作失败：找不到标识符为 '{identifier}' 的股票。"
            )
            return

        param = param.lower()
        old_stock_id = stock.stock_id

        if param in ("name", "名称"):
            # 【修正】调用 db_manager
            await self.db_manager.update_stock_name(old_stock_id, value)
            stock.name = value
            yield event.plain_result(
                f"✅ 成功将股票 {old_stock_id} 的名称修改为: {value}"
            )

        elif param in ("stock_id", "股票代码", "代码"):
            new_stock_id = value.upper()
            if new_stock_id in self.stocks:
                yield event.plain_result(
                    f"❌ 操作失败：新的股票代码 {new_stock_id} 已存在！"
                )
                return
            try:
                # 【修正】调用 db_manager
                await self.db_manager.update_stock_id(old_stock_id, new_stock_id)
                stock.stock_id = new_stock_id
                self.stocks[new_stock_id] = self.stocks.pop(old_stock_id)
                yield event.plain_result(
                    f"✅ 成功将股票代码 {old_stock_id} 修改为: {new_stock_id}，所有关联数据已同步更新。"
                )
            except Exception as e:
                logger.error(f"修改股票代码时发生数据库错误: {e}", exc_info=True)
                yield event.plain_result(
                    "❌ 修改股票代码时发生数据库错误，操作已取消。"
                )

        elif param in ("industry", "行业"):
            # 【修正】调用 db_manager
            await self.db_manager.update_stock_industry(old_stock_id, value)
            stock.industry = value
            yield event.plain_result(
                f"✅ 成功将股票 {old_stock_id} 的行业修改为: {value}"
            )

        elif param in ("volatility", "波动率"):
            try:
                new_vol = float(value)
                # 【修正】调用 db_manager
                await self.db_manager.update_stock_volatility(old_stock_id, new_vol)
                stock.volatility = new_vol
                yield event.plain_result(
                    f"✅ 成功将股票 {old_stock_id} 的波动率修改为: {new_vol:.4f}"
                )
            except ValueError:
                yield event.plain_result("❌ 波动率必须是有效的数字。")

        else:
            yield event.plain_result(
                f"❌ 未知的参数: '{param}'。\n可用参数: `name`, `stock_id`, `industry`, `volatility`"
            )

    # 替换 main.py 中的 admin_set_price 函数

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("设置股价", alias={"修改股价"})
    async def admin_set_price(
        self, event: AstrMessageEvent, identifier: str, new_price: float
    ):
        """[管理员] 强制修改指定股票的当前价格"""
        await self._ready_event.wait()

        if new_price <= 0:
            yield event.plain_result("❌ 价格必须是一个正数。")
            return

        stock = await self.find_stock(identifier)
        if not stock:
            yield event.plain_result(
                f"❌ 操作失败：找不到标识符为 '{identifier}' 的股票。"
            )
            return

        old_price = stock.current_price
        stock_id = stock.stock_id

        # 1. 更新内存中的价格
        stock.current_price = new_price
        stock.price_history.append(new_price)

        # 2. 【修正】调用 db_manager 更新数据库
        await self.db_manager.update_stock_price(stock_id, new_price)

        # 3. 发送成功确认信息
        yield event.plain_result(
            f"✅ 操作成功！\n"
            f"已将股票 {stock.name} ({stock_id}) 的价格\n"
            f"从 ${old_price:.2f} 强制修改为 ${new_price:.2f}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("股票详情", alias={"查询股票参数"})
    async def admin_stock_details(self, event: AstrMessageEvent, identifier: str):
        """[管理员] 查看股票的所有内部详细参数"""
        stock = await self.find_stock(identifier)
        if not stock:
            yield event.plain_result(
                f"❌ 操作失败：找不到标识符为 '{identifier}' 的股票。"
            )
            return
        details = (
            f"--- 股票内部参数详情 ---\n"
            f"股票名称: {stock.name}\n"
            f"股票代码: {stock.stock_id}\n"
            f"所属行业: {stock.industry}\n"
            f"--------------------\n"
            f"当前价格: ${stock.current_price:.2f}\n"
            f"波动率 (volatility): {stock.volatility:.4f}\n"
            f"基本价值 (FV): ${stock.fundamental_value:.2f}\n"
            f"--------------------\n"
            f"【动能波系统】\n"
            f"当前动能值: {stock.intraday_momentum:.4f}\n"
            f"动能波峰值: {stock.momentum_target_peak:.4f}\n"
            f"动能波进程: {stock.momentum_current_tick} / {stock.momentum_duration_ticks} (Ticks)\n"
            f"--------------------\n"
            f"内存记录:\n"
            f" - 价格历史点: {len(stock.price_history)} / {stock.price_history.maxlen}\n"
            f" - K线历史点: {len(stock.kline_history)} / {stock.kline_history.maxlen}"
        )

        yield event.plain_result(details)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("列出所有股票", alias={"所有股票"})
    async def admin_list_db_stocks(self, event: AstrMessageEvent):
        """[管理员] 从数据库中查询并列出所有股票的详细信息。"""
        await self._ready_event.wait()

        try:
            # 【修正】调用 db_manager
            stock_data = await self.db_manager.get_all_stocks_with_details()
        except Exception as e:
            logger.error(f"查询数据库股票列表时出错: {e}", exc_info=True)
            yield event.plain_result("❌ 查询数据库时出错，请检查日志。")
            return

        if not stock_data:
            yield event.plain_result("数据库中没有任何股票信息。")
            return

        response_lines = []
        header = (
            f"{'代码':<8}{'名称':<12}{'初始价':<10}{'当前价':<10}{'波动率':<10}{'行业'}"
        )
        response_lines.append(header)
        response_lines.append("-" * 55)

        for row in stock_data:
            initial_price = row["initial_price"]
            initial_p_str = (
                f"{initial_price:<10.2f}"
                if initial_price is not None
                else f"{'N/A':<10}"
            )
            line = (
                f"{row['stock_id']:<8}"
                f"{row['name']:<12}"
                f"{initial_p_str}"
                f"{row['current_price']:<10.2f}"
                f"{row['volatility']:<10.4f}"
                f"{row['industry']}"
            )
            response_lines.append(line)

        full_response = "```\n" + "\n".join(response_lines) + "\n```"
        yield event.plain_result(full_response)

    @filter.command("订阅股票", alias={"订阅市场"})
    async def subscribe_news(self, event: AstrMessageEvent):
        """订阅随机市场事件快讯"""
        await self._ready_event.wait()
        umo = event.unified_msg_origin
        if umo in self.broadcast_subscribers:
            yield event.plain_result("✅ 您已订阅市场快讯，无需重复操作。")
        else:
            try:
                # 【修正】调用 db_manager
                await self.db_manager.add_subscriber(umo)

                self.broadcast_subscribers.add(umo)
                logger.info(f"新的订阅者已添加并持久化: {umo}")
                yield event.plain_result(
                    "🎉 订阅成功！\n当有随机市场事件发生时，您将会在这里收到推送。"
                )
            except Exception as e:
                logger.error(f"添加订阅者 {umo} 到数据库时失败: {e}", exc_info=True)
                yield event.plain_result("❌ 订阅失败，后台数据库出错。")

    @filter.command("取消订阅股票", alias={"退订市场"})
    async def unsubscribe_news(self, event: AstrMessageEvent):
        """取消订阅随机市场事件快讯"""
        await self._ready_event.wait()
        umo = event.unified_msg_origin
        if umo in self.broadcast_subscribers:
            try:
                # 【修正】调用 db_manager
                await self.db_manager.remove_subscriber(umo)

                self.broadcast_subscribers.remove(umo)
                logger.info(f"订阅者已移除并持久化: {umo}")
                yield event.plain_result("✅ 已为您取消订阅市场快讯。")
            except Exception as e:
                logger.error(f"从数据库移除订阅者 {umo} 时失败: {e}", exc_info=True)
                yield event.plain_result("❌ 取消订阅失败，后台数据库出错。")
        else:
            yield event.plain_result("您尚未订阅市场快讯。")

    async def get_user_asset_rank(self, target_user_id: str) -> tuple[int | str, int]:
        """
        [新版] 获取单个用户的资产排名和总上榜人数 (利用现有的 get_total_asset_ranking API)。
        """
        # 调用您现有的方法获取一个足够长的排行榜，以确保目标用户在其中。
        # 通过设置一个超大的 limit 值，我们实际上就获取了完整的排行榜。
        try:
            full_ranking = await self.get_total_asset_ranking(limit=999999)
        except Exception as e:
            logger.error(
                f"调用 get_total_asset_ranking 获取完整排行时出错: {e}", exc_info=True
            )
            return "查询失败", 0

        total_players = len(full_ranking)
        if total_players == 0:
            return "未上榜", 0

        # 在返回的榜单中查找目标用户
        for i, user_data in enumerate(full_ranking):
            # 使用 .get() 方法以避免因缺少 'user_id' 键而引发错误
            if user_data.get("user_id") == target_user_id:
                return i + 1, total_players  # 返回排名 (索引+1) 和总人数

        return "未上榜", total_players  # 如果用户不在榜上（例如总资产为0）

    @filter.command("总资产", alias={"资产"})
    async def my_total_asset(self, event: AstrMessageEvent):
        """查询当前用户或@用户的个人总资产详情 (金币+股票+公司+银行)，并显示其全服排名"""
        try:
            # ID获取逻辑 (保持不变)
            target_user_id = None
            for component in event.message_obj.message:
                if isinstance(component, Comp.At):
                    target_user_id = str(component.qq)
                    break
            if not target_user_id:
                target_user_id = event.get_sender_id()

            # 并行获取资产详情和排名 (逻辑不变)
            asset_details_task = self.get_user_total_asset(target_user_id)
            asset_rank_task = self.get_user_asset_rank(target_user_id)

            asset_details, (rank, total_players) = await asyncio.gather(
                asset_details_task, asset_rank_task
            )

            if not asset_details:
                yield event.plain_result("未能查询到该用户的资产信息。")
                return

            # --- 核心修改部分 开始 ---

            # 数据提取 (新增 bank_deposits 和 bank_loans)
            total_assets = asset_details.get("total_assets", 0)
            coins = asset_details.get("coins", 0)
            stock_value = asset_details.get("stock_value", 0)
            company_assets = asset_details.get("company_assets", 0)
            bank_deposits = asset_details.get("bank_deposits", 0)  # <--- 新增
            bank_loans = asset_details.get("bank_loans", 0)  # <--- 新增

            # 输出格式化 (逻辑不变)
            is_self_query = target_user_id == event.get_sender_id()
            display_name = target_user_id
            if self.nickname_api:
                custom_nickname = await self.nickname_api.get_nickname(target_user_id)
                if custom_nickname:
                    display_name = custom_nickname
            if is_self_query and display_name == target_user_id:
                display_name = event.get_sender_name()

            title = (
                "💰 您的个人资产报告 💰"
                if is_self_query
                else f"💰 {display_name} 的资产报告 💰"
            )
            rank_text = (
                f"🏆 资产排名: {rank} "
                if isinstance(rank, int)
                else f"🏆 资产排名: {rank}"
            )

            # 结果文本 (新增“银行存款”和“银行贷款”两行)
            result_text = (
                f"{title}\n"
                f"--------------------\n"
                f"🪙 现金余额: {coins:,.2f}\n"
                f"📈 股票市值: {stock_value:,.2f}\n"
                f"🏢 公司资产: {company_assets:,.2f}\n"
                f"💳 银行存款: {bank_deposits:,.2f}\n"  # <--- 新增
                f"🚨 银行贷款: {bank_loans:,.2f}\n"  # <--- 新增
                f"--------------------\n"
                f"🏦 总计资产: {total_assets:,.2f}\n"
                f"{rank_text}"
            )

            # --- 核心修改部分 结束 ---

            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(
                f"查询用户 {event.get_sender_id()} 的总资产失败: {e}", exc_info=True
            )
            yield event.plain_result("查询资产失败了喵~ 可能是服务出了点小问题。")

    @filter.command("总资产排行", alias={"资产榜", "资产排行"})
    async def total_asset_ranking(self, event: AstrMessageEvent):
        """查看总资产排行榜 (金币+股票)"""
        if not self.economy_api:
            yield event.plain_result("错误：经济系统未连接，无法计算总资产排行榜。")
            return

        try:
            # 直接调用公开的API实现方法
            ranking_data = await self.get_total_asset_ranking(limit=20)

            header = "🏆 宇宙总资产排行榜 🏆\n--------------------\n"
            if not ranking_data:
                yield event.plain_result("现在还没有人进行投资，快来成为股神第一人！")
                return

            user_ids_on_ranking = [row["user_id"] for row in ranking_data]
            custom_nicknames = {}
            if self.nickname_api:
                custom_nicknames = await self.nickname_api.get_nicknames_batch(
                    user_ids_on_ranking
                )

            fallback_nicknames = {}
            profiles = await asyncio.gather(
                *[
                    self.economy_api.get_user_profile(uid)
                    for uid in user_ids_on_ranking
                    if uid not in custom_nicknames
                ]
            )
            for profile in profiles:
                if profile:
                    fallback_nicknames[profile["user_id"]] = profile.get("nickname")

            entries = []
            for i, row in enumerate(ranking_data, 1):
                user_id = row["user_id"]
                display_name = (
                    custom_nicknames.get(user_id)
                    or fallback_nicknames.get(user_id)
                    or user_id
                )

                # 【修改】使用新的格式化函数来处理总资产的显示
                formatted_assets = format_large_number(row["total_assets"])

                entries.append(
                    f"🏅 第 {i} 名: {display_name}   总资产: {formatted_assets}"
                )

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(f"获取总资产排行榜失败: {e}", exc_info=True)
            yield event.plain_result("排行榜不见了喵~ 可能是服务出了点小问题。")

    @filter.command("webk", alias={"webk线", "webK线图"})
    async def show_kline_chart_web(
        self, event: AstrMessageEvent, identifier: str | None = None
    ):
        """显示所有股票的K线图Web版，可指定默认显示的股票，并为用户生成专属链接"""
        await self._ready_event.wait()
        if not self.web_server:
            yield event.plain_result("❌ Web服务当前不可用，请联系管理员。")
            return

        user_id = event.get_sender_id()
        current_user_hash = generate_user_hash(user_id)

        if IS_SERVER_DOMAIN:
            base_url = f"{SERVER_DOMAIN}/charts/{current_user_hash}"
        else:
            base_url = f"{SERVER_BASE_URL}/charts/{current_user_hash}"

        if identifier:
            stock = await self.find_stock(identifier)
            if not stock:
                yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
                return

            chart_url = f"{base_url}#{stock.stock_id}"
            message = f"📈 已为您生成【{stock.name}】的实时K线图页面，点击链接查看，您可在此页面自由切换其他股票并查看专属持仓信息：\n{chart_url}"
        else:
            chart_url = base_url
            message = f"📈 已为您生成您的专属实时K线图页面，请点击链接查看所有股票和您的持仓信息：\n{chart_url}"

        yield event.plain_result(message)

    @filter.command("验证")
    async def verify_registration(self, event: AstrMessageEvent, code: str):
        """接收验证码，完成账户的注册和绑定"""
        await self._ready_event.wait()  # 等待初始化

        pending_data = self.pending_verifications.get(code)

        if not pending_data or (datetime.now() - pending_data["timestamp"]) > timedelta(
            minutes=5
        ):
            if code in self.pending_verifications:
                del self.pending_verifications[code]
            yield event.plain_result("❌ 无效或已过期的验证码。")
            return

        qq_user_id = event.get_sender_id()
        login_id = pending_data["login_id"]
        password_hash = pending_data["password_hash"]

        user_exists = await self.db_manager.get_user_by_qq_id(qq_user_id)
        if user_exists:
            yield event.plain_result("✅ 您的QQ号已经绑定了网页账户，无需重复验证。")
            del self.pending_verifications[code]
            return

        await self.db_manager.register_web_user(
            login_id, password_hash, qq_user_id, datetime.now().isoformat()
        )

        del self.pending_verifications[code]
        logger.info(f"用户 {qq_user_id} 成功将网页账户 '{login_id}' 与其绑定。")
        yield event.plain_result(
            f"🎉 恭喜！您的网页账户 '{login_id}' 已成功激活并与您的QQ绑定！现在可以返回网页登录了。"
        )

    @filter.command("重置密码")
    async def reset_password_verify(self, event: AstrMessageEvent, code: str):
        """通过QQ验证重置密码的请求。"""
        await self._ready_event.wait()

        pending_request = self.pending_password_resets.get(code)

        if not pending_request or (
            datetime.now() - pending_request["timestamp"]
        ) > timedelta(minutes=10):
            if code in self.pending_password_resets:
                del self.pending_password_resets[code]
            yield event.plain_result("❌ 无效或已过期的重置码。")
            return

        sender_id = event.get_sender_id()
        if sender_id != pending_request.get("qq_user_id"):
            yield event.plain_result(
                "❌ 验证失败！请使用与该账户绑定的QQ号发送此命令。"
            )
            return

        # Mark as verified
        pending_request["verified"] = True
        pending_request["timestamp"] = (
            datetime.now()
        )  # Refresh timestamp for the final step

        logger.info(
            f"用户 {sender_id} 成功验证了登录ID '{pending_request['login_id']}' 的密码重置请求。"
        )
        yield event.plain_result(
            "✅ 验证成功！请返回网页，设置您的新密码。该验证码在5分钟内有效。"
        )

    @filter.command("炒股帮助", alias={"股票帮助", "stock_help"})
    async def show_plugin_help(self, event: AstrMessageEvent):
        """显示本插件的所有指令帮助"""
        help_text = """
        --- 📈 模拟炒股插件帮助 📉 ---
【基础指令】
/股票 - 查看所有可交易的股票
/行情 <编号/代码/名称> - 查询股票行情
/K线 <编号/代码/名称> - 显示股票K线图
/持仓（图） - 查看您的个人持仓详情（图片）
/webk - 在线网页K线图及持仓信息(推荐)

/资产 - 查看您的当前总资产
【交易指令】
/买入 <标识符> <数量> - 买入指定数量股票
/卖出 <标识符> <数量> - 卖出指定数量股票

【快捷指令】
/梭哈股票 <标识符> - 用全部现金买入该股票
/全抛 <标识符> - 卖出该股票的全部持仓
/清仓 - 卖出您持有的所有股票

【管理员指令】
/添加股票 <代码> <名称> <价格> [波动率] [行业]
/删除股票 <标识符>
"""
        msg = help_text.strip()
        msg = self.forwarder.create_from_text(msg)

        yield event.chain_result([msg])


# """
# 修改股票名称

# /修改股票 ASTR name 星尘宇宙集团

# ✅ 成功将股票 ASTR 的名称修改为: 星尘宇宙集团

# 修改股票代码 (请谨慎操作)

# /修改股票 ASTR stock_id ASTR-U

# ✅ 成功将股票代码 ASTR 修改为: ASTR-U，所有关联数据已同步更新。

# 修改其他参数 (一并提供，方便统一管理)

# /修改股票 ASTR industry 宇宙科技   #行业

# /修改股票 ASTR volatility 0.045  #波动率

# """

# ----------------------------
# LLM Function Tools (带有日志记录的最终版)
# ----------------------------


@filter.llm_tool(name="get_market_overview")
async def llm_get_market_overview(self, event: AstrMessageEvent):
    """
    获取当前股票市场的整体概览信息。你应该使用这些数据向用户总结市场的宏观动态，例如哪些板块/股票在上涨或下跌。

    Args:
        None
    """
    logger.info("LLM 工具 [get_market_overview] 被调用。")
    try:
        # ... (函数体代码保持不变) ...
        stocks = list(self.stocks.values())
        if not stocks:
            logger.warning("LLM 工具 [get_market_overview]: 市场中没有股票数据。")
            return {"error": "市场中没有可用的股票数据。"}
        market_data = []
        for stock in stocks:
            price_change_30m = get_price_change_percentage_30m(stock)
            trend = (
                "上涨"
                if price_change_30m > 0
                else "下跌"
                if price_change_30m < 0
                else "持平"
            )
            market_data.append(
                {
                    "name": stock.name,
                    "code": stock.stock_id,
                    "price": f"{stock.current_price:.2f}",
                    "change_30m_percent": f"{price_change_30m:.2f}",
                    "trend": trend,
                }
            )
        logger.info(
            "LLM 工具 [get_market_overview] 成功执行，将数据返回给LLM进行处理。"
        )
        return market_data
    except Exception as e:
        logger.error(f"LLM 工具 [get_market_overview] 执行出错: {e}", exc_info=True)
        return {"error": "获取市场概览时发生内部错误。"}


@filter.llm_tool(name="get_stock_detail")
async def llm_get_stock_detail(self, event: AstrMessageEvent, stock_code: str):
    """
    获取指定股票的详细数据，包括当前价格和24小时价格历史。你应该基于返回的数据为用户解读关键信息，例如识别近期的高点/低点、价格波动范围等。

    Args:
        stock_code(string): 需要查询的股票代码或名称。
    """
    logger.info(f"LLM 工具 [get_stock_detail] 被调用，参数 stock_code: {stock_code}")
    try:
        # ... (函数体代码保持不变) ...
        stock = await self.find_stock(stock_code)
        if not stock:
            logger.warning(f"LLM 工具 [get_stock_detail]: 找不到股票 {stock_code}。")
            return {"error": f"找不到代码或名称为 '{stock_code}' 的股票。"}
        history = get_stock_price_history_24h(stock)
        detail_data = {
            "name": stock.name,
            "code": stock.stock_id,
            "price": f"{stock.current_price:.2f}",
            "24h_history_hourly": [
                (ts.strftime("%H:%M"), f"{price:.2f}") for ts, price in history
            ],
        }
        logger.info(
            f"LLM 工具 [get_stock_detail] 成功执行，将为'{stock_code}'的数据返回给LLM。"
        )
        return detail_data
    except Exception as e:
        logger.error(f"LLM 工具 [get_stock_detail] 执行出错: {e}", exc_info=True)
        return {"error": "获取股票详情时发生内部错误。"}


@filter.llm_tool(name="get_user_portfolio")
async def llm_get_user_portfolio(self, event: AstrMessageEvent):
    """
    查询当前玩家的游戏持仓和现金余额。你应该使用这些信息为玩家总结其资产状况，例如总市值、总盈亏，并可以结合市场行情给出操作建议。

    Args:
        None
    """
    logger.info("LLM 工具 [get_user_portfolio] 被调用...")
    try:
        # ... (函数体代码保持不变) ...
        user_id = event.get_sender_id()
        portfolio_task = self.db_manager.get_user_holdings_aggregated(user_id)
        balance_task = self.economy_api.get_coins(user_id)
        portfolio, balance = await asyncio.gather(portfolio_task, balance_task)
        if not portfolio:
            logger.info(f"LLM 工具 [get_user_portfolio]: 用户 {user_id} 无持仓。")
            return {
                "cash_balance": f"{balance:.2f}",
                "holdings": [],
                "summary": {"total_market_value": "0.00", "total_pnl": "0.00"},
            }
        holdings_data = []
        total_value = 0
        total_pnl = 0
        for stock_id, holding_info in portfolio.items():
            stock = self.stocks.get(stock_id)
            if stock:
                quantity = holding_info["quantity"]
                market_value = quantity * stock.current_price
                total_value += market_value
                cost_basis = holding_info["cost_basis"]
                pnl = market_value - cost_basis
                total_pnl += pnl
                pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
                holdings_data.append(
                    {
                        "name": stock.name,
                        "code": stock_id,
                        "shares": quantity,
                        "market_value": f"{market_value:.2f}",
                        "pnl": f"{pnl:+.2f}",
                        "pnl_percent": f"{pnl_percent:+.2f}",
                    }
                )
        result_data = {
            "cash_balance": f"{balance:.2f}",
            "holdings": holdings_data,
            "summary": {
                "total_market_value": f"{total_value:.2f}",
                "total_pnl": f"{total_pnl:+.2f}",
            },
        }
        logger.info("LLM 工具 [get_user_portfolio] 成功执行，数据已返回给LLM。")
        return result_data
    except Exception as e:
        logger.error(f"LLM 工具 [get_user_portfolio] 执行出错: {e}", exc_info=True)
        return {"error": "查询用户持仓时发生内部错误。"}


@filter.llm_tool(name="get_user_assets")
async def llm_get_user_assets(self, event: AstrMessageEvent):
    """
    查询当前玩家在游戏中的现金余额。

    Args:
        None
    """
    logger.info("LLM 工具 [get_user_assets] 被调用...")
    try:
        # ... (函数体代码保持不变) ...
        user_id = event.get_sender_id()
        balance = await self.economy_api.get_coins(user_id)
        result_data = {"cash_balance": f"{balance:.2f}"}
        logger.info(f"LLM 工具 [get_user_assets] 成功执行，数据: {result_data}")
        return result_data
    except Exception as e:
        logger.error(f"LLM 工具 [get_user_assets] 执行出错: {e}", exc_info=True)
        return {"error": "查询用户现金余额时发生内部错误。"}


@filter.llm_tool(name="buy_stock")
async def llm_buy_stock(self, event: AstrMessageEvent, stock_code: str, shares: int):
    """
    为当前玩家执行购买指定数量股票的游戏操作。这是一个最终执行动作，在调用前必须先向玩家提议并获得明确同意。

    Args:
        stock_code(string): 要购买的股票代码或名称。
        shares(number): 希望购买的股票数量。
    """
    logger.info("LLM 工具 [buy_stock] 被调用...")
    try:
        # ... (函数体代码保持不变) ...
        user_id = event.get_sender_id()
        success, message = await self.trading_manager.perform_buy(
            user_id, stock_code, shares
        )
        result = {"success": success, "action": "buy", "message": message}
        logger.info(f"LLM 工具 [buy_stock] 成功执行，结果: {result}")
        return result
    except Exception as e:
        logger.error(f"LLM 工具 [buy_stock] 执行出错: {e}", exc_info=True)
        return {"success": False, "message": "执行购买操作时发生内部错误。"}


@filter.llm_tool(name="sell_stock")
async def llm_sell_stock(self, event: AstrMessageEvent, stock_code: str, shares: int):
    """
    为当前玩家执行出售指定数量股票的游戏操作。这是一个最终执行动作，在调用前必须先向玩家提议并获得明确同意。

    Args:
        stock_code(string): 要出售的股票代码或名称。
        shares(number): 希望出售的股票数量。
    """
    logger.info("LLM 工具 [sell_stock] 被调用...")
    try:
        # ... (函数体代码保持不变) ...
        user_id = event.get_sender_id()
        success, message, data = await self.trading_manager.perform_sell(
            user_id, stock_code, shares
        )
        result = {
            "success": success,
            "action": "sell",
            "message": message,
            "details": data or {},
        }
        logger.info(f"LLM 工具 [sell_stock] 成功执行，结果: {result}")
        return result
    except Exception as e:
        logger.error(f"LLM 工具 [sell_stock] 执行出错: {e}", exc_info=True)
        return {"success": False, "message": "执行出售操作时发生内部错误。"}


@filter.llm_tool(name="all_in_stock")
async def llm_all_in_stock(self, event: AstrMessageEvent, stock_code: str):
    """
    为当前玩家执行梭哈（用全部现金购买某支股票）的游戏操作。此为高风险操作，在调用前必须明确告知玩家风险并获得其同意。

    Args:
        stock_code(string): 要梭哈的股票代码或名称。
    """
    logger.info("LLM 工具 [all_in_stock] 被调用...")
    try:
        # ... (函数体代码保持不变) ...
        user_id = event.get_sender_id()
        success, message = await self.trading_manager.perform_buy_all_in(
            user_id, stock_code
        )
        result = {"success": success, "action": "all_in", "message": message}
        logger.info(f"LLM 工具 [all_in_stock] 成功执行，结果: {result}")
        return result
    except Exception as e:
        logger.error(f"LLM 工具 [all_in_stock] 执行出错: {e}", exc_info=True)
        return {"success": False, "message": "执行梭哈操作时发生内部错误。"}


@filter.llm_tool(name="sell_all_stocks")
async def llm_sell_all_stocks(self, event: AstrMessageEvent, stock_code: str = None):
    """
    为当前玩家执行清仓（卖出所有持仓）或全抛（卖出单支股票的全部）的游戏操作。此为高风险操作，在调用前必须明确告知玩家风险并获得其同意。

    Args:
        stock_code(string, optional): 要全抛的单支股票代码。若不提供，则为清仓。
    """
    logger.info("LLM 工具 [sell_all_stocks] 被调用...")
    try:
        # ... (函数体代码保持不变) ...
        user_id = event.get_sender_id()
        if stock_code:
            success, message = await self.trading_manager.perform_sell_all_for_stock(
                user_id, stock_code
            )
        else:
            success, message = await self.trading_manager.perform_sell_all_portfolio(
                user_id
            )
        result = {"success": success, "action": "sell_all", "message": message}
        logger.info(f"LLM 工具 [sell_all_stocks] 成功执行，结果: {result}")
        return result
    except Exception as e:
        logger.error(f"LLM 工具 [sell_all_stocks] 执行出错: {e}", exc_info=True)
        return {"success": False, "message": "执行清仓/全抛操作时发生内部错误。"}
