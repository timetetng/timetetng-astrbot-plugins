import re
from datetime import datetime
from astrbot.api import logger
from .const import (
    INSTRUCTION_PROMPT, BLOCK_PATTERN, FAVOUR_PATTERN, 
    ATTITUDE_PATTERN, RELATIONSHIP_PATTERN, DAILY_FAVOUR_LIMIT
)
from .database import DatabaseManager

class LogicService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def get_context_prompt(self, state: dict) -> str:
        """生成注入到 System Prompt 的上下文"""
        return (
            f"\n[当前状态] 你与该用户的关系是：{state['relationship']}，"
            f"好感度为 {state['favour']}，"
            f"你对他的印象是：{state['attitude']}。\n"
            f"{INSTRUCTION_PROMPT}"
        )

    async def process_llm_response(self, user_id: str, session_id: str | None, original_text: str) -> str:
        """解析 LLM 回复，更新数据库，返回清理后的文本"""
        block_match = BLOCK_PATTERN.search(original_text)
        
        # 如果没有匹配到状态块，直接返回原文
        if not block_match:
            return original_text

        block_text = block_match.group(0)
        final_text = re.sub(r"\[.*?\]", "", original_text, flags=re.DOTALL).strip()
        
        # 1. 解析好感度
        favour_match = FAVOUR_PATTERN.search(block_text)
        if not favour_match:
            return final_text # 有块但没数值，可能是格式错，忽略更新

        proposed_favour = int(favour_match.group(1).strip())
        current_state = await self.db.get_user_state(user_id, session_id)
        old_favour = current_state["favour"]
        
        # 2. 计算并修正增益
        gain = proposed_favour - old_favour
        if gain > 5:
            logger.warning(f"用户 {user_id} 好感异常增益 {gain}，修正为 5")
            gain = 5
        elif gain < -10:
            logger.warning(f"用户 {user_id} 好感异常减损 {gain}，修正为 -10")
            gain = -10

        # 3. 处理每日上限
        today_str = datetime.now().strftime("%Y-%m-%d")
        if current_state.get("last_update_date") != today_str:
            current_state["daily_favour_gain"] = 0
        current_state["last_update_date"] = today_str

        final_favour = old_favour + gain
        
        if gain > 0:
            current_daily = current_state["daily_favour_gain"]
            if current_daily >= DAILY_FAVOUR_LIMIT:
                final_favour = old_favour # 达上限，不加分
                logger.info(f"用户 {user_id} 今日增益已达上限")
            elif current_daily + gain > DAILY_FAVOUR_LIMIT:
                allowed_gain = DAILY_FAVOUR_LIMIT - current_daily
                final_favour = old_favour + allowed_gain
                current_state["daily_favour_gain"] = DAILY_FAVOUR_LIMIT
            else:
                current_state["daily_favour_gain"] += gain

        current_state["favour"] = final_favour

        # 4. 解析印象与关系 (检查锁定)
        now_ts = datetime.now().timestamp()
        is_locked = current_state.get("relationship_lock_until", 0) > now_ts
        
        attitude_match = ATTITUDE_PATTERN.search(block_text)
        relationship_match = RELATIONSHIP_PATTERN.search(block_text)

        if not is_locked:
            if attitude_match:
                current_state["attitude"] = attitude_match.group(1).strip(" ,")
            if relationship_match:
                current_state["relationship"] = relationship_match.group(1).strip(" ,")
        elif attitude_match or relationship_match:
            logger.info(f"用户 {user_id} 关系锁定中，忽略变更。")

        # 5. 保存状态
        await self.db.update_user_state(user_id, current_state, session_id)
        
        return final_text
