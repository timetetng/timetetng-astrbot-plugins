# stock_market/database.py

from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from astrbot.api import logger

from .config import SELL_LOCK_MINUTES
from .models import VirtualStock


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _safe_add_columns(self, db, table_name, columns_to_add: dict[str, str]):
        """安全地为指定表添加多个列。"""
        cursor = await db.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {row[1] for row in await cursor.fetchall()}

        for col_name, col_definition in columns_to_add.items():
            if col_name not in existing_columns:
                logger.info(f"为表 `{table_name}` 添加新列: `{col_name}`")
                await db.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_definition}"
                )

    async def initialize(self):
        """检查并初始化数据库。如果表或列不存在，则创建它们。"""
        logger.info("正在检查并初始化数据库结构...")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY NOT NULL,
                    login_id TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );""")
                await db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login_id ON users (login_id);"
                )

                await db.execute("""
                CREATE TABLE IF NOT EXISTS stocks (
                    stock_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    current_price REAL NOT NULL,
                    volatility REAL NOT NULL DEFAULT 0.05,
                    industry TEXT NOT NULL DEFAULT '综合'
                );""")

                await db.execute("""
                CREATE TABLE IF NOT EXISTS kline_history (
                    stock_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    PRIMARY KEY (stock_id, timestamp),
                    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
                );""")

                await db.execute("""
                CREATE TABLE IF NOT EXISTS holdings (
                    holding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    stock_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    purchase_price REAL NOT NULL,
                    purchase_timestamp TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );""")
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_holdings_user_stock ON holdings (user_id, stock_id);"
                )

                await db.execute(
                    "CREATE TABLE IF NOT EXISTS subscriptions (umo TEXT PRIMARY KEY NOT NULL);"
                )

                await self._safe_add_columns(
                    db,
                    "stocks",
                    {
                        "is_listed_company": "BOOLEAN NOT NULL DEFAULT 0",
                        "owner_id": "TEXT",
                        "total_shares": "INTEGER",
                        "market_pressure": "REAL NOT NULL DEFAULT 0.0",
                        "fundamental_value": "REAL",
                    },
                )

                await db.commit()
            logger.info("数据库初始化完成。")
        except Exception as e:
            logger.error(f"数据库初始化过程中发生严重错误: {e}", exc_info=True)
            raise

    async def load_stocks(self) -> dict[str, VirtualStock]:
        """从数据库加载所有股票信息到内存。"""
        stocks = {}
        async with aiosqlite.connect(self.db_path) as db:
            query = "SELECT stock_id, name, current_price, volatility, industry, is_listed_company, owner_id, total_shares, market_pressure, fundamental_value FROM stocks"
            cursor = await db.execute(query)
            rows = await cursor.fetchall()

            if not rows:
                logger.info("数据库为空，正在插入初始股票数据...")
                initial_data = [
                    ("ZY", "智云科技", 57, 0.022, "科技"),
                    ("HL", "华联医药", 49, 0.0250, "医药"),
                    ("DF", "东方能源", 44, 0.0140, "新能源"),
                    ("JM", "金马物流", 54, 0.0200, "运输"),
                    ("RL", "荣立地产", 45, 0.0300, "房地产"),
                    ("TX", "天讯软件", 26, 0.0450, "软件"),
                ]
                await db.executemany(
                    "INSERT INTO stocks (stock_id, name, current_price, volatility, industry, fundamental_value) VALUES (?, ?, ?, ?, ?, ?)",
                    [(d[0], d[1], d[2], d[3], d[4], d[2]) for d in initial_data],
                )
                await db.commit()
                cursor = await db.execute(query)
                rows = await cursor.fetchall()

            for row in rows:
                (
                    stock_id,
                    name,
                    price,
                    volatility,
                    industry,
                    is_listed,
                    owner_id,
                    total_shares,
                    market_pressure,
                    fundamental_value,
                ) = row
                if fundamental_value is None:
                    fundamental_value = price

                stock = VirtualStock(
                    stock_id=stock_id,
                    name=name,
                    current_price=price,
                    volatility=volatility,
                    industry=industry,
                    fundamental_value=fundamental_value,
                    previous_close=price,
                    is_listed_company=is_listed or False,
                    owner_id=owner_id,
                    total_shares=total_shares or 0,
                    market_pressure=market_pressure or 0.0,
                )

                k_cursor = await db.execute(
                    "SELECT timestamp, open, high, low, close FROM kline_history WHERE stock_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (stock_id, stock.kline_history.maxlen),
                )
                k_rows = await k_cursor.fetchall()
                kline_data = [
                    {
                        "date": r[0],
                        "open": r[1],
                        "high": r[2],
                        "low": r[3],
                        "close": r[4],
                    }
                    for r in reversed(k_rows)
                ]

                stock.kline_history.extend(kline_data)
                stock.price_history.extend([k["close"] for k in kline_data])
                if not stock.price_history:
                    stock.price_history.append(price)
                stock.daily_close_history.extend(
                    list(stock.price_history)[-stock.daily_close_history.maxlen :]
                )

                stocks[stock_id] = stock

        logger.info(f"成功从数据库加载 {len(stocks)} 支股票。")
        return stocks

    async def load_subscriptions(self) -> set:
        """从数据库加载所有订阅者到内存。"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT umo FROM subscriptions")
                rows = await cursor.fetchall()
                subscribers = {row[0] for row in rows}
                logger.info(f"成功从数据库加载 {len(subscribers)} 个订阅者。")
                return subscribers
        except Exception as e:
            logger.error(f"从数据库加载订阅者列表失败: {e}", exc_info=True)
            return set()

    async def batch_update_stock_data(self, updates: list[dict[str, Any]]):
        """批量更新股票价格、压力和K线数据。"""
        if not updates:
            return
        async with aiosqlite.connect(self.db_path) as db:
            for data in updates:
                await db.execute(
                    "UPDATE stocks SET current_price = ?, market_pressure = ? WHERE stock_id = ?",
                    (data["current_price"], data["market_pressure"], data["stock_id"]),
                )
                k = data["kline"]
                await db.execute(
                    "INSERT INTO kline_history (stock_id, timestamp, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(stock_id, timestamp) DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close",
                    (
                        data["stock_id"],
                        k["date"],
                        k["open"],
                        k["high"],
                        k["low"],
                        k["close"],
                    ),
                )
            await db.commit()

    async def get_user_holdings(self, user_id: str) -> list[tuple[str, int]]:
        """获取指定用户的所有持仓。"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT stock_id, SUM(quantity) FROM holdings WHERE user_id = ? GROUP BY stock_id",
                (user_id,),
            )
            return await cursor.fetchall()

    async def get_all_user_ids_with_holdings(self) -> set:
        """获取所有持有股票的用户ID集合。"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT DISTINCT user_id FROM holdings")
            return {row[0] for row in await cursor.fetchall()}

    async def get_user_holdings_aggregated(self, user_id: str) -> dict:
        """获取并聚合指定用户的持仓数据。"""
        aggregated_holdings = {}
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT stock_id, quantity, purchase_price FROM holdings WHERE user_id=?",
                (user_id,),
            )
            raw_holdings = await cursor.fetchall()

        for stock_id, qty, price in raw_holdings:
            if stock_id not in aggregated_holdings:
                aggregated_holdings[stock_id] = {"quantity": 0, "cost_basis": 0}
            aggregated_holdings[stock_id]["quantity"] += qty
            aggregated_holdings[stock_id]["cost_basis"] += qty * price

        return aggregated_holdings

    async def get_user_by_qq_id(self, qq_user_id: str) -> bool:
        """根据QQ号检查用户是否存在"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (qq_user_id,)
            )
            return await cursor.fetchone() is not None

    async def register_web_user(
        self, login_id: str, password_hash: str, qq_user_id: str, timestamp: str
    ):
        """注册一个新的Web用户并绑定QQ"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO users (login_id, password_hash, user_id, created_at) VALUES (?, ?, ?, ?)",
                (login_id, password_hash, qq_user_id, timestamp),
            )
            await db.commit()

    async def get_user_by_login_id(self, login_id: str) -> dict | None:
        """根据登录ID查找用户记录。"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT user_id, login_id, password_hash FROM users WHERE login_id = ?",
                (login_id,),
            )
            record = await cursor.fetchone()
            return dict(record) if record else None

    async def update_user_password(self, login_id: str, new_password_hash: str) -> None:
        """更新指定用户的密码。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET password_hash = ? WHERE login_id = ?",
                (new_password_hash, login_id),
            )
            await db.commit()

    async def add_holding(
        self, user_id: str, stock_id: str, quantity: int, purchase_price: float
    ):
        """新增一笔持仓记录。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO holdings (user_id, stock_id, quantity, purchase_price, purchase_timestamp) VALUES (?, ?, ?, ?, ?)",
                (
                    user_id,
                    stock_id,
                    quantity,
                    purchase_price,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

    async def get_sellable_quantity(self, user_id: str, stock_id: str) -> int:
        """获取指定股票的可卖出总量。"""
        unlock_time_str = (
            datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)
        ).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT SUM(quantity) FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp <= ?",
                (user_id, stock_id, unlock_time_str),
            )
            result = await cursor.fetchone()
            return result[0] if result and result[0] else 0

    async def get_next_unlock_time_str(self, user_id: str, stock_id: str) -> str | None:
        """获取下一批持仓的解锁时间提示。"""
        unlock_time_str = (
            datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)
        ).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT MIN(purchase_timestamp) FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp > ?",
                (user_id, stock_id, unlock_time_str),
            )
            next_purchase = await cursor.fetchone()
            if next_purchase and next_purchase[0]:
                unlock_dt = datetime.fromisoformat(next_purchase[0]) + timedelta(
                    minutes=SELL_LOCK_MINUTES
                )
                time_left = unlock_dt - datetime.now()
                if time_left.total_seconds() > 0:
                    minutes, seconds = divmod(int(time_left.total_seconds()), 60)
                    return f"\n提示：下一批持仓大约在 {minutes}分{seconds}秒 后解锁。"
        return None

    async def execute_fifo_sell(
        self, user_id: str, stock_id: str, quantity_to_sell: int
    ) -> float:
        """
        按先进先出(FIFO)原则执行卖出操作，并返回卖出部分的总成本。
        """
        unlock_time = (
            datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)
        ).isoformat()
        total_cost_basis = 0
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT holding_id, quantity, purchase_price FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp <= ? ORDER BY purchase_timestamp ASC",
                (user_id, stock_id, unlock_time),
            )
            sellable_holdings = await cursor.fetchall()

            remaining_to_sell = quantity_to_sell
            for holding_id, qty, price in sellable_holdings:
                if remaining_to_sell <= 0:
                    break

                sell_from_this_holding = min(remaining_to_sell, qty)
                total_cost_basis += sell_from_this_holding * price

                if sell_from_this_holding == qty:
                    await db.execute(
                        "DELETE FROM holdings WHERE holding_id=?", (holding_id,)
                    )
                else:
                    new_qty = qty - sell_from_this_holding
                    await db.execute(
                        "UPDATE holdings SET quantity=? WHERE holding_id=?",
                        (new_qty, holding_id),
                    )

                remaining_to_sell -= sell_from_this_holding
            await db.commit()
        return total_cost_basis

    async def get_sellable_portfolio(self, user_id: str) -> list[tuple[str, int]]:
        """获取用户所有可卖出的持仓（汇总后）。"""
        unlock_time_str = (
            datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)
        ).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT stock_id, SUM(quantity) FROM holdings WHERE user_id=? AND purchase_timestamp <= ? GROUP BY stock_id",
                (user_id, unlock_time_str),
            )
            return await cursor.fetchall()

    async def add_stock(
        self,
        stock_id: str,
        name: str,
        initial_price: float,
        volatility: float,
        industry: str,
    ):
        """[DB] 添加一支新股票。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO stocks (stock_id, name, current_price, volatility, industry, fundamental_value) VALUES (?, ?, ?, ?, ?, ?)",
                (stock_id, name, initial_price, volatility, industry, initial_price),
            )
            await db.commit()

    async def delete_stock(self, stock_id: str):
        """[DB] 删除一支股票及其所有關聯數據。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM stocks WHERE stock_id = ?", (stock_id,))
            await db.commit()

    async def update_stock_name(self, stock_id: str, new_name: str):
        """[DB] 更新股票名稱。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE stocks SET name = ? WHERE stock_id = ?", (new_name, stock_id)
            )
            await db.commit()

    async def update_stock_id(self, old_stock_id: str, new_stock_id: str):
        """[DB] 更新股票代碼 (這是一個複雜操作，需要事務)。"""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("PRAGMA foreign_keys = OFF")
                await db.execute("BEGIN TRANSACTION")

                await db.execute(
                    "UPDATE stocks SET stock_id = ? WHERE stock_id = ?",
                    (new_stock_id, old_stock_id),
                )
                await db.execute(
                    "UPDATE holdings SET stock_id = ? WHERE stock_id = ?",
                    (new_stock_id, old_stock_id),
                )
                await db.execute(
                    "UPDATE kline_history SET stock_id = ? WHERE stock_id = ?",
                    (new_stock_id, old_stock_id),
                )

                await db.execute("COMMIT")
            except Exception as e:
                await db.execute("ROLLBACK")
                raise e
            finally:
                await db.execute("PRAGMA foreign_keys = ON")

    async def update_stock_industry(self, stock_id: str, new_industry: str):
        """[DB] 更新股票行業。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE stocks SET industry = ? WHERE stock_id = ?",
                (new_industry, stock_id),
            )
            await db.commit()

    async def update_stock_volatility(self, stock_id: str, new_volatility: float):
        """[DB] 更新股票波動率。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE stocks SET volatility = ? WHERE stock_id = ?",
                (new_volatility, stock_id),
            )
            await db.commit()

    async def update_stock_price(self, stock_id: str, new_price: float):
        """[DB] 更新指定股票的当前价格。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE stocks SET current_price = ? WHERE stock_id = ?",
                (new_price, stock_id),
            )
            await db.commit()

    async def get_all_stocks_with_details(self) -> list:
        """[DB] 从数据库查询所有股票的详细信息，用于管理员指令。"""
        query = """
            SELECT
                s.stock_id, s.name,
                (SELECT open FROM kline_history WHERE stock_id = s.stock_id ORDER BY timestamp ASC LIMIT 1) AS initial_price,
                s.current_price, s.volatility, s.industry
            FROM stocks s
            ORDER BY s.stock_id ASC
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            # 将 aiosqlite.Row 对象转换为普通字典列表，方便处理
            return [dict(row) for row in rows]

    async def add_subscriber(self, umo: str):
        """[DB] 添加一个新的订阅者。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO subscriptions (umo) VALUES (?)", (umo,))
            await db.commit()

    async def remove_subscriber(self, umo: str):
        """[DB] 移除一个订阅者。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM subscriptions WHERE umo = ?", (umo,))
            await db.commit()
