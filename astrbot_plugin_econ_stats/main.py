# astrbot_plugin_econ_stats/main.py

import asyncio
import os
import datetime
import aiosqlite
import jinja2
import json
from playwright.async_api import async_playwright
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from ..common.services import shared_services
import astrbot.api.message_components as Comp


PERSONAL_STATS_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
    body { 
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; 
        background-color: #f4f4f4; 
        padding: 20px; 
        width: 600px; 
    }
    .container { background-color: white; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); padding: 25px; border-top: 5px solid #DAA520; }
    h1 { font-size: 24px; font-weight: 700; color: #333; text-align: center; margin-top: 0; margin-bottom: 5px;}
    .user-id-span {
        font-weight: normal;
        color: #888;
        font-size: 0.8em;
    }
    table { width: 100%; border-collapse: separate; border-spacing: 10px; margin-top: 15px; }
    td { width: 50%; background-color: #FFFDF5; border: 1px solid #F0E68C; border-radius: 8px; padding: 15px; text-align: center; }
    h3 { margin: 0 0 10px 0; color: #6c757d; font-size: 16px; font-weight: 500;}
    .value { font-size: 24px; font-weight: 700; }
    .balance { color: #DAA520; }
    .source { color: #B8860B; }
    .sink { color: #8B4513; }
    .net-positive { color: #B8860B; }
    .net-negative { color: #8B4513; }
    .footer { text-align: center; margin-top: 25px; font-size: 12px; color: #aaa; }
</style>
</head>
<body>
    <div class="container">
        <h1>个人数据报告</h1>
        
        <table>
            <tr>
                <td colspan="2" style="padding-bottom: 5px;">
                    <h3 style="font-size: 20px; font-weight: 700; margin: 0;">
                        {{ nickname }} 
                        <span class="user-id-span">({{ user_id }})</span>
                    </h3>
                </td>
            </tr>

            <tr>
                <td>
                    <h3>{{ ranking_type }}</h3>
                    <p class="value balance">{{ current_balance }}</p>
                </td>
                <td>
                    <h3>财富排名</h3>
                    <p class="value">{{ coin_rank }}</p>
                </td>
            </tr>
            <tr>
                <td><h3>总收入 (近7日)</h3><p class="value source">+{{ week_income }}</p></td>
                <td><h3>总支出 (近7日)</h3><p class="value sink">-{{ week_expenditure }}</p></td>
            </tr>
            <tr>
                 <td><h3>净收入 (近7日)</h3><p class="value {{ 'net-positive' if week_net_income >= 0 else 'net-negative' }}">{{ '%+d'|format(week_net_income) }}</p></td>
                 <td><h3>今日净收入</h3><p class="value {{ 'net-positive' if today_net_income >= 0 else 'net-negative' }}">{{ '%+d'|format(today_net_income) }}</p></td>
            </tr>
            <tr>
                <td colspan="2"><h3>抽奖专家分析 (近7日)</h3></td>
            </tr>
            <tr>
                <td><h3>抽奖净收益</h3><p class="value {{ 'net-positive' if lottery_net_profit >= 0 else 'net-negative' }}">{{ '%+d'|format(lottery_net_profit) }}</p></td>
                <td><h3>抽奖胜率</h3><p class="value">{{ lottery_win_rate }}%</p></td>
            </tr>
            <tr>
                <td><h3>平均中奖倍率</h3><p class="value">{{ avg_lottery_multiplier }}x</p></td>
                <td><h3>本周运势</h3><p class="value">{{ best_fortune }}</p></td>
            </tr>
        </table>
        <p class="footer">报告生成于: {{ update_time }}</p>
    </div>
</body>
</html>
"""


# ==================================
#         主看板的HTML模板 (V3)
# ==================================
STATS_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f0f2f5; padding: 20px; width: 600px; }
    .container { background-color: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); padding: 25px; }
    h1 { color: #1c1e21; text-align: center; border-bottom: 1px solid #ddd; padding-bottom: 15px; margin-top: 0; }
    table { width: 100%; border-collapse: separate; border-spacing: 10px; margin-top: 15px; }
    td { width: 50%; background-color: #f7f8fa; border: 1px solid #e0e0e0; border-radius: 6px; padding: 15px; text-align: center; }
    h3 { margin: 0 0 10px 0; color: #606770; font-size: 16px; font-weight: 500;}
    .value { font-size: 24px; font-weight: bold; }
    .source { color: #42b72a; }
    .sink { color: #f02849; }
    .net-positive { color: #42b72a; }
    .net-negative { color: #f02849; }
    .footer { text-align: center; margin-top: 20px; font-size: 12px; color: #90949c; }
</style>
</head>
<body>
    <div class="container">
        <h1>经济数据看板 (每日)</h1>
        <table>
            <tr>
                <td><h3>全服总资产 (现金+股票)</h3><p class="value">{{ total_supply }}</p></td>
                <td><h3>本日净增长</h3><p class="value {{ net_change_class }}">{{ net_change }}</p></td>
            </tr>
            <tr>
                <td><h3>本日总产出</h3><p class="value source">{{ source }}</p></td>
                <td><h3>本日总回收</h3><p class="value sink">{{ sink }}</p></td>
            </tr>
            <tr>
                <td><h3>回收率 (本日)</h3><p class="value">{{ recycling_rate }}%</p></td>
                <td><h3>本日活跃人数</h3><p class="value">{{ active_users }}</p></td>
            </tr>
             <tr>
                <td><h3>本日活动总产出</h3><p class="value source">{{ total_activity_rewards }}</p></td>
                <td><h3>人均活动收益</h3><p class="value">{{ avg_activity_reward }}</p></td>
            </tr>
        </table>
        <p class="footer">数据更新于: {{ update_time }}</p>
    </div>
</body>
</html>
"""


CHART_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8"><title>资产增长图</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; 
            background-color: #f0f2f5; 
            padding: 10px; 
        }
        .chart-container {
            width: 780px; 
            height: 420px; 
            background-color: white;
            border-radius: 8px; 
            padding: 20px; 
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
    </style>
</head>
<body>
    <div class="chart-container">
        <canvas id="coinChart"></canvas>
    </div>

    <script>
        const ctx = document.getElementById('coinChart').getContext('2d');
        
        const gradient = ctx.createLinearGradient(0, 0, 0, 400);
        gradient.addColorStop(0, 'rgba(218, 165, 32, 0.4)');
        gradient.addColorStop(1, 'rgba(255, 253, 245, 0.1)');

        new Chart(ctx, {
            type: 'line',
            data: {
                labels: {{ labels|safe }}, 
                datasets: [{
                    label: '全服总资产 (15min)',
                    data: {{ data|safe }},
                    borderColor: '#DAA520',
                    backgroundColor: gradient,
                    borderWidth: 2,
                    tension: 0.3,
                    fill: true,
                    pointBackgroundColor: '#B8860B',
                    pointRadius: 2,
                    pointHoverRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                    },
                    title: {
                        display: true,
                        text: '近 {{ days_count }} 全服总资产趋势',
                        font: {
                            size: 18,
                            family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: false 
                    }
                }
            }
        });
    </script>
</body>
</html>
"""

class StatsDatabase:
    def __init__(self, plugin_dir: str):
        db_dir = os.path.join(os.path.dirname(os.path.dirname(plugin_dir)), "plugins_db")
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)
        self.db_path = os.path.join(db_dir, "astrbot_plugin_econ_stats.db")
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        
        # 1. 每日快照 (用于每日看板)
        await self.conn.execute('''CREATE TABLE IF NOT EXISTS daily_snapshots (
            date TEXT PRIMARY KEY,
            total_supply INTEGER,
            net_change REAL,
            source REAL,
            sink REAL,
            active_users INTEGER,
            total_activity_rewards INTEGER 
        )''')
        
        # 2. 全局资产快照 (每15分钟)
        await self.conn.execute('''CREATE TABLE IF NOT EXISTS global_wealth_snapshots (
            timestamp INTEGER PRIMARY KEY,
            date_str TEXT,
            total_wealth REAL,
            cash_supply REAL,
            stock_value REAL
        )''')

        # 3. 用户资产快照 (每2小时, 用于视频)
        await self.conn.execute('''CREATE TABLE IF NOT EXISTS user_asset_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            user_id TEXT,
            total_assets REAL,
            cash REAL,
            stock REAL
        )''')

        await self.conn.commit()

    async def save_snapshot(self, data: dict):
        query = """
            INSERT OR REPLACE INTO daily_snapshots 
            (date, total_supply, net_change, source, sink, active_users, total_activity_rewards) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        await self.conn.execute(query, (
            data['date'], data['total_supply'], data['net_change'],
            data['source'], data['sink'], data['active_users'], data['total_activity_rewards']
        ))
        await self.conn.commit()

    async def save_global_wealth_15m(self, data: dict):
        """保存15分钟粒度的全局数据"""
        query = """
            INSERT OR REPLACE INTO global_wealth_snapshots 
            (timestamp, date_str, total_wealth, cash_supply, stock_value) 
            VALUES (?, ?, ?, ?, ?)
        """
        await self.conn.execute(query, (
            data['timestamp'], data['date_str'], data['total_wealth'],
            data['cash_supply'], data['stock_value']
        ))
        await self.conn.commit()

    async def save_user_stats_batch(self, records: list):
        """批量保存用户资产快照"""
        if not records:
            return
        query = """
            INSERT INTO user_asset_snapshots (timestamp, user_id, total_assets, cash, stock)
            VALUES (?, ?, ?, ?, ?)
        """
        await self.conn.executemany(query, records)
        await self.conn.commit()

    async def get_recent_global_wealth(self, days: int = 7):
        """获取最近 N 天的 15 分钟粒度数据"""
        cutoff = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp())
        query = "SELECT * FROM global_wealth_snapshots WHERE timestamp > ? ORDER BY timestamp ASC"
        async with self.conn.execute(query, (cutoff,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows] if rows else []

    async def get_snapshot_by_date(self, date_str: str):
        query = "SELECT * FROM daily_snapshots WHERE date = ?"
        async with self.conn.execute(query, (date_str,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
            
    async def get_recent_snapshots(self, limit: int = 7):
        query = "SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT ?"
        async with self.conn.execute(query, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in reversed(rows)] if rows else []

@register("econ_stats", "timetetng", "经济系统数据统计与看板", "1.0.0")
class EconStatsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.db = StatsDatabase(os.path.dirname(__file__))
        self.is_running = True
        self.background_task = None
        self.update_lock = asyncio.Lock()
        self.economy_api = None
        asyncio.create_task(self.initialize_and_run_task())

    async def initialize_and_run_task(self):
        logger.info("正在等待经济系统API加载...")
        timeout_seconds = 30 
        start_time = asyncio.get_event_loop().time()
        
        while self.economy_api is None:
            self.economy_api = shared_services.get("economy_api")
            if self.economy_api is None:
                if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                    logger.warning("等待经济系统API超时，插件功能将受限！")
                    break
                await asyncio.sleep(1) 
        
        if self.economy_api:
            logger.info("经济系统API已成功加载。")
            await self._clear_cache_on_startup() 
            await self.db.connect()
            # 立即运行一次15分钟记录，保证启动即有数据
            await self._record_15m_stats()
            self.background_task = asyncio.create_task(self.run_statistics_periodically())
        else:
            logger.warning("经济系统API未找到，插件功能将受限！")

    async def terminate(self):
        self.is_running = False
        if self.background_task:
            self.background_task.cancel()
        if self.db and self.db.conn:
            await self.db.conn.close()
        logger.info("经济统计插件已停止。")

    async def _clear_cache_on_startup(self, retention_days: int = 1):
        cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        if not os.path.isdir(cache_dir): return
        now = datetime.datetime.now().timestamp()
        retention_period = retention_days * 24 * 60 * 60 
        try:
            for filename in os.listdir(cache_dir):
                file_path = os.path.join(cache_dir, filename)
                if os.path.isfile(file_path) and filename.endswith('.png'):
                    if (now - os.path.getmtime(file_path)) > retention_period:
                        os.remove(file_path)
        except Exception as e:
            logger.error(f"清理缓存时发生错误: {e}")

    # ================= 核心统计逻辑 =================

    async def _get_system_total_wealth(self):
        """获取系统总资产（现金+股票）。
        由于 Stock API 没有直接返回市场总值的接口，我们通过获取 Top N 用户的总资产之和来近似（或者如果API允许直接获取）。
        """
        economy_api = shared_services.get("economy_api")
        stock_api = shared_services.get("stock_market_api")
        
        cash_supply = 0
        total_wealth = 0
        
        # 1. 获取现金总量 (EconomyAPI)
        if economy_api:
            # 尝试调用底层 DB 获取现金总量
            try:
                if hasattr(economy_api, '_db') and hasattr(economy_api._db, 'get_total_coin_supply'):
                    cash_supply = await economy_api._db.get_total_coin_supply()
                else:
                    # Fallback: 如果没有底层方法，暂时设为0或通过排行估算
                    pass
            except Exception:
                pass

        # 2. 获取总财富 (现金+股票)
        if stock_api:
            try:
                # 利用 get_total_asset_ranking 获取全服（限制较大数量）的总资产之和
                # 假设前 2000 名用户占据了绝大部分财富
                ranking = await stock_api.get_total_asset_ranking(limit=2000)
                total_wealth = sum(user_data.get('total_assets', 0) for user_data in ranking)
            except Exception as e:
                logger.warning(f"无法从 Stock API 计算总市值: {e}")
                total_wealth = cash_supply # 降级为仅现金
        else:
            total_wealth = cash_supply

        # 3. 倒推股票/其他资产价值
        stock_value = total_wealth - cash_supply
        
        return cash_supply, stock_value, total_wealth

    async def _record_15m_stats(self):
        """记录15分钟粒度的全局数据"""
        try:
            now = datetime.datetime.now()
            cash, stock, total = await self._get_system_total_wealth()
            
            data = {
                "timestamp": int(now.timestamp()),
                "date_str": now.strftime('%Y-%m-%d %H:%M:%S'),
                "total_wealth": total,
                "cash_supply": cash,
                "stock_value": stock
            }
            await self.db.save_global_wealth_15m(data)
            logger.debug(f"已记录15分钟经济数据: Total={total}")
        except Exception as e:
            logger.error(f"记录15分钟数据失败: {e}")

    async def _record_2h_user_stats(self):
        """记录每2小时的用户资产快照（Top 1000）"""
        try:
            logger.info("开始执行每2小时用户资产快照记录...")
            economy_api = shared_services.get("economy_api")
            stock_api = shared_services.get("stock_market_api")
            
            # 使用总资产排行获取活跃用户，这通常比 Economy 的金币排行更准确反映财富
            target_users = []
            if stock_api:
                ranking = await stock_api.get_total_asset_ranking(limit=1000)
                target_users = ranking # [{'user_id':..., 'total_assets':...}, ...]
            elif economy_api:
                ranking = await economy_api.get_ranking(limit=1000)
                target_users = ranking # [{'user_id':..., 'coins':...}, ...]
            
            now_ts = int(datetime.datetime.now().timestamp())
            records = []
            
            for u in target_users:
                uid = u.get('user_id')
                if not uid: continue
                
                # 获取各项资产
                u_cash = 0
                u_total = 0
                
                # 1. 获取现金
                if economy_api:
                    u_cash = await economy_api.get_coins(uid)
                
                # 2. 获取总资产 (优先信赖 StockAPI 的 total_assets)
                if stock_api:
                    # 如果 target_users 来源于 StockAPI，直接取值
                    if 'total_assets' in u:
                        u_total = u['total_assets']
                    else:
                        # 否则单独查询
                        asset_info = await stock_api.get_user_total_asset(uid)
                        u_total = asset_info.get('total_assets', u_cash)
                else:
                    u_total = u_cash

                u_stock = u_total - u_cash
                records.append((now_ts, uid, u_total, u_cash, u_stock))
            
            await self.db.save_user_stats_batch(records)
            logger.info(f"已保存 {len(records)} 名用户的资产快照")

        except Exception as e:
            logger.error(f"用户资产快照记录失败: {e}", exc_info=True)

    async def _generate_snapshot_for_date(self, target_date: datetime.date, current_total_wealth: int) -> dict:
        """为每日看板生成数据"""
        date_str = target_date.strftime('%Y-%m-%d')
        now = datetime.datetime.now()
        economy_api = shared_services.get("economy_api") # 主要用于流向统计
        
        # 注意：每日看板的 "Net Change" 和 "Flow" 依然基于 EconomyAPI 的金币流水
        # 因为 StockAPI 通常不记录详细的流水日志
        
        # 1. 计算历史总量 (简化：仅记录当天的最终 Total Wealth，不反推历史)
        # 如果是生成当天数据，直接使用传入的 current_total_wealth
        historical_supply = current_total_wealth

        start_of_day = datetime.datetime.combine(target_date, datetime.time.min)
        end_of_day = now if target_date == now.date() else datetime.datetime.combine(target_date, datetime.time.max)
        
        flow_summary = {'source': 0, 'sink': 0}
        active_users = 0
        total_activity_rewards = 0

        if economy_api:
             # 使用统一的时间格式
            flow_summary = await economy_api._db.get_coin_flow_summary(
                start_of_day.strftime('%Y-%m-%d %H:%M:%S'), 
                end_of_day.strftime('%Y-%m-%d %H:%M:%S')
            )
            active_users = await economy_api._db.get_active_user_count_on_date(date_str)
            total_activity_rewards = await economy_api._db.get_total_activity_rewards_on_date(date_str)

        snapshot_data = {
            "date": date_str,
            "total_supply": historical_supply, # 这里的 Total Supply 变成了总资产
            "net_change": flow_summary['source'] - flow_summary['sink'], # 净增长依然是金币维度的
            "source": flow_summary['source'],
            "sink": flow_summary['sink'],
            "active_users": active_users,
            "total_activity_rewards": total_activity_rewards
        }
        await self.db.save_snapshot(snapshot_data)
        return snapshot_data

    async def _update_snapshot_data(self) -> dict:
        """更新每日看板数据"""
        async with self.update_lock:
            today = datetime.date.today()
            economy_api = shared_services.get("economy_api")
            if not economy_api: return {}
            
            logger.info("正在执行每日数据快照更新...")
            # 获取当前系统总资产
            _, _, total_wealth = await self._get_system_total_wealth()
            return await self._generate_snapshot_for_date(today, total_wealth)

    async def run_statistics_periodically(self):
        """后台定时任务：每15分钟唤醒一次"""
        logger.info(f"经济统计后台任务已启动 (Interval: 15m)")
        while self.is_running:
            try:
                now = datetime.datetime.now()
                
                # 1. 总是执行：15分钟粒度的全局记录
                await self._record_15m_stats()
                
                # 2. 条件执行：每2小时执行一次用户快照 (整点偶数小时，且在开头15分钟内)
                # 例如 00:00-00:15, 02:00-02:15 ...
                if now.hour % 2 == 0 and now.minute < 15:
                    await self._record_2h_user_stats()
                
                # 3. 每日更新 daily_snapshots (用于每日看板)
                if now.hour == 0 and now.minute < 15:
                    await self._update_snapshot_data()

                # 固定每 15 分钟 (900秒) 唤醒一次
                await asyncio.sleep(900)

            except asyncio.CancelledError:
                logger.info("经济统计任务被取消。")
                break
            except Exception as e:
                logger.error(f"经济统计后台任务出现错误: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def render_html_locally(self, html_content: str) -> str:
        plugin_dir = os.path.dirname(__file__)
        cache_dir = os.path.join(plugin_dir, "cache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        timestamp = int(datetime.datetime.now().timestamp())
        image_path = os.path.join(cache_dir, f"stats_{timestamp}.png")
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html_content, wait_until="domcontentloaded")
            bounding_box = await page.locator('body > div').bounding_box()
            if bounding_box:
                await page.set_viewport_size({"width": 820, "height": int(bounding_box['height']) + 20})
            await page.locator('body > div').screenshot(path=image_path)
            await browser.close()
        return image_path

    async def _generate_and_send_dashboard(self, event: AstrMessageEvent, data: dict):
        try:
            if not data:
                yield event.plain_result("错误：无法生成看板，因为没有可用的数据。")
                return

            now = datetime.datetime.now()
            avg_activity_reward = (data['total_activity_rewards'] / data['active_users']) if data['active_users'] > 0 else 0
            
            render_data = {
                "total_supply": f"{data['total_supply']:,}", # 全服总资产
                "net_change": f"{data['net_change']:+.0f}",
                "net_change_class": "net-positive" if data['net_change'] >= 0 else "net-negative",
                "source": f"{data['source']:.0f}", "sink": f"{data['sink']:.0f}",
                "recycling_rate": f"{(data['sink'] / data['source'] * 100) if data['source'] > 0 else 0:.2f}",
                "active_users": data['active_users'],
                "total_activity_rewards": f"{data['total_activity_rewards']:,}",
                "avg_activity_reward": f"{avg_activity_reward:.1f}",
                "update_time": now.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            template = jinja2.Template(STATS_HTML_TEMPLATE)
            final_html = template.render(render_data)
            image_path = await self.render_html_locally(final_html)
            yield event.image_result(image_path)
            
        except Exception as e:
            logger.error(f"生成经济看板图片时失败: {e}", exc_info=True)
            yield event.plain_result(f"生成看板图片时发生错误，请查看后台日志。")

    @filter.command("经济看板", alias={'数据看板', 'stats'})
    async def show_stats_dashboard(self, event: AstrMessageEvent):
        try:
            now = datetime.datetime.now()
            today_str = now.strftime('%Y-%m-%d')
            data = await self.db.get_snapshot_by_date(today_str)
            if not data:
                yield event.plain_result("暂无今日数据。请等待定时任务更新，或联系管理员使用 /刷新数据 命令立即生成。")
                return
            async for result in self._generate_and_send_dashboard(event, data):
                yield result
        except Exception as e:
            logger.error(f"生成经济看板失败: {e}", exc_info=True)
            yield event.plain_result(f"生成经济看板时发生错误，请查看后台日志。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新数据", alias={'refresh_stats'})
    async def refresh_data(self, event: AstrMessageEvent):
        try:
            yield event.plain_result("正在刷新经济数据并生成看板，请稍候...")
            updated_data = await self._update_snapshot_data()
            if not updated_data:
                yield event.plain_result("❌ 数据刷新失败，未能获取到有效数据。请检查后台日志。")
                return
            async for result in self._generate_and_send_dashboard(event, updated_data):
                yield result
        except Exception as e:
            logger.error(f"手动刷新数据失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 刷新数据时发生错误，请检查后台日志。")

    @filter.command("金币增长图", alias={'金币统计', '金币曲线',"金币变化图", "资产变化图"})
    async def show_growth_chart(self, event: AstrMessageEvent):
        """显示全服总资产变化曲线 (15分钟粒度)"""
        try:
            days = self.config.get("chart_days", 3)
            # 获取 15 分钟粒度的数据
            snapshots = await self.db.get_recent_global_wealth(days=days)

            if len(snapshots) < 2:
                yield event.plain_result(f"历史数据不足（需要至少2个记录点）。\n数据正在每15分钟收集中，请稍后再试。")
                return
            
            # 准备数据：格式化时间戳
            labels = [datetime.datetime.fromtimestamp(item['timestamp']).strftime('%m-%d %H:%M') for item in snapshots]
            # 数据使用 Total Wealth (Total Assets)
            total_supply_data = [item['total_wealth'] for item in snapshots]

            # 简单的降采样，防止点太多
            if len(labels) > 200:
                step = len(labels) // 200
                labels = labels[::step]
                total_supply_data = total_supply_data[::step]

            render_data = {
                "labels": json.dumps(labels),
                "data": json.dumps(total_supply_data),
                "days_count": f"{days}天 (15min粒度)"
            }

            template = jinja2.Template(CHART_HTML_TEMPLATE)
            final_html = template.render(render_data)
            image_path = await self.render_html_locally(final_html)
            yield event.image_result(image_path)

        except Exception as e:
            logger.error(f"生成增长图失败: {e}", exc_info=True)
            yield event.plain_result(f"生成增长图时发生错误，请查看后台日志。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("回填数据", alias={'backfill_stats'})
    async def backfill_stats(self, event: AstrMessageEvent):
        """[注意] 回填功能仅支持每日看板数据，不支持15分钟粒度的曲线图回填"""
        try:
            days_to_backfill = self.config.get("chart_days", 7)
            yield event.plain_result(f"开始回填过去 {days_to_backfill} 天的【每日看板】数据...")
            
            # 使用当前的总资产近似
            _, _, current_total = await self._get_system_total_wealth()
            
            today = datetime.date.today()
            for i in range(days_to_backfill):
                target_day = today - datetime.timedelta(days=i)
                date_str = target_day.strftime('%Y-%m-%d')
                if await self.db.get_snapshot_by_date(date_str) is None:
                    await self._generate_snapshot_for_date(target_day, current_total)
                    await asyncio.sleep(0.5)
            
            yield event.plain_result(f"✅ 数据回填成功！")

        except Exception as e:
            logger.error(f"数据回填失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 数据回填时发生错误，请检查后台日志。")

    @filter.command("个人数据", alias={'数据统计', 'personal_stats'})
    async def show_personal_stats(self, event: AstrMessageEvent):
        try:
            user_id = event.get_sender_id()
            default_user_name = event.get_sender_name()
            now = datetime.datetime.now()
            yield event.plain_result(f"正在为 {default_user_name} 生成个人数据报告，请稍候...")

            economy_api = shared_services.get("economy_api")
            nickname_api = shared_services.get("nickname_api")
            stock_market_api = shared_services.get("stock_market_api")
            
            if not economy_api:
                yield event.plain_result("❌ 错误：经济系统 (EconomyAPI) 未加载。")
                return
            
            tasks = {
                "week_flow": economy_api._db.get_personal_flow_summary(user_id, days=7),
                "today_flow": economy_api._db.get_personal_flow_summary(user_id, days=1),
                "lottery_stats": economy_api._db.get_personal_lottery_stats(user_id, days=7),
                "nickname": nickname_api.get_nickname(user_id) if nickname_api else asyncio.sleep(0, result=None),
            }

            if stock_market_api:
                tasks["assets"] = stock_market_api.get_user_total_asset(user_id)
                tasks["ranking"] = stock_market_api.get_total_asset_ranking(limit=100)
                ranking_type = "总资产 (现金+股票)"
            else:
                tasks["assets"] = economy_api.get_coins(user_id)
                tasks["ranking"] = economy_api.get_ranking(limit=100)
                ranking_type = "金币"

            results = await asyncio.gather(*tasks.values())
            res_dict = dict(zip(tasks.keys(), results))
            
            display_name = res_dict['nickname'] or default_user_name
            
            user_rank = "未上榜"
            ranking_list = res_dict.get('ranking', [])
            for i, user_data in enumerate(ranking_list):
                if user_data.get('user_id') == user_id:
                    user_rank = f"第 {i + 1} 名"
                    break
            
            if stock_market_api:
                asset_info = res_dict['assets']
                display_balance = f"{asset_info.get('total_assets', 0):,.2f}"
            else: 
                display_balance = f"{res_dict['assets']:,}"

            l_stats = res_dict['lottery_stats']
            total_plays = l_stats['total_plays']
            lottery_win_rate = (l_stats['profitable_wins'] / total_plays * 100) if total_plays > 0 else 0
            best_fortune = "未知"
            if total_plays >= 3:
                if lottery_win_rate > 80: best_fortune = "大吉"
                elif lottery_win_rate > 65: best_fortune = "吉"
                elif lottery_win_rate > 50: best_fortune = "小吉"
                elif lottery_win_rate > 35: best_fortune = "末小吉"
                elif lottery_win_rate > 20: best_fortune = "凶"
                else: best_fortune = "大凶" if lottery_win_rate > 0 else "非酋"
            lottery_net_profit = l_stats['total_won'] - l_stats['total_spent']
            avg_lottery_multiplier = l_stats['avg_multiplier'] if l_stats['avg_multiplier'] else 0
            week_flow = res_dict['week_flow']
            today_flow = res_dict['today_flow']

            render_data = {
                "nickname": display_name,
                "user_id": user_id,
                "current_balance": display_balance,
                "coin_rank": user_rank,
                "ranking_type": ranking_type,
                "week_income": f"{week_flow['income']:,}",
                "week_expenditure": f"{week_flow['expenditure']:,}",
                "week_net_income": week_flow['income'] - week_flow['expenditure'],
                "today_net_income": today_flow['income'] - today_flow['expenditure'],
                "lottery_net_profit": lottery_net_profit,
                "lottery_win_rate": f"{lottery_win_rate:.1f}",
                "avg_lottery_multiplier": f"{avg_lottery_multiplier:.2f}",
                "best_fortune": best_fortune,
                "update_time": now.strftime('%Y-%m-%d %H:%M:%S')
            }

            template = jinja2.Template(PERSONAL_STATS_HTML_TEMPLATE)
            final_html = template.render(render_data)
            image_path = await self.render_html_locally(final_html)
            yield event.image_result(image_path)

        except Exception as e:
            logger.error(f"生成个人数据失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 生成个人数据时发生错误，请检查后台日志。")

    async def _display_luck_ranking(self, event: AstrMessageEvent, order: str, title: str, emoji: str):
        try:
            economy_api = shared_services.get("economy_api")
            if not economy_api:
                yield event.plain_result("❌ 错误：EconomyAPI 未加载。"); return

            ranking_data = await economy_api._db.get_lottery_luck_ranking(limit=10, order=order)

            if not ranking_data:
                yield event.plain_result(f"{title}\n--------------------\n暂无足够数据（需要至少有玩家抽奖3次以上）。")
                return

            nickname_api = shared_services.get("nickname_api")
            custom_nicknames = {}
            if nickname_api:
                user_ids = [row['user_id'] for row in ranking_data]
                custom_nicknames = await nickname_api.get_nicknames_batch(user_ids)
            
            entries = [f"{title}\n(仅统计抽奖超过3次的用户)\n--------------------"]
            for i, row in enumerate(ranking_data, 1):
                user_id = row['user_id']
                display_name = custom_nicknames.get(user_id) or row['nickname'] or user_id
                avg_mult = row['avg_mult']
                play_count = row['play_count']
                display_name_short = (display_name[:10] + '...') if len(display_name) > 12 else display_name
                entries.append(f"{emoji} 第 {i} 名: {display_name_short}  均倍: {avg_mult:.2f}x | 次数: {play_count}")

            yield event.plain_result("\n".join(entries))

        except Exception as e:
            logger.error(f"生成运气排行榜失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 生成排行榜时发生错误，请检查后台日志。")


