import asyncio
import os
from typing import Any

import aiosqlite

from astrbot.api import logger


class SignDatabase:
    def __init__(self, plugin_dir: str):
        db_dir = os.path.join(os.path.dirname(os.path.dirname(plugin_dir)), "plugins_db")
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)
        self.db_path = os.path.join(db_dir, "astrbot_plugin_sign.db")
        self.conn = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self):
        if self.conn is None:
            async with self._lock:
                if self.conn is None:
                    logger.info("数据库未连接，正在尝试建立新连接...")
                    try:
                        self.conn = await aiosqlite.connect(self.db_path)
                        self.conn.row_factory = aiosqlite.Row
                        await self._init_db()
                        logger.info("数据库连接成功并完成初始化。")
                    except Exception as e:
                        logger.error(f"数据库连接失败: {e}", exc_info=True)
                        self.conn = None
                        raise e

    async def _check_and_add_columns(self):
        """检查并为旧版数据库添加新列，以实现平滑升级。"""
        async with self.conn.execute("PRAGMA table_info(sign_data)") as cursor:
            existing_columns = [row[1] for row in await cursor.fetchall()]

        columns_to_add = {
            "lottery_count": "INTEGER DEFAULT 0",
            "last_lottery_date": "TEXT DEFAULT ''",
            "nickname": "TEXT DEFAULT ''",
            "lucky_clover_buff_date": "TEXT DEFAULT ''",
            "extra_lottery_attempts": "INTEGER DEFAULT 0",
            "last_relief_fund_date": "TEXT DEFAULT ''",
            "luck_change_card_uses_today": "INTEGER DEFAULT 0",
            "last_luck_change_card_use_date": "TEXT DEFAULT ''",
            "holy_light_uses_today": "INTEGER DEFAULT 0"
        }

        for col, col_type in columns_to_add.items():
            if col not in existing_columns:
                await self.conn.execute(f"ALTER TABLE sign_data ADD COLUMN {col} {col_type}")
                logger.info(f"数据库升级：为 'sign_data' 表添加了 '{col}' 列。")

        await self.conn.commit()

    # --- 核心修改点: 调整此函数的位置 ---
    # 将函数定义移动到 _init_db 调用它之前
    async def _check_and_add_columns_for_lottery(self):
        """检查并为 lottery_history 表添加新列"""
        async with self.conn.execute("PRAGMA table_info(lottery_history)") as cursor:
            existing_columns = [row[1] for row in await cursor.fetchall()]

        if "fortune_at_time" not in existing_columns:
            await self.conn.execute("ALTER TABLE lottery_history ADD COLUMN fortune_at_time TEXT DEFAULT ''")
            logger.info("数据库升级：为 'lottery_history' 表添加了 'fortune_at_time' 列。")

        await self.conn.commit()

    async def _init_db(self):
        """初始化数据库连接和表结构"""
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS sign_data (
            user_id TEXT PRIMARY KEY,
            total_days INTEGER DEFAULT 0,
            last_sign TEXT DEFAULT '',
            continuous_days INTEGER DEFAULT 0,
            coins INTEGER DEFAULT 0,
            total_coins_gift INTEGER DEFAULT 0,
            last_fortune_result TEXT DEFAULT '',
            last_fortune_value INTEGER DEFAULT 0,
            lottery_count INTEGER DEFAULT 0,
            last_lottery_date TEXT DEFAULT '',
            nickname TEXT DEFAULT '',
            lucky_clover_buff_date TEXT DEFAULT '',
            extra_lottery_attempts INTEGER DEFAULT 0,
            last_relief_fund_date TEXT DEFAULT '',
            luck_change_card_uses_today INTEGER DEFAULT 0,
            last_luck_change_card_use_date TEXT DEFAULT '',
            holy_light_uses_today INTEGER DEFAULT 0
        )""")

        await self._check_and_add_columns()

        tables = [
             """CREATE TABLE IF NOT EXISTS coins_history (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, amount INTEGER,
                 reason TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP
             )""",
             """CREATE TABLE IF NOT EXISTS fortune_history (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, result TEXT,
                 value INTEGER, timestamp TEXT DEFAULT CURRENT_TIMESTAMP
             )""",
             """CREATE TABLE IF NOT EXISTS jackpot_wins (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, nickname TEXT,
                 amount INTEGER NOT NULL, timestamp TEXT DEFAULT (datetime('now', '+8 hours'))
             )""",
             """CREATE TABLE IF NOT EXISTS transfer_history (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT NOT NULL, sender_name TEXT,
                 recipient_id TEXT NOT NULL, recipient_name TEXT, amount INTEGER NOT NULL,
                 timestamp TEXT DEFAULT (datetime('now', '+8 hours'))
             )""",
             """CREATE TABLE IF NOT EXISTS lottery_history (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, bet_amount INTEGER NOT NULL,
                   prize_won INTEGER NOT NULL, multiplier REAL, is_jackpot INTEGER DEFAULT 0,
                   fortune_at_time TEXT DEFAULT '',
                   timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                 )"""
        ]

        for table in tables:
            await self.conn.execute(table)

        await self.conn.execute("""CREATE TABLE IF NOT EXISTS plugin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")

        # 现在调用时，函数已经被定义了
        await self._check_and_add_columns_for_lottery()
        await self.conn.commit()

    # --- 后续所有函数保持不变 ---

    async def log_transfer(self, sender_id: str, sender_name: str, recipient_id: str, recipient_name: str, amount: int):
        """记录一笔转账流水。"""
        await self._ensure_connected()
        await self.conn.execute(
            "INSERT INTO transfer_history (sender_id, sender_name, recipient_id, recipient_name, amount) VALUES (?, ?, ?, ?, ?)",
            (sender_id, sender_name, recipient_id, recipient_name, amount)
        )
        await self.conn.commit()

    async def get_transfer_history(self, user_id: str, limit: int = 10) -> list[aiosqlite.Row]:
        """获取指定用户最近的转账历史（包括转入和转出）。"""
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT * FROM transfer_history WHERE sender_id = ? OR recipient_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

    async def get_incoming_transfers(self, user_id: str, limit: int = 15) -> list[aiosqlite.Row]:
        """只获取指定用户的收款历史。"""
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT * FROM transfer_history WHERE recipient_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

    async def get_outgoing_transfers(self, user_id: str, limit: int = 15) -> list[aiosqlite.Row]:
        """只获取指定用户的付款历史。"""
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT * FROM transfer_history WHERE sender_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        await self._ensure_connected()
        async with self.conn.execute("SELECT value FROM plugin_settings WHERE key = ?", (key,)) as cursor:
            result = await cursor.fetchone()
            return result["value"] if result else default

    async def set_setting(self, key: str, value: str):
        await self._ensure_connected()
        await self.conn.execute("INSERT OR REPLACE INTO plugin_settings (key, value) VALUES (?, ?)", (key, value))
        await self.conn.commit()

    async def get_user_data(self, user_id: str) -> dict[str, Any] | None:
        await self._ensure_connected()
        async with self.conn.execute("SELECT * FROM sign_data WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_user_data(self, user_id: str, **kwargs):
        await self._ensure_connected()
        if not await self.get_user_data(user_id):
            await self.conn.execute("INSERT OR IGNORE INTO sign_data (user_id) VALUES (?)", (user_id,))

        update_fields = []
        values = []
        for key, value in kwargs.items():
            update_fields.append(f"{key} = ?")
            values.append(value)
        values.append(user_id)

        sql = f"UPDATE sign_data SET {', '.join(update_fields)} WHERE user_id = ?"
        await self.conn.execute(sql, values)
        await self.conn.commit()

    async def log_coins(self, user_id: str, amount: int, reason: str):
        await self._ensure_connected()
        await self.conn.execute(
            "INSERT INTO coins_history (user_id, amount, reason) VALUES (?, ?, ?)",
            (user_id, amount, reason)
        )
        await self.conn.commit()

    async def log_fortune(self, user_id: str, result: str, value: int):
        """记录一次运势结果。"""
        await self._ensure_connected()
        await self.conn.execute(
            "INSERT INTO fortune_history (user_id, result, value) VALUES (?, ?, ?)",
            (user_id, result, value)
        )
        await self.conn.commit()

    async def log_jackpot_win(self, user_id: str, nickname: str, amount: int):
        """记录一次奖池大奖的获胜者信息"""
        await self._ensure_connected()
        await self.conn.execute(
            "INSERT INTO jackpot_wins (user_id, nickname, amount) VALUES (?, ?, ?)",
            (user_id, nickname, amount)
        )
        await self.conn.commit()

    async def get_coin_history(self, user_id: str, limit: int = 5) -> list[aiosqlite.Row]:
        """获取指定用户最近的金币变动历史"""
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT amount, reason, timestamp FROM coins_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

    async def get_lottery_history(self, user_id: str, limit: int = 15) -> list[aiosqlite.Row]:
        """获取指定用户最近的抽奖记录。"""
        await self._ensure_connected()
        # 注意：这里的查询需要包含 fortune_at_time 才能在 main.py 中使用
        query = """
            SELECT timestamp, bet_amount, prize_won, multiplier, is_jackpot, fortune_at_time
            FROM lottery_history
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        async with self.conn.execute(query, (user_id, limit)) as cursor:
            return await cursor.fetchall()

    async def get_fortune_history(self, user_id: str, limit: int = 5) -> list[aiosqlite.Row]:
        """获取指定用户最近的运势历史"""
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT result, value, timestamp FROM fortune_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

    async def get_jackpot_wins(self, limit: int = 5) -> list[aiosqlite.Row]:
        """获取最近的奖池获奖记录"""
        await self._ensure_connected()
        async with self.conn.execute("SELECT nickname, amount, timestamp FROM jackpot_wins ORDER BY timestamp DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()

    async def get_ranking(self, limit: int = 10) -> list[tuple[str, int, int]]:
        await self._ensure_connected()
        async with self.conn.execute("""
            SELECT user_id, nickname, coins, total_days FROM sign_data
            ORDER BY coins DESC, total_days DESC LIMIT ?
        """, (limit,)) as cursor:
            return await cursor.fetchall()

    async def get_total_coin_supply(self) -> int:
        """获取当前全市场金币总量"""
        await self._ensure_connected()
        async with self.conn.execute("SELECT SUM(coins) as total FROM sign_data") as cursor:
            result = await cursor.fetchone()
            return result["total"] if result and result["total"] else 0

    async def get_coin_flow_summary(self, start_time: str, end_time: str) -> dict:
        """
        获取指定时间段内的金币产出与回收摘要.
        返回: {'source': float, 'sink': float}
        """
        await self._ensure_connected()
        summary = {"source": 0, "sink": 0}

        source_query = """
            SELECT SUM(amount) as total FROM coins_history 
            WHERE amount > 0 
            AND reason NOT LIKE '%收到来自%的转账%' 
            AND DATETIME(timestamp, 'localtime') BETWEEN ? AND ?
        """
        sink_query = """
            SELECT SUM(amount) as total FROM coins_history 
            WHERE amount < 0 
            AND reason NOT LIKE '%转账给用户%' 
            AND DATETIME(timestamp, 'localtime') BETWEEN ? AND ?
        """

        async with self.conn.execute(source_query, (start_time, end_time)) as cursor:
            result = await cursor.fetchone()
            summary["source"] = result["total"] if result and result["total"] else 0

        async with self.conn.execute(sink_query, (start_time, end_time)) as cursor:
            result = await cursor.fetchone()
            summary["sink"] = abs(result["total"]) if result and result["total"] else 0

        return summary
    async def get_active_user_count_on_date(self, date_str: str) -> int:
        """获取指定日期签到了多少人"""
        await self._ensure_connected()
        query = "SELECT COUNT(user_id) as count FROM sign_data WHERE last_sign = ?"
        async with self.conn.execute(query, (date_str,)) as cursor:
            result = await cursor.fetchone()
            return result["count"] if result else 0

    async def get_signin_rewards_on_date(self, date_str: str) -> int:
        """获取指定日期的总签到产出（基础+连续）"""
        await self._ensure_connected()
        query = """
            SELECT SUM(amount) as total FROM coins_history
            WHERE amount > 0
            AND (reason = '基础签到' OR reason LIKE '连续%天签到奖励')
            AND DATE(timestamp) = ?
        """
        async with self.conn.execute(query, (date_str,)) as cursor:
            result = await cursor.fetchone()
            return result["total"] if result and result["total"] else 0

    async def get_total_activity_rewards_on_date(self, date_str: str) -> int:
        """获取指定日期的总活动产出（排除用户间转账的所有正向金币变动）"""
        await self._ensure_connected()
        query = """
            SELECT SUM(amount) as total FROM coins_history
            WHERE amount > 0
            AND reason NOT LIKE '%收到来自%的转账%'
            AND DATE(timestamp, 'localtime') = ?
        """
        async with self.conn.execute(query, (date_str,)) as cursor:
            result = await cursor.fetchone()
            return result["total"] if result and result["total"] else 0

    async def get_net_change_between(self, start_time_iso: str, end_time_iso: str) -> int:
        """获取两个ISO格式时间字符串之间的金币净变动（产出 - 回收）"""
        await self._ensure_connected()
        query = "SELECT SUM(amount) as total FROM coins_history WHERE timestamp BETWEEN ? AND ?"
        async with self.conn.execute(query, (start_time_iso, end_time_iso)) as cursor:
            result = await cursor.fetchone()
            return result["total"] if result and result["total"] else 0

    async def get_personal_flow_summary(self, user_id: str, days: int = 7) -> dict:
        """获取单个用户在指定天数内的总收入和总支出（包含转账）"""
        await self._ensure_connected()
        summary = {"income": 0, "expenditure": 0}

        if days == 1:
            time_clause = "DATE(timestamp, 'localtime') = DATE('now', 'localtime')"
            days_param = ()
        else:
            time_clause = "DATETIME(timestamp, 'localtime') >= DATETIME('now', ?, 'localtime')"
            days_param = (f"-{days-1} days",)

        income_query = f"""
            SELECT SUM(amount) as total FROM coins_history
            WHERE user_id = ? AND amount > 0 AND {time_clause}
        """
        expenditure_query = f"""
            SELECT SUM(amount) as total FROM coins_history
            WHERE user_id = ? AND amount < 0 AND {time_clause}
        """

        async with self.conn.execute(income_query, (user_id, *days_param)) as cursor:
            result = await cursor.fetchone()
            summary["income"] = result["total"] if result and result["total"] else 0

        async with self.conn.execute(expenditure_query, (user_id, *days_param)) as cursor:
            result = await cursor.fetchone()
            summary["expenditure"] = abs(result["total"]) if result and result["total"] else 0

        return summary

    async def get_personal_lottery_history(self, user_id: str, days: int = 7) -> list[aiosqlite.Row]:
        """获取单个用户指定天数内所有抽奖相关的记录"""
        await self._ensure_connected()
        time_clause = "DATETIME(timestamp, 'localtime') >= DATETIME('now', ?, 'localtime')"
        days_param = f"-{days-1} days"

        query = f"SELECT reason, amount FROM coins_history WHERE user_id = ? AND reason LIKE '抽奖%' AND {time_clause}"
        async with self.conn.execute(query, (user_id, days_param)) as cursor:
            return await cursor.fetchall()

    async def get_personal_fortune_summary(self, user_id: str, days: int = 7) -> str | None:
        """获取单个用户在指定天数内出现次数最多的运势"""
        await self._ensure_connected()
        time_clause = "DATETIME(timestamp, 'localtime') >= DATETIME('now', ?, 'localtime')"
        days_param = f"-{days-1} days"

        query = f"""
            SELECT result, COUNT(result) as count FROM fortune_history
            WHERE user_id = ? AND {time_clause}
            GROUP BY result ORDER BY count DESC LIMIT 1
        """
        async with self.conn.execute(query, (user_id, days_param)) as cursor:
            result = await cursor.fetchone()
            return result["result"] if result else "无记录"

    async def log_lottery_play(self, user_id: str, bet: int, prize: int, multiplier: float, jackpot: bool, fortune: str):
        """记录一次抽奖，包含当时的运势"""
        await self._ensure_connected()
        await self.conn.execute(
            "INSERT INTO lottery_history (user_id, bet_amount, prize_won, multiplier, is_jackpot, fortune_at_time) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, bet, prize, multiplier, 1 if jackpot else 0, fortune)
        )
        await self.conn.commit()

    async def get_personal_lottery_stats(self, user_id: str, days: int = 7) -> dict:
        """从新的lottery_history表中获取精确的个人抽奖统计数据"""
        await self._ensure_connected()
        time_clause = "DATETIME(timestamp, 'localtime') >= DATE('now', ?, 'localtime')"
        days_param = f"-{days-1} days"

        query = f"""
            SELECT
                COUNT(*) AS total_plays,
                SUM(CASE WHEN prize_won > bet_amount THEN 1 ELSE 0 END) AS profitable_wins,
                SUM(bet_amount) AS total_spent,
                SUM(prize_won) AS total_won,
                AVG(CASE WHEN prize_won > 0 THEN multiplier ELSE NULL END) AS avg_multiplier
            FROM lottery_history
            WHERE user_id = ? AND {time_clause}
        """
        async with self.conn.execute(query, (user_id, days_param)) as cursor:
            stats = await cursor.fetchone()
            if stats and stats["total_plays"] > 0:
                return dict(stats)
            return {
                "total_plays": 0, "profitable_wins": 0, "total_spent": 0,
                "total_won": 0, "avg_multiplier": 0.0
            }

    async def get_lottery_luck_ranking(self, limit: int = 10, order: str = "DESC") -> list[aiosqlite.Row]:
        """获取抽奖运气排行榜"""
        await self._ensure_connected()
        query = f"""
            SELECT
                h.user_id,
                s.nickname,
                AVG(h.multiplier) as avg_mult,
                COUNT(h.id) as play_count
            FROM
                lottery_history h
            LEFT JOIN
                sign_data s ON h.user_id = s.user_id
            GROUP BY
                h.user_id
            HAVING
                play_count >= 3
            ORDER BY
                avg_mult {order}
            LIMIT
                ?
        """
        async with self.conn.execute(query, (limit,)) as cursor:
            return await cursor.fetchall()

    async def process_luck_change_card_usage(self, user_id: str, new_coins: int, cost: int, fortune_result: str, fortune_value: int, new_uses_today: int, today_str: str, reason_for_cost: str, holy_light_uses_today: int):
        """[修正版] 在一个事务中处理转运卡的所有数据库更新。"""
        await self._ensure_connected()
        async with self._lock:  # <-- 在这里添加异步锁
            try:
                await self.conn.execute("BEGIN")
                # 1. 更新用户数据
                await self.conn.execute(
                    """UPDATE sign_data SET
                       coins = ?, last_fortune_result = ?, last_fortune_value = ?,
                       luck_change_card_uses_today = ?, last_luck_change_card_use_date = ?,
                       holy_light_uses_today = ?
                       WHERE user_id = ?""",
                    (new_coins, fortune_result, fortune_value, new_uses_today, today_str, holy_light_uses_today, user_id)
                )
                # 2. 记录金币日志
                if cost > 0:
                    await self.conn.execute(
                        "INSERT INTO coins_history (user_id, amount, reason) VALUES (?, ?, ?)",
                        (user_id, -cost, reason_for_cost)
                    )
                # 3. 记录运势日志
                await self.conn.execute(
                    "INSERT INTO fortune_history (user_id, result, value) VALUES (?, ?, ?)",
                    (user_id, fortune_result, fortune_value)
                )
                await self.conn.commit()
            except Exception as e:
                await self.conn.rollback()
                logger.error(f"转运卡数据库事务执行失败: {e}", exc_info=True)
                raise # 重新抛出异常，让上层知道操作失败

    async def process_lottery_ticket_usage(self, user_id: str, cost: int, current_extra_attempts: int) -> int:
        """[修正版] 在一个事务中处理抽奖券的所有数据库更新。返回更新后的金币余额。"""
        await self._ensure_connected()
        async with self._lock:  # <-- 也在这里添加异步锁
            try:
                await self.conn.execute("BEGIN")
                # 1. 获取当前金币
                cursor = await self.conn.execute("SELECT coins FROM sign_data WHERE user_id = ?", (user_id,))
                row = await cursor.fetchone()
                current_coins = row["coins"] if row else 0

                # 2. 计算新金币并更新用户数据（金币和额外次数）
                new_coins = current_coins - cost
                new_extra_attempts = current_extra_attempts + 1
                await self.conn.execute(
                    "UPDATE sign_data SET coins = ?, extra_lottery_attempts = ? WHERE user_id = ?",
                    (new_coins, new_extra_attempts, user_id)
                )

                # 3. 记录金币日志
                await self.conn.execute(
                    "INSERT INTO coins_history (user_id, amount, reason) VALUES (?, ?, ?)",
                    (user_id, -cost, "使用抽奖券的代价")
                )
                await self.conn.commit()
                return new_coins
            except Exception as e:
                await self.conn.rollback()
                logger.error(f"抽奖券数据库事务执行失败: {e}", exc_info=True)
                raise # 重新抛出异常，让上层知道操作失败    async def close(self):
        """关闭数据库连接"""
        if self.conn:
            await self.conn.close()
            self.conn = None
            logger.info("数据库连接已关闭。")
