import aiosqlite
import os
import time
from typing import Optional, Dict, Any, List
from .config import DATABASE_FILE, DATABASE_DIR
from astrbot.api import logger

async def init_db():
    """初始化数据库，创建并安全地更新所有表结构，确保数据兼容"""
    try:
        os.makedirs(DATABASE_DIR, exist_ok=True)
        async with aiosqlite.connect(DATABASE_FILE) as db:
            # 步骤 1: 创建或更新 companies 表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    user_id TEXT PRIMARY KEY, name TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL, last_income_claim_time INTEGER NOT NULL,
                    last_event_time INTEGER NOT NULL DEFAULT 0
                );
            """)
            cursor = await db.execute("PRAGMA table_info(companies)")
            columns = {row[1] for row in await cursor.fetchall()}
            if 'dept_ops_level' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN dept_ops_level INTEGER NOT NULL DEFAULT 0")
            if 'dept_res_level' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN dept_res_level INTEGER NOT NULL DEFAULT 0")
            if 'dept_pr_level' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN dept_pr_level INTEGER NOT NULL DEFAULT 0")
            if 'dept_ops_alias' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN dept_ops_alias TEXT DEFAULT NULL")
            if 'dept_res_alias' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN dept_res_alias TEXT DEFAULT NULL")
            if 'dept_pr_alias' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN dept_pr_alias TEXT DEFAULT NULL")
            if 'is_public' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
            if 'stock_ticker' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN stock_ticker TEXT DEFAULT NULL")
            if 'total_shares' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN total_shares INTEGER NOT NULL DEFAULT 0")
            if 'last_earnings_report_time' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN last_earnings_report_time INTEGER NOT NULL DEFAULT 0")
            if 'last_corporate_action_time' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN last_corporate_action_time INTEGER NOT NULL DEFAULT 0")
            # +++ V3 新增：上次查看公司信息的时间戳 +++
            if 'last_profile_view_time' not in columns:
                await db.execute("ALTER TABLE companies ADD COLUMN last_profile_view_time INTEGER NOT NULL DEFAULT 0")
                logger.info("成功为 companies 表添加 last_profile_view_time 字段。")
            
            # 步骤 2: 创建或更新 active_effects 表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS active_effects (
                    effect_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    origin_user_id TEXT DEFAULT NULL,
                    effect_type TEXT NOT NULL, potency REAL NOT NULL, expires_at INTEGER NOT NULL
                );
            """)
            
            cursor = await db.execute("PRAGMA table_info(active_effects)")
            columns_in_effects = {row[1] for row in await cursor.fetchall()}

            if 'origin_user_id' not in columns_in_effects:
                await db.execute("ALTER TABLE active_effects ADD COLUMN origin_user_id TEXT DEFAULT NULL")
            
            if 'is_consumed_on_use' not in columns_in_effects:
                await db.execute("ALTER TABLE active_effects ADD COLUMN is_consumed_on_use INTEGER NOT NULL DEFAULT 0")
                logger.info("成功为 active_effects 表添加 is_consumed_on_use 字段。")
            
            # +++ V3 新增：效果创建时间戳 +++
            if 'created_at' not in columns_in_effects:
                await db.execute("ALTER TABLE active_effects ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
                logger.info("成功为 active_effects 表添加 created_at 字段。")

            await db.execute("CREATE INDEX IF NOT EXISTS idx_effects_user_expires ON active_effects(user_id, expires_at);")
            
            await db.commit()
            logger.info("数据库结构检查与更新完成。")
    except Exception as e:
        logger.error(f"数据库初始化/更新失败: {e}")

async def create_company(user_id: str, company_info: Dict[str, Any]) -> bool:
    """异步创建一个新公司（已更新）"""
    try:
        current_time = int(time.time())
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute(
                """INSERT INTO companies 
                   (user_id, name, level, created_at, last_income_claim_time, last_event_time, 
                    dept_ops_level, dept_res_level, dept_pr_level,
                    dept_ops_alias, dept_res_alias, dept_pr_alias,
                    last_profile_view_time) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, company_info['name'], 1, current_time, current_time, 0, 0, 0, 0, None, None, None, current_time)
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"创建公司(user_id={user_id})失败: {e}")
        return False


async def get_company(user_id: str) -> Optional[Dict[str, Any]]:
    """异步获取指定用户的公司数据"""
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM companies WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"查询公司(user_id={user_id})失败: {e}")
        return None

async def get_all_companies() -> List[Dict[str, Any]]:
    """异步获取所有公司的信息"""
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM companies ORDER BY level DESC, created_at ASC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"查询所有公司失败: {e}")
        return []

async def update_company(user_id: str, updates: Dict[str, Any]) -> bool:
    """异步更新一个用户公司的特定字段"""
    if not updates: return True
    
    set_clause = ', '.join([f"{key} = ?" for key in updates.keys()])
    params = list(updates.values()) + [user_id]
    
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute(f"UPDATE companies SET {set_clause} WHERE user_id = ?", tuple(params))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"更新公司(user_id={user_id})失败: {e}")
        return False

async def delete_company(user_id: str) -> bool:
    """异步删除指定用户的公司数据"""
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute("DELETE FROM companies WHERE user_id = ?", (user_id,))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"删除公司(user_id={user_id})失败: {e}")
        return False

async def delete_all_effects_for_user(user_id: str) -> bool:
    """异步删除指定用户的所有效果数据"""
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute("DELETE FROM active_effects WHERE user_id = ?", (user_id,))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"删除用户 {user_id} 的所有效果失败: {e}")
        return False

async def add_effect(user_id: str, effect_type: str, potency: float, duration_seconds: int, origin_user_id: Optional[str] = None, is_consumed_on_use: bool = False):
    """为用户添加一个有时效性的效果 (V3 - 增加创建时间)"""
    now = int(time.time())
    expires_at = now + duration_seconds
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute(
                """INSERT INTO active_effects 
                   (user_id, effect_type, potency, expires_at, origin_user_id, is_consumed_on_use, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, effect_type, potency, expires_at, origin_user_id, is_consumed_on_use, now)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"为用户 {user_id} 添加效果失败: {e}")

async def get_active_effects(user_id: str, effect_type: str) -> List[Dict]:
    """获取用户所有未过期的指定类型效果"""
    now = int(time.time())
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM active_effects WHERE user_id = ? AND effect_type = ? AND expires_at > ?",
                (user_id, effect_type, now)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"获取用户 {user_id} 的活动效果失败: {e}")
        return []

# +++ V3 新增：查询新收到的debuff +++
async def get_new_debuffs_since(user_id: str, timestamp: int) -> List[Dict]:
    """获取用户自指定时间戳后收到的新debuff"""
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM active_effects
                   WHERE user_id = ? AND created_at > ?
                   AND (
                       (effect_type = 'income_modifier' AND potency < 1.0) OR
                       (effect_type = 'cost_modifier')
                   )
                   ORDER BY created_at DESC""",
                (user_id, timestamp)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"查询用户 {user_id} 的新debuff失败: {e}")
        return []

async def clear_expired_effects(user_id: str):
    """清理用户所有已过期的效果"""
    now = int(time.time())
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute("DELETE FROM active_effects WHERE user_id = ? AND expires_at <= ?", (user_id, now))
            await db.commit()
    except Exception as e:
        logger.error(f"清理用户 {user_id} 的过期效果失败: {e}")

async def consume_effect(effect_id: int):
    """根据主键ID消耗一个效果"""
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute("DELETE FROM active_effects WHERE effect_id = ?", (effect_id,))
            await db.commit()
    except Exception as e:
        logger.error(f"消耗效果 (id={effect_id}) 失败: {e}")