# plugins/shop_plugin/shop_database.py (异步化改造后)

import aiosqlite
import os
import asyncio
from typing import List, Dict, Optional
from astrbot.api import logger
import datetime


class ShopDatabase:
    """负责管理商店所有数据，包括商品定义和用户库存。"""

    def __init__(self, plugin_dir: str):
        # 路径构造方式保持不变
        db_dir = os.path.join(
            os.path.dirname(os.path.dirname(plugin_dir)), "plugins_db"
        )
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)
        self.db_path = os.path.join(db_dir, "shop_data.db")
        self.conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self):
        """确保数据库已连接，如果未连接则建立连接。"""
        if self.conn is None:
            async with self._lock:
                if self.conn is None:
                    logger.info("商店数据库未连接，正在建立新连接...")
                    self.conn = await aiosqlite.connect(self.db_path)
                    self.conn.row_factory = aiosqlite.Row
                    await self._init_db()
                    logger.info("商店数据库连接成功并完成初始化。")

    async def _check_and_add_columns(self):
        """检查并为旧版数据库的 items 表添加新列。"""
        async with self.conn.execute("PRAGMA table_info(items)") as cursor:
            existing_columns = [row[1] for row in await cursor.fetchall()]

        if "daily_limit" not in existing_columns:
            await self.conn.execute(
                "ALTER TABLE items ADD COLUMN daily_limit INTEGER NOT NULL DEFAULT 0"
            )
            logger.info(
                "INFO: shop_plugin > 已成功为 items 表添加 'daily_limit' 字段。"
            )
            await self.conn.commit()

    async def _init_db(self):
        """异步初始化数据库表结构。"""
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS items (
            item_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            price INTEGER NOT NULL,
            owner_plugin TEXT NOT NULL,
            daily_limit INTEGER NOT NULL DEFAULT 0
        )""")
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS user_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY(item_id) REFERENCES items(item_id),
            UNIQUE(user_id, item_id)
        )""")
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS purchase_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            purchase_date TEXT NOT NULL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

        await self._check_and_add_columns()  # 在初始化时调用升级检查

        await self.conn.commit()

    # --- 将所有数据操作方法改造为异步 ---

    async def add_or_update_item_definition(
        self,
        item_id: str,
        name: str,
        description: str,
        price: int,
        owner_plugin: str,
        daily_limit: int = 0,
    ):
        await self._ensure_connected()
        await self.conn.execute(
            "INSERT OR REPLACE INTO items (item_id, name, description, price, owner_plugin, daily_limit) VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, name, description, price, owner_plugin, daily_limit),
        )
        await self.conn.commit()

    async def log_purchase(self, user_id: str, item_id: str, quantity: int):
        await self._ensure_connected()
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        await self.conn.execute(
            "INSERT INTO purchase_history (user_id, item_id, quantity, purchase_date) VALUES (?, ?, ?, ?)",
            (user_id, item_id, quantity, today_str),
        )
        await self.conn.commit()

    async def get_today_purchase_count(self, user_id: str, item_id: str) -> int:
        await self._ensure_connected()
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        query = "SELECT SUM(quantity) as total FROM purchase_history WHERE user_id = ? AND item_id = ? AND purchase_date = ?"
        async with self.conn.execute(query, (user_id, item_id, today_str)) as cursor:
            result = await cursor.fetchone()
            return result["total"] if result and result["total"] else 0

    async def get_all_items(self) -> List[Dict]:
        await self._ensure_connected()
        # 在查询语句中加入 daily_limit 字段
        query = "SELECT item_id, name, description, price, daily_limit FROM items ORDER BY price"
        async with self.conn.execute(query) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def remove_item_definition(self, item_id: str) -> str:
        """
        从商店定义中移除一个商品，返回操作结果状态。
        'success': 成功删除
        'in_use': 因有用户持有而无法删除
        'not_found': 商品本身不存在
        """
        await self._ensure_connected()
        async with self._lock:  # 使用锁确保检查和删除操作的原子性
            # 1. 检查是否仍有用户持有该物品
            async with self.conn.execute(
                "SELECT 1 FROM user_inventory WHERE item_id = ? LIMIT 1", (item_id,)
            ) as cursor:
                is_owned = await cursor.fetchone()
                if is_owned:
                    logger.warning(
                        f"无法下架物品 {item_id}，因为它仍存在于用户背包中。"
                    )
                    return "in_use"

            # 2. 如果无人持有，则执行删除
            cursor = await self.conn.execute(
                "DELETE FROM items WHERE item_id = ?", (item_id,)
            )
            await self.conn.commit()

            # 3. 根据影响的行数判断是否真的删除了
            if cursor.rowcount > 0:
                return "success"
            else:
                return "not_found"

    async def get_item_by_id(self, item_id: str) -> Optional[Dict]:
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT * FROM items WHERE item_id = ?", (item_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_item_by_name(self, name: str) -> Optional[Dict]:
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT * FROM items WHERE name = ?", (name,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_user_inventory(self, user_id: str) -> List[Dict]:
        await self._ensure_connected()
        async with self.conn.execute(
            """
            SELECT i.item_id, i.name, i.description, inv.quantity
            FROM user_inventory inv
            JOIN items i ON inv.item_id = i.item_id
            WHERE inv.user_id = ?
        """,
            (user_id,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def add_item_to_user(self, user_id: str, item_id: str, quantity: int = 1):
        await self._ensure_connected()
        await self.conn.execute(
            "INSERT INTO user_inventory (user_id, item_id, quantity) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, item_id) DO UPDATE SET quantity = quantity + ?",
            (user_id, item_id, quantity, quantity),
        )
        await self.conn.commit()

    async def remove_item_from_user(
        self, user_id: str, item_id: str, quantity: int = 1
    ) -> bool:
        """从用户库存中移除指定数量的物品，如果数量足够则返回True。"""
        await self._ensure_connected()
        # 使用事务确保操作的原子性
        async with self.conn.execute("BEGIN"):
            async with self.conn.execute(
                "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = ?",
                (user_id, item_id),
            ) as cursor:
                result = await cursor.fetchone()

            if result is None or result["quantity"] < quantity:
                return False  # 物品不存在或数量不足

            new_quantity = result["quantity"] - quantity
            if new_quantity > 0:
                await self.conn.execute(
                    "UPDATE user_inventory SET quantity = ? WHERE user_id = ? AND item_id = ?",
                    (new_quantity, user_id, item_id),
                )
            else:
                await self.conn.execute(
                    "DELETE FROM user_inventory WHERE user_id = ? AND item_id = ?",
                    (user_id, item_id),
                )
        await self.conn.commit()
        return True
