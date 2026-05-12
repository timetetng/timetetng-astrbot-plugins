import re
from datetime import datetime
from astrbot.api import logger
from .const import (
    INSTRUCTION_PROMPT,
    BLOCK_PATTERN,
    FAVOUR_PATTERN,
    ATTITUDE_PATTERN,
    RELATIONSHIP_PATTERN,
    DAILY_FAVOUR_LIMIT,
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

    async def process_llm_response(
        self, user_id: str, session_id: str | None, original_text: str
    ) -> str:
        """解析 LLM 回复，更新数据库，返回清理后的文本"""
        block_match = BLOCK_PATTERN.search(original_text)

        # 如果没有匹配到状态块，直接返回原文
        if not block_match:
            return original_text

        block_text = block_match.group(0)
        final_text = re.sub(r"\[.*?\]", "", original_text, flags=re.DOTALL).strip()

        # 1. 解析好感度 (此时获取的是增量，例如 +5 或 -2)
        favour_match = FAVOUR_PATTERN.search(block_text)
        if not favour_match:
            return final_text  # 有块但没数值，可能是格式错，忽略更新

        try:
            raw_gain = int(favour_match.group(1).strip())
        except ValueError:
            logger.warning(f"无法解析好感度数值: {favour_match.group(1)}")
            return final_text

        current_state = await self.db.get_user_state(user_id, session_id)
        old_favour = current_state["favour"]

        # 2. 修正增益幅度 (强制约束模型输出在合理范围内)
        gain = raw_gain
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

        # 计算应用上限后的实际增益
        actual_gain = gain

        # 只有正向增加才受每日上限限制
        if gain > 0:
            current_daily = current_state["daily_favour_gain"]
            if current_daily >= DAILY_FAVOUR_LIMIT:
                actual_gain = 0  # 达上限，不加分
                logger.info(f"用户 {user_id} 今日增益已达上限")
            elif current_daily + gain > DAILY_FAVOUR_LIMIT:
                allowed_gain = DAILY_FAVOUR_LIMIT - current_daily
                actual_gain = allowed_gain
                current_state["daily_favour_gain"] = DAILY_FAVOUR_LIMIT
            else:
                current_state["daily_favour_gain"] += gain

        # 最终计算
        final_favour = old_favour + actual_gain
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

    async def try_trigger_recovery(self, user_id: str, session_id: str | None) -> dict:
        """
        尝试触发好感度自然恢复机制 (遗忘机制)
        规则:
        1. Favour < -100: +10点/小时, 上限 -100
        2. -100 <= Favour < 0: +2点/小时, 上限 0
        """
        state = await self.db.get_user_state(user_id, session_id)
        current_favour = state["favour"]

        # 如果好感度是非负的，不需要恢复，直接更新时间戳并返回
        if current_favour >= 0:
            state["last_recovery_ts"] = int(datetime.now().timestamp())
            await self.db.update_user_state(user_id, state, session_id)
            return state

        now_ts = int(datetime.now().timestamp())
        last_ts = state.get("last_recovery_ts", 0)

        if last_ts == 0:
            state["last_recovery_ts"] = now_ts
            await self.db.update_user_state(user_id, state, session_id)
            return state

        # 计算经过的小时数
        delta_seconds = now_ts - last_ts
        hours_passed = delta_seconds // 3600

        if hours_passed < 1:
            return state  # 不足一小时，不处理

        recovered_favour = current_favour

        for _ in range(hours_passed):
            if recovered_favour < -100:
                recovered_favour += 20
                # 这一级最多恢复到 -100
                if recovered_favour > -100:
                    recovered_favour = -100
            elif -100 <= recovered_favour < 0:
                recovered_favour += 5
                # 这一级最多恢复到 0
                if recovered_favour > 0:
                    recovered_favour = 0
            else:
                break  # 已经回正，停止恢复

        if recovered_favour != current_favour:
            logger.info(
                f"用户 {user_id} 触发遗忘机制: {hours_passed}小时, 好感 {current_favour} -> {recovered_favour}"
            )
            state["favour"] = recovered_favour

        # 更新结算时间为当前时间
        state["last_recovery_ts"] = now_ts
        await self.db.update_user_state(user_id, state, session_id)

        return state
