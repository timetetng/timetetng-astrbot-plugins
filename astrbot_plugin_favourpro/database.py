import aiosqlite
from pathlib import Path
from typing import Dict, Any, Optional
from astrbot.api import logger
from .const import DEFAULT_STATE

class DatabaseManager:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db = None

    async def init_db(self):
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS user_states (
                key TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT,
                favour INTEGER DEFAULT 0, attitude TEXT DEFAULT '中立', relationship TEXT DEFAULT '陌生人'
            )""")

        async with self._db.execute("PRAGMA table_info(user_states)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        
        # 动态添加列
        new_columns = {
            "daily_favour_gain": "INTEGER DEFAULT 0",
            "last_update_date": "TEXT DEFAULT '1970-01-01'",
            "daily_gift_gain": "INTEGER DEFAULT 0",
            "relationship_lock_until": "INTEGER DEFAULT 0",
            "last_recovery_ts": "INTEGER DEFAULT 0"  # [新增] 记录上次恢复结算的时间戳
        }
        
        for col_name, col_def in new_columns.items():
            if col_name not in columns:
                await self._db.execute(f"ALTER TABLE user_states ADD COLUMN {col_name} {col_def}")

        await self._db.commit()
        logger.info("好感度数据库初始化成功！")

    def _get_key(self, user_id: str, session_id: Optional[str]) -> str:
        return f"{session_id}_{user_id}" if session_id else user_id

    async def get_user_state(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        if not self._db: return DEFAULT_STATE.copy()
        
        self._db.row_factory = aiosqlite.Row
        query = "SELECT favour, attitude, relationship, daily_favour_gain, last_update_date, daily_gift_gain, relationship_lock_until FROM user_states WHERE key = ?"

        # 尝试获取 Session 级别状态
        if session_id:
            session_key = self._get_key(user_id, session_id)
            async with self._db.execute(query, (session_key,)) as cursor:
                row = await cursor.fetchone()
                if row: return dict(row)

        # 尝试获取全局状态
        global_key = self._get_key(user_id, None)
        async with self._db.execute(query, (global_key,)) as cursor:
            row = await cursor.fetchone()
            if row: return dict(row)

        return DEFAULT_STATE.copy()

    async def update_user_state(self, user_id: str, new_state: Dict[str, Any], session_id: Optional[str] = None):
        if not self._db: return
        key = self._get_key(user_id, session_id)
        
        # 合并默认值防止 key error
        state = DEFAULT_STATE.copy()
        state.update(new_state)

        await self._db.execute(
            """INSERT INTO user_states (key, user_id, session_id, favour, attitude, relationship, daily_favour_gain, last_update_date, daily_gift_gain, relationship_lock_until)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET
               favour = excluded.favour, attitude = excluded.attitude, relationship = excluded.relationship,
               daily_favour_gain = excluded.daily_favour_gain, last_update_date = excluded.last_update_date,
               daily_gift_gain = excluded.daily_gift_gain, relationship_lock_until = excluded.relationship_lock_until""",
            (
                key, user_id, session_id or "",
                state["favour"], state["attitude"], state["relationship"],
                state["daily_favour_gain"], state["last_update_date"], 
                state["daily_gift_gain"], state["relationship_lock_until"]
            ),
        )
        await self._db.commit()

    async def get_favour_ranking(self, limit: int = 10) -> list:
        if not self._db: return []
        self._db.row_factory = aiosqlite.Row
        query = "SELECT user_id, favour, relationship FROM user_states WHERE session_id = '' ORDER BY favour DESC LIMIT ?"
        async with self._db.execute(query, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
            
    async def get_dislike_ranking(self, limit: int = 10) -> list:
        if not self._db: return []
        self._db.row_factory = aiosqlite.Row
        query = "SELECT user_id, favour, relationship FROM user_states WHERE session_id = '' ORDER BY favour ASC LIMIT ?"
        async with self._db.execute(query, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def close(self):
        if self._db:
            await self._db.close()
