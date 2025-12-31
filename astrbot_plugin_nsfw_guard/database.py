import sqlite3
import time
from pathlib import Path
from astrbot.api import logger

class Database:
    def __init__(self, data_dir: Path):
        self.db_file = data_dir / "offenses.db"
        self._init_db()

    def _init_db(self):
        try:
            self.conn = sqlite3.connect(self.db_file, check_same_thread=False)
            cursor = self.conn.cursor()
            # 用户状态表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS offenses (
                    user_id TEXT PRIMARY KEY,
                    offense_count INTEGER NOT NULL DEFAULT 0,
                    block_until_timestamp REAL NOT NULL DEFAULT 0,
                    last_offense_timestamp REAL NOT NULL DEFAULT 0
                )
            ''')
            # 违规日志表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS offense_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    user_name TEXT,
                    group_id TEXT,
                    offense_type TEXT NOT NULL,
                    trigger_method TEXT,
                    reason TEXT,
                    offending_message TEXT,
                    timestamp REAL NOT NULL
                )
            ''')
            self.conn.commit()
            
            # 兼容性检查：确保 last_offense_timestamp 存在
            try:
                cursor.execute("SELECT last_offense_timestamp FROM offenses LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute("ALTER TABLE offenses ADD COLUMN last_offense_timestamp REAL NOT NULL DEFAULT 0")
                self.conn.commit()
                logger.info("NSFW Guard: 数据库已升级，添加 last_offense_timestamp 字段。")
                
        except Exception as e:
            logger.error(f"NSFW Guard: 初始化数据库失败: {e}")

    def get_user_data(self, user_id: str) -> dict:
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT offense_count, block_until_timestamp, last_offense_timestamp FROM offenses WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return {"count": row[0], "block_until": row[1], "last_offense": row[2]}
            return {"count": 0, "block_until": 0, "last_offense": 0}
        except Exception as e:
            logger.error(f"NSFW Guard: 查询用户 {user_id} 失败: {e}")
            return {"count": 0, "block_until": 0, "last_offense": 0}

    def update_user_data(self, user_id: str, count: int, block_until: float, last_offense: float):
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO offenses (user_id, offense_count, block_until_timestamp, last_offense_timestamp)
                VALUES (?, ?, ?, ?)
            ''', (user_id, count, block_until, last_offense))
            self.conn.commit()
        except Exception as e:
            logger.error(f"NSFW Guard: 更新用户 {user_id} 失败: {e}")

    def log_offense(self, user_id, user_name, group_id, offense_type, trigger_method, reason, message):
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO offense_logs (user_id, user_name, group_id, offense_type, trigger_method, reason, offending_message, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, user_name, group_id or "", offense_type, trigger_method, reason, message, time.time()))
            self.conn.commit()
        except Exception as e:
            logger.error(f"NSFW Guard: 记录日志失败: {e}")

    def get_stats(self):
        cursor = self.conn.cursor()
        # 总体统计
        cursor.execute("SELECT offense_type, COUNT(*) FROM offense_logs GROUP BY offense_type")
        overall = dict(cursor.fetchall())
        
        # 用户排行 Top 10
        cursor.execute("SELECT user_name, user_id, COUNT(*) as count FROM offense_logs GROUP BY user_id ORDER BY count DESC LIMIT 10")
        top_users = cursor.fetchall()
        
        # 群组排行 Top 10
        cursor.execute("SELECT group_id, COUNT(*) as count FROM offense_logs WHERE group_id IS NOT NULL AND group_id != '' GROUP BY group_id ORDER BY count DESC LIMIT 10")
        top_groups = cursor.fetchall()
        
        return overall, top_users, top_groups

    def get_user_logs(self, user_id: str, limit=10):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT timestamp, offense_type, reason, offending_message 
            FROM offense_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
        """, (user_id, limit))
        return cursor.fetchall()

    def get_all_offending_messages(self) -> list[str]:
        """获取所有违规消息内容，用于生成词云"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT offending_message FROM offense_logs WHERE offending_message IS NOT NULL AND offending_message != ''")
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"NSFW Guard: 获取违规消息失败: {e}")
            return []

    def close(self):
        if self.conn:
            self.conn.close()
