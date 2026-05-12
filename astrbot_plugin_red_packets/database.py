# database.py
import aiosqlite
import os
import asyncio
import json
from typing import List, Dict, Any, Optional
from datetime import datetime


class RedPacketDatabase:
    """负责管理红包插件的所有数据，包括活动红包和历史记录。"""

    def __init__(self, plugin_dir: str):
        db_dir = os.path.join(
            os.path.dirname(os.path.dirname(plugin_dir)), "plugins_db"
        )
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)
        self.db_path = os.path.join(db_dir, "redpacket_data.db")
        self.conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self):
        if self.conn is None or self.conn._connection is None:
            async with self._lock:
                if self.conn is None or self.conn._connection is None:
                    self.conn = await aiosqlite.connect(self.db_path)
                    self.conn.row_factory = aiosqlite.Row
                    await self._init_db()

    async def _init_db(self):
        """异步初始化数据库表结构。"""
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS active_packets (
            packet_id TEXT PRIMARY KEY,
            packet_type TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            group_id TEXT NOT NULL,
            unified_msg_origin TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            total_amount INTEGER NOT NULL,
            remaining_packets INTEGER NOT NULL,
            greeting TEXT,
            password TEXT,
            amounts_json TEXT,
            claimed_by_json TEXT,
            amount_per_packet INTEGER
        )""")

        # --- 核心修改：在 history 表中增加 sender_name 字段 ---
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS packet_history (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            packet_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            related_user_id TEXT,
            sender_name TEXT, 
            amount INTEGER NOT NULL,
            fee INTEGER DEFAULT 0,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

        async with self.conn.execute("PRAGMA table_info(active_packets)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if "unified_msg_origin" not in columns:
                await self.conn.execute(
                    "ALTER TABLE active_packets ADD COLUMN unified_msg_origin TEXT"
                )
            if "amount_per_packet" not in columns:
                await self.conn.execute(
                    "ALTER TABLE active_packets ADD COLUMN amount_per_packet INTEGER"
                )

        # 检查并为 history 表添加新列
        async with self.conn.execute("PRAGMA table_info(packet_history)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if "sender_name" not in columns:
                await self.conn.execute(
                    "ALTER TABLE packet_history ADD COLUMN sender_name TEXT"
                )

        await self.conn.commit()

    async def close(self):
        async with self._lock:
            if self.conn:
                await self.conn.close()
                self.conn = None

    async def add_active_packet(self, packet_data: Dict[str, Any]):
        await self._ensure_connected()
        await self.conn.execute(
            """INSERT INTO active_packets (packet_id, packet_type, sender_id, sender_name, group_id, unified_msg_origin, created_at, expires_at,
                                         total_amount, remaining_packets, greeting, password, amounts_json, claimed_by_json, amount_per_packet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                packet_data["packet_id"],
                packet_data["packet_type"],
                packet_data["sender_id"],
                packet_data["sender_name"],
                packet_data["group_id"],
                packet_data["unified_msg_origin"],
                packet_data["created_at"],
                packet_data["expires_at"],
                packet_data["total_amount"],
                packet_data["remaining_packets"],
                packet_data["greeting"],
                packet_data.get("password"),
                json.dumps(packet_data.get("amounts_list")),
                json.dumps(packet_data.get("claimed_by", {})),
                packet_data.get("amount_per_packet"),
            ),
        )
        await self.conn.commit()

    # --- 核心修改：log_transaction 签名和逻辑更新 ---
    async def log_transaction(
        self,
        user_id: str,
        packet_id: str,
        action_type: str,
        amount: int,
        related_user_id: Optional[str] = None,
        fee: int = 0,
        sender_name: Optional[str] = None,
    ):
        await self._ensure_connected()
        await self.conn.execute(
            """INSERT INTO packet_history (user_id, packet_id, action_type, amount, related_user_id, fee, sender_name) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                packet_id,
                action_type,
                amount,
                related_user_id,
                fee,
                sender_name,
            ),
        )
        await self.conn.commit()

    # --- 核心修改：get_user_history 查询逻辑重写，不再需要JOIN ---
    async def get_user_history(self, user_id: str) -> Dict[str, Any]:
        await self._ensure_connected()
        history = {"sent": [], "received": []}

        # 查询发送记录 (逻辑不变)
        query_sent = "SELECT amount, fee, timestamp FROM packet_history WHERE user_id = ? AND action_type = 'SEND' ORDER BY timestamp DESC LIMIT 20"
        async with self.conn.execute(query_sent, (user_id,)) as cursor:
            history["sent"] = [dict(row) for row in await cursor.fetchall()]

        # 查询接收记录 (不再JOIN，直接从本表读取sender_name)
        query_received = "SELECT amount, sender_name, timestamp FROM packet_history WHERE user_id = ? AND action_type = 'RECEIVE' ORDER BY timestamp DESC LIMIT 20"
        async with self.conn.execute(query_received, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            # 做一个简单的处理，以防万一有旧的、没有sender_name的记录
            history["received"] = [
                {
                    "amount": row["amount"],
                    "timestamp": row["timestamp"],
                    "sender_name": row["sender_name"]
                    if row["sender_name"]
                    else "历史红包",
                }
                for row in rows
            ]
        return history

    # ... [其他数据库方法保持不变] ...
    async def get_active_packet(self, packet_id: str) -> Optional[Dict]:
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT * FROM active_packets WHERE packet_id = ?", (packet_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_packet_by_password(self, password: str) -> Optional[Dict]:
        await self._ensure_connected()
        async with self.conn.execute(
            "SELECT * FROM active_packets WHERE password = ? AND remaining_packets > 0",
            (password,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_user_active_packet(
        self, user_id: str, group_id: str
    ) -> Optional[Dict]:
        await self._ensure_connected()
        query = "SELECT * FROM active_packets WHERE sender_id = ? AND group_id = ? AND packet_type IN ('lucky', 'fixed')"
        async with self.conn.execute(query, (user_id, group_id)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_active_packets_in_group(self, group_id: str) -> List[Dict]:
        await self._ensure_connected()
        now = datetime.now().isoformat()
        query = "SELECT * FROM active_packets WHERE group_id = ? AND expires_at > ? AND remaining_packets > 0 ORDER BY created_at ASC"
        async with self.conn.execute(query, (group_id, now)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def get_expired_packets(self) -> List[Dict]:
        await self._ensure_connected()
        now = datetime.now().isoformat()
        async with self.conn.execute(
            "SELECT * FROM active_packets WHERE expires_at <= ?", (now,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def update_packet_claim(
        self,
        packet_id: str,
        remaining_packets: int,
        amounts_json: str,
        claimed_by_json: str,
    ):
        await self._ensure_connected()
        await self.conn.execute(
            "UPDATE active_packets SET remaining_packets = ?, amounts_json = ?, claimed_by_json = ? WHERE packet_id = ?",
            (remaining_packets, amounts_json, claimed_by_json, packet_id),
        )
        await self.conn.commit()

    async def remove_active_packet(self, packet_id: str):
        await self._ensure_connected()
        await self.conn.execute(
            "DELETE FROM active_packets WHERE packet_id = ?", (packet_id,)
        )
        await self.conn.commit()
