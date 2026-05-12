# main.py

import asyncio
import datetime
import os
import random
import re
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register

from ..common.forwarder import Forwarder

# --- 确保导入了 shared_services ---
from ..common.services import shared_services
from .database import SignDatabase
from .sign_manager import SignManager

# --- 配置部分 (无变化) ---
MAX_LOTTERY_PER_DAY = 3
MIN_LOTTERY_BET = 5
MAX_LOTTERY_BET = 1_000_000_000
LUCK_CARD_PERCENT_COST_TIERS = (
    0.0,  # 第1次使用 (已使用0次): 0% 金币成本
    0.01,  # 第2次使用 (已使用1次): 1%
    0.01,  # 第3次使用 (已使用2次): 3%
    0.03,  # 第4次使用 (已使用3次): 5%
    0.03,  # 第5次使用 (已使用4次): 7%
    0.08,  # 第6次使用 (已使用5次): 10%
    0.15,  # 第7次使用 (已使用6次): 20%
    0.30,  # 第8次使用 (已使用7次): 40%
    0.60,  # 第9次使用 (已使用8次): 80%
    0.90,  # 第10次使用 (已使用9次): 90%
)  # 第11次及以后将自动使用最后一个值 (90%)

LOTTERY_TIERS = [
    (
        "💥传说大奖💥",
        1,
        [4.0, 8.0],
        "口圭！金色传说！您获得了 {multiplier:.2f} 倍回报！",
    ),
    ("🎉稀有大奖🎉", 7, [1.5, 2.8], "强运！您抽中了倍率 {multiplier:.2f}x！"),
    ("✨幸运奖励✨", 36, [1.0, 1.4], "运气不错！获得了 {multiplier:.2f} 倍的金币！"),
    ("😅普通结果😅", 42, [0.5, 0.8], "一般般，只拿回了 {multiplier:.2f}x ..."),
    (
        "💨血本无归💨",
        10,
        [0.0, 0.3],
        "一阵风吹过...您的金币只剩下了 {multiplier:.2f}x ...",
    ),
]
TIER_WEIGHTS = [tier[1] for tier in LOTTERY_TIERS]
JACKPOT_INITIAL_AMOUNT = 2000
JACKPOT_WIN_CHANCE = 0.005

FORTUNE_EFFECTS = {
    "圣辉": {
        "description": "神迹降临！当日首3次抽奖必为[幸运奖励]及以上！奖池命中率x2，奖励倍率提升30%！",
        "jackpot_chance_mult": 2.0,
        "prize_mult_mod": 1.3,
        "special_effect": "holy_light",  # 特殊标记，用于触发特殊逻辑
    },
    "大吉": {
        "description": "好运连连！奖池命中率x2，奖励倍率提升10%！",
        "jackpot_chance_mult": 2.0,
        "prize_mult_mod": 1.1,
        "tier_shift": {"from": "😅普通结果😅", "to": "🎉稀有大奖🎉", "amount": 5},
    },
    "吉": {
        "description": "一帆风顺！稀有奖励的概率略微提升了。",
        "jackpot_chance_mult": 1.0,
        "prize_mult_mod": 1.0,
        "tier_shift": {"from": "😅普通结果😅", "to": "✨幸运奖励✨", "amount": 10},
    },
    "半吉": {
        "description": "平平淡淡才是真，今天没有特殊效果。",
        "jackpot_chance_mult": 1.0,
        "prize_mult_mod": 1.0,
        "tier_shift": None,
    },
    "小吉": {
        "description": "平平淡淡才是真，今天没有特殊效果。",
        "jackpot_chance_mult": 1.0,
        "prize_mult_mod": 1.0,
        "tier_shift": None,
    },
    "末吉": {
        "description": "运气稍差，不容易抽到稀有奖励了。",
        "jackpot_chance_mult": 1.0,
        "prize_mult_mod": 1.0,
        "tier_shift": {"from": "🎉稀有大奖🎉", "to": "😅普通结果😅", "amount": 5},
    },
    "末小吉": {
        "description": "运气不佳，血本无归的概率略微提升了...",
        "jackpot_chance_mult": 1.0,
        "prize_mult_mod": 1.0,
        "tier_shift": {"from": "✨幸运奖励✨", "to": "💨血本无归💨", "amount": 3},
    },
    "凶": {
        "description": "诸事不宜！奖池命中率减半，所有奖励倍率降低25%！",
        "jackpot_chance_mult": 0.5,
        "prize_mult_mod": 0.75,
        "tier_shift": {"from": "🎉稀有大奖🎉", "to": "💨血本无归💨", "amount": 3},
    },
}
TIER_NAME_TO_INDEX = {tier[0]: i for i, tier in enumerate(LOTTERY_TIERS)}


class EconomyAPI:
    def __init__(self, db: "SignDatabase"):  # 使用引号避免循环导入
        self._db = db

    def _format_coin_display(self, amount: int) -> str:
        """将整数金币值格式化为带两位小数的字符串用于显示。"""
        try:
            numeric_amount = int(amount or 0)
        except (ValueError, TypeError):
            numeric_amount = 0
        return f"{numeric_amount}"

    async def get_coins(self, user_id: str) -> int:
        """(Async) 查询指定用户的金币余额。如果用户不存在或数据异常，返回 0。"""
        user_data = await self._db.get_user_data(user_id)
        if not user_data:
            return 0

        raw_coins = user_data.get("coins", 0)

        try:
            return round(float(raw_coins or 0))
        except (ValueError, TypeError):
            return 0

    async def add_coins(self, user_id: str, amount: int, reason: str) -> bool:
        """
        (Async) 为指定用户增加或减少金币。
        此版本支持负数金币（欠款），扣款操作不会因余额不足而失败。
        """
        try:
            safe_amount = round(float(amount))
        except (ValueError, TypeError):
            logger.error(
                f"API add_coins 失败: 传入的 amount '{amount}' 不是有效的数字。"
            )
            return False

        current_coins = await self.get_coins(user_id)
        new_coins = current_coins + safe_amount

        await self._db.update_user_data(user_id, coins=new_coins)
        await self._db.log_coins(user_id, safe_amount, reason)

        operation_text = "增加" if safe_amount >= 0 else "减少"
        result_text = f"余额变为 {new_coins}"
        if current_coins < 0 and new_coins > current_coins:
            result_text = f"偿还欠款后，余额变为 {new_coins}"
        return True

    async def set_coins(self, user_id: str, amount: int, reason: str) -> bool:
        """
        (Async, 慎用) 直接设置指定用户的金币数量。
        出于安全考虑，此方法仍然禁止直接将用户金币设置为负数。
        """
        try:
            safe_amount = round(float(amount))
        except (ValueError, TypeError):
            logger.error(
                f"API set_coins 失败: 传入的 amount '{amount}' 不是有效的数字。"
            )
            return False
        if safe_amount < 0:
            logger.error(
                f"API set_coins 失败: 目标金额 {safe_amount} 不能为负。如需扣款请使用 add_coins。"
            )
            return False

        current_coins = await self.get_coins(user_id)
        change_amount = safe_amount - current_coins

        await self._db.update_user_data(user_id, coins=safe_amount)
        await self._db.log_coins(user_id, change_amount, reason)
        logger.info(
            f"API金币设置: 用户 {user_id} 金币被设置为 {safe_amount}, 原因: {reason}"
        )
        return True

    async def get_user_profile(self, user_id: str) -> dict | None:
        """
        (Async) 获取用户的公开签到信息。
        (此处的金币字段仍然是格式化后的，用于显示)
        """
        user_data = await self._db.get_user_data(user_id)
        if not user_data:
            return None

        coins_value = await self.get_coins(user_id)

        # 从数据库获取原始昵称
        display_nickname = user_data.get("nickname")

        # 如果是机器人自己，则强制修改昵称
        if str(user_id) == "1902929802":
            display_nickname = "菲比"

        return {
            "user_id": user_data.get("user_id"),
            "nickname": display_nickname,  # <-- 使用处理过的昵称
            "coins": self._format_coin_display(coins_value),  # 可以正确显示负数
            "total_days": user_data.get("total_days", 0),
            "continuous_days": user_data.get("continuous_days", 0),
            "last_sign": user_data.get("last_sign"),
        }

    async def get_ranking(self, limit: int = 10) -> list:
        """
        (Async) 获取金币排行榜。
        (金币字段将被格式化)
        """
        ranking_data = await self._db.get_ranking(limit=limit)
        formatted_ranking = []
        for row in ranking_data:
            profile = dict(row)
            clean_coins = round(float(profile.get("coins", 0) or 0))
            profile["coins"] = self._format_coin_display(clean_coins)
            formatted_ranking.append(profile)

        return formatted_ranking

    async def get_coin_history(self, user_id: str, limit: int = 5) -> list:
        """
        (Async) 获取指定用户的金币变动历史。
        (金币变动量将被格式化)
        """
        history_data = await self._db.get_coin_history(user_id, limit=limit)
        formatted_history = []
        for row in history_data:
            history_item = dict(row)
            clean_amount = round(float(history_item.get("amount", 0) or 0))
            history_item["amount"] = self._format_coin_display(clean_amount)
            formatted_history.append(history_item)

        return formatted_history

    async def get_incoming_transfer_history(
        self, user_id: str, limit: int = 1000
    ) -> list[dict]:
        """
        获取指定用户的收款历史记录。

        与 get_coin_history 不同，此方法专门获取 transfer_history 表中的记录。
        返回的是更原始的、包含发送方信息的交易列表。

        Args:
            user_id (str): 收款用户的 ID。
            limit (int): 获取记录的条数上限。

        Returns:
            List[Dict]: 一个包含交易记录字典的列表。
                        每个字典包含: sender_id, sender_name, recipient_id, amount, timestamp 等字段。
        """
        # 调用数据库底层方法
        raw_history = await self._db.get_incoming_transfers(user_id, limit=limit)
        # 将 aiosqlite.Row 转换为更通用的 dict，方便其他插件使用
        return [dict(row) for row in raw_history]

    # --- [新增API 1: 抽奖历史] ---
    async def get_lottery_history(self, user_id: str, limit: int = 10) -> list:
        """
        (Async) 获取指定用户详细的抽奖历史记录。
        返回一个字典列表，每条记录包含：时间戳、花费、总奖金、总倍率、是否中大奖、抽奖时运势。
        """
        # 直接调用底层的数据库方法
        raw_history = await self._db.get_lottery_history(user_id, limit=limit)
        if not raw_history:
            return []

        # 格式化数据，提供一个干净、易用的API返回格式
        formatted_history = []
        for row in raw_history:
            item = dict(row)
            formatted_history.append(
                {
                    "timestamp": item.get("timestamp"),
                    "bet_amount": int(item.get("bet_amount", 0)),
                    "prize_won": int(item.get("prize_won", 0)),
                    # 将倍率格式化为两位小数的字符串，更适合展示
                    "multiplier": f"{item.get('multiplier', 0.0):.2f}x",
                    # 将 0/1 转换为更直观的布尔值
                    "is_jackpot": bool(item.get("is_jackpot", 0)),
                    "fortune_at_time": item.get("fortune_at_time", "未知"),
                }
            )
        return formatted_history

    async def get_fortune_history(self, user_id: str, limit: int = 5) -> list:
        """
        (Async) 获取指定用户的运势抽取记录。
        返回一个字典列表，每条记录包含：时间戳、运势结果、运势值。
        """
        history_data = await self._db.get_fortune_history(user_id, limit=limit)

        # 简单地将数据库行对象转换为字典列表，提供一个标准的API响应
        return [dict(row) for row in history_data] if history_data else []


@register("astrbot_plugin_sign", "FengYing", "一个可自定义金额的抽奖签到插件", "1.2")
class SignPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        plugin_dir = os.path.dirname(__file__)
        self.db = SignDatabase(plugin_dir)
        self.total_lottery_weight = sum(TIER_WEIGHTS)
        self.api = None
        self.last_reset_date = None
        self.forwarder = Forwarder()
        asyncio.create_task(self.initialize_plugin())

    async def _daily_reset_task(self):
        """
        一个健壮的后台任务，在启动时检查一次，然后定时在每天23:59重置奖池。
        """
        logger.info("奖池每日重置任务已启动。")

        # 任务启动时，先等待一小会儿，确保数据库等其他组件已准备好
        await asyncio.sleep(10)

        try:
            today_str = datetime.date.today().isoformat()
            last_reset_date_str = await self.db.get_setting("last_jackpot_reset_date")

            if last_reset_date_str != today_str:
                logger.info(
                    f"检测到日期变更或首次运行（上次重置: {last_reset_date_str}, 今天: {today_str}），立即执行一次奖池重置..."
                )
                await self.db.set_setting("jackpot_pool", str(JACKPOT_INITIAL_AMOUNT))
                await self.db.set_setting("last_jackpot_reset_date", today_str)
                logger.info(f"奖池已成功重置为初始值: {JACKPOT_INITIAL_AMOUNT}。")
        except Exception as e:
            logger.error(f"启动时检查奖池重置失败: {e}", exc_info=True)

        # --- 主循环：定时任务 ---
        while True:
            try:
                # 1. 计算到下一个 23:59:00 的秒数
                now = datetime.datetime.now()
                # 设置目标时间为今天的 23:59
                next_run_time = now.replace(hour=23, minute=59, second=0, microsecond=0)

                if now > next_run_time:
                    # 如果当前时间已经超过了今天的23:59，那么目标就是明天的23:59
                    next_run_time += datetime.timedelta(days=1)

                sleep_seconds = (next_run_time - now).total_seconds()

                logger.info(
                    f"下一次奖池自动重置已安排在: {next_run_time.strftime('%Y-%m-%d %H:%M:%S')}"
                )

                # 2. 等待指定秒数
                await asyncio.sleep(sleep_seconds)

                # 3. 时间到了，执行重置操作
                logger.info(
                    f"到达预定时间 {next_run_time.strftime('%H:%M:%S')}, 开始执行每日奖池重置..."
                )
                await self.db.set_setting("jackpot_pool", str(JACKPOT_INITIAL_AMOUNT))

                # 4. 记录重置日期
                reset_date_str = next_run_time.date().isoformat()
                await self.db.set_setting("last_jackpot_reset_date", reset_date_str)

                logger.info(
                    f"每日奖池已成功重置为初始值: {JACKPOT_INITIAL_AMOUNT}，并已记录重置日期为 {reset_date_str}。"
                )

                # 5. 短暂休眠61秒，以防止万一时间计算出问题导致CPU空转，并确保不会在同一分钟内重复执行
                await asyncio.sleep(61)

            except asyncio.CancelledError:
                logger.info("奖池每日重置任务被取消。")
                break  # 退出循环
            except Exception as e:
                logger.error(f"奖池每日重置任务出现异常: {e}", exc_info=True)
                # 发生异常后等待5分钟再重试，防止错误刷屏
                await asyncio.sleep(300)

    async def initialize_plugin(self):
        """
        异步初始化插件本身。
        """
        try:
            logger.info("正在初始化签到插件...")

            await self.db.get_setting("placeholder", "0")

            if await self.db.get_setting("jackpot_pool") is None:
                await self.db.set_setting("jackpot_pool", str(JACKPOT_INITIAL_AMOUNT))

            self.api = EconomyAPI(self.db)
            shared_services["economy_api"] = self.api
            logger.info("经济系统 API 已注册到全局服务。")
            asyncio.create_task(self._daily_reset_task())
        except Exception as e:
            logger.error(f"签到插件异步初始化失败: {e}", exc_info=True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新商店", alias={"重载商店"})
    async def refresh_shop_items(self, event: AstrMessageEvent):
        """
        [管理员命令] 手动将此插件的物品注册到商店插件。
        """
        yield event.plain_result("尝试刷新商店商品...")
        shop_api = shared_services.get("shop_api")

        if not shop_api:
            yield event.plain_result(
                "❌ 失败：未找到商店服务 API。请确保已正确加载 `shop_plugin`。"
            )
            return

        try:
            await shop_api.register_item(
                owner_plugin="astrbot_plugin_sign",
                item_id="lucky_clover",
                name="幸运四叶草",
                description="[消耗品] 购买后今日抽奖时，正面收益（幸运奖励及以上）的概率提升。",
                price=1500,
            )
            await shop_api.register_item(
                owner_plugin="astrbot_plugin_sign",
                item_id="luck_change_card",
                name="转运卡",
                description="[消耗品] 购买后，立即重新抽取一次今日运势。",
                price=200,
            )
            await shop_api.register_item(
                owner_plugin="astrbot_plugin_sign",
                item_id="lottery_ticket",
                name="抽奖券",
                description="[消耗品]<每日限购两次> 使用后增加一次抽奖次数，代价是减少持有金币的20%",
                price=100,
                daily_limit=2,
            )
            yield event.plain_result("✅ 成功！签到插件物品已在商店中刷新。")
        except Exception as e:
            logger.error(f"手动物品注册期间出错: {e}", exc_info=True)
            yield event.plain_result(f"❌ 注册物品时发生内部错误: {e}")

    async def terminate(self):
        """安全地关闭插件终止时的数据库连接。"""
        logger.info("正在关闭签到插件的数据库连接...")
        if self.db:
            await self.db.close()

    async def _check_and_consume_lottery_items(
        self, event: AstrMessageEvent, user_data: dict[str, Any]
    ) -> str | None:
        """
        用于检查并消耗抽奖相关的道具（幸运四叶草、抽奖券）。
        """
        if getattr(event, "items_consumed_this_event", False):
            return None

        shop_api = shared_services.get("shop_api")
        if not shop_api:
            return None

        user_id = event.get_sender_id()
        today_str = datetime.date.today().strftime("%Y-%m-%d")

        consumed_item_messages = []

        # 1. 检查幸运四叶草
        if user_data.get("lucky_clover_buff_date") != today_str:
            if await shop_api.has_item(user_id, "lucky_clover"):
                if await shop_api.consume_item(user_id, "lucky_clover"):
                    await self.db.update_user_data(
                        user_id, lucky_clover_buff_date=today_str
                    )
                    msg = "🍀 您背包中的【幸运四叶草】已自动使用！\n今日您的抽奖将受到好运加持！"
                    consumed_item_messages.append(msg)
        # 2. 检查抽奖券
        if await shop_api.has_item(user_id, "lottery_ticket"):
            if await shop_api.consume_item(user_id, "lottery_ticket"):
                current_coins = await self.api.get_coins(user_id)
                cost = int(current_coins * 0.20)
                current_extra_attempts = user_data.get("extra_lottery_attempts", 0)
                remaining_coins = await self.db.process_lottery_ticket_usage(
                    user_id=user_id,
                    cost=cost,
                    current_extra_attempts=current_extra_attempts,
                )

                msg = (
                    f"🎟️ 您背包中的【抽奖券】已自动使用！\n"
                    f"效果：增加 1 次今日抽奖次数。\n"
                    f"代价：扣除了您当前金币的20% ({cost}金币)。\n"
                    f"💰 剩余金币: {remaining_coins}"
                )
                consumed_item_messages.append(msg)

        if consumed_item_messages:
            setattr(event, "items_consumed_this_event", True)
            return "\n--------------------\n".join(consumed_item_messages)

        return None

    @filter.command("转运", alias={"luckchange"})
    async def luck_change_command(self, event: AstrMessageEvent) -> MessageEventResult:
        """
        使用【转运卡】来刷新今日运势。
        此操作会消耗一张转运卡，并根据您的总资产扣除一定比例的金币。
        """
        # 依赖服务获取 - 在函数执行时实时获取，打破循环依赖
        shop_api = shared_services.get("shop_api")
        stock_api = shared_services.get("stock_market_api")

        if not shop_api:
            return event.plain_result("错误：商店服务当前不可用。")
        if not stock_api:
            return event.plain_result("错误：股市服务当前不可用，无法计算您的总资产。")

        user_id = event.get_sender_id()

        # 检查用户是否拥有转运卡
        if not await shop_api.has_item(user_id, "luck_change_card"):
            return event.plain_result("您没有【转运卡】，无法进行转运。")

        user_data = await self.db.get_user_data(user_id)
        if not user_data:
            return event.plain_result("错误：找不到您的用户数据。")

        # --- 成本计算逻辑 ---
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        last_use_date = user_data.get("last_luck_change_card_use_date")
        current_uses = user_data.get("luck_change_card_uses_today", 0)

        if last_use_date != today_str:
            current_uses = 0

        # 根据当日使用次数确定成本比例
        if current_uses < len(LUCK_CARD_PERCENT_COST_TIERS):
            current_percentage = LUCK_CARD_PERCENT_COST_TIERS[current_uses]
        else:
            current_percentage = LUCK_CARD_PERCENT_COST_TIERS[-1]

        # --- 按总资产计算成本 ---
        asset_data = await stock_api.get_user_total_asset(user_id)
        total_asset = asset_data.get("total_assets", 0)
        cost = int(total_asset * current_percentage)
        current_coins = user_data.get("coins", 0)
        if current_coins < cost:
            return event.plain_result(
                f"金币不足！本次转运需要 {cost} 金币，但您只有 {current_coins} 金币。"
            )

        # --- 消耗道具并执行转运 ---
        if await shop_api.consume_item(user_id, "luck_change_card"):
            new_coins = current_coins - cost
            fortune_result, fortune_value = SignManager.get_fortune()

            # 触发“圣辉”成就
            if fortune_result == "圣辉" and shared_services:
                achievement_api = shared_services.get("achievement_api")
                if achievement_api:
                    await achievement_api.unlock_achievement(
                        user_id=user_id,
                        achievement_id="lottery_holy_radiance",
                        event=event,
                    )

            # 使用事务一次性更新数据库
            reason_for_cost = f"使用转运卡(第{current_uses + 1}次,成本基于总资产的{current_percentage:.0%})"
            await self.db.process_luck_change_card_usage(
                user_id=user_id,
                new_coins=new_coins,
                cost=cost,
                fortune_result=fortune_result,
                fortune_value=fortune_value,
                new_uses_today=current_uses + 1,
                today_str=today_str,
                reason_for_cost=reason_for_cost,
                holy_light_uses_today=0,
            )

            # 计算下一次的使用成本
            next_use_index = current_uses + 1
            next_percentage = (
                LUCK_CARD_PERCENT_COST_TIERS[next_use_index]
                if next_use_index < len(LUCK_CARD_PERCENT_COST_TIERS)
                else LUCK_CARD_PERCENT_COST_TIERS[-1]
            )

            msg = (
                f"✨ 消耗了您总资产的 {current_percentage:.0%} ({cost} 金币) 和1张【转运卡】(今日第 {current_uses + 1} 次)...\n"
                f"您今日的运势刷新为: 【{fortune_result}】({fortune_value}/500)\n"
                f"💰 剩余金币: {new_coins}\n"
                f"📈 下一次使用成本: 您届时总资产的 {next_percentage:.0%}"
            )
            return event.plain_result(msg)
        else:
            return event.plain_result("使用【转运卡】失败，请稍后再试。")

    def _calculate_lottery_ev(self) -> tuple[float, list[dict[str, Any]]]:
        total_ev = 0.0
        tier_details = []
        if self.total_lottery_weight == 0:
            return 0.0, []
        for tier in LOTTERY_TIERS:
            name, weight, mult_range, _ = tier
            min_mult, max_mult = mult_range
            probability = weight / self.total_lottery_weight
            avg_multiplier = (min_mult + max_mult) / 2.0
            ev_contribution = probability * avg_multiplier
            total_ev += ev_contribution
            tier_details.append(
                {"name": name, "probability": probability, "mult_range": mult_range}
            )
        return total_ev, tier_details

    @filter.command("签到", alias={"sign"})
    async def sign(self, event: AstrMessageEvent):
        """每日签到"""
        try:
            user_id = event.get_sender_id()
            user_name = event.get_sender_name()
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            user_data = await self.db.get_user_data(user_id) or {}

            if user_data.get("last_sign") == today_str:
                if user_data.get("nickname") != user_name:
                    await self.db.update_user_data(user_id, nickname=user_name)
                response_text = "今天已经签到过啦喵~\n明天再来吧！"
                yield event.plain_result(response_text)
                event.stop_event()
                return

            yesterday_str = (
                datetime.date.today() - datetime.timedelta(days=1)
            ).strftime("%Y-%m-%d")
            continuous_days_new = (
                user_data.get("continuous_days", 0) + 1
                if user_data.get("last_sign") == yesterday_str
                else 1
            )
            coins_got, coins_gift = SignManager.calculate_sign_rewards(
                continuous_days_new
            )
            fortune_result, fortune_value = SignManager.get_fortune()

            if fortune_result == "圣辉" and shared_services:
                achievement_api = shared_services.get("achievement_api")
                if achievement_api:
                    await achievement_api.unlock_achievement(
                        user_id=user_id,
                        achievement_id="lottery_holy_radiance",
                        event=event,
                    )

            display_data = user_data.copy()
            display_data["continuous_days"] = continuous_days_new
            result_text = SignManager.format_sign_result(
                display_data, coins_got, coins_gift, fortune_result, fortune_value
            )

            await self.db.update_user_data(
                user_id,
                nickname=user_name,
                total_days=user_data.get("total_days", 0) + 1,
                last_sign=today_str,
                continuous_days=continuous_days_new,
                coins=user_data.get("coins", 0) + coins_got + coins_gift,
                total_coins_gift=user_data.get("total_coins_gift", 0) + coins_gift,
                last_fortune_result=fortune_result,
                last_fortune_value=fortune_value,
                holy_light_uses_today=0,  # <--- 新增此行
            )

            if coins_gift > 0:
                await self.db.log_coins(
                    user_id, coins_gift, f"连续{continuous_days_new}天签到奖励"
                )
            await self.db.log_fortune(user_id, fortune_result, value=fortune_value)

            yield event.plain_result(result_text)
            event.stop_event()

        except Exception as e:
            logger.error(f"签到失败: {e}", exc_info=True)
            yield event.plain_result("签到失败了喵~ 请联系管理员检查日志。")

    @filter.command("查询", alias={"query", "info"})
    async def query_command(self, event: AstrMessageEvent) -> MessageEventResult:
        """
        查询个人或他人的签到、金币及运势信息。
        用法: /查询 [@某人]
        """
        try:
            # --- 1. 确定目标用户 ---
            target_user_id = None
            # 遍历消息链以查找 @ 提及
            for component in event.message_obj.message:
                if isinstance(component, Comp.At):
                    # 在提供的文档中，'qq' 属性在 QQ 平台上代表用户 ID
                    target_user_id = component.qq
                    break

            # 如果未找到提及，则默认为命令发送者
            if not target_user_id:
                target_user_id = event.get_sender_id()

            # --- 2. 获取用户数据 ---
            user_data = await self.db.get_user_data(target_user_id)
            today_str = datetime.date.today().strftime("%Y-%m-%d")

            # # --- 3. 处理道具消耗（例如，抽奖券） ---
            # if user_data:
            #     consume_msg = await self._check_and_consume_lottery_items(
            #         event, user_data
            #     )
            #     if consume_msg:
            #         # 单独发送消耗消息，这样它们就不会阻塞主查询结果
            #         await event.send(event.plain_result(consume_msg))
            #         # 重新获取数据，以防消耗操作改变了用户状态（如金币）
            #         user_data = await self.db.get_user_data(target_user_id)
            #
            # --- 4. 处理并显示数据 ---
            if user_data:
                display_name = None

                # 如果是查询自己，则更新数据库中的昵称以匹配当前平台昵称
                if str(target_user_id) == str(event.get_sender_id()):
                    user_name = event.get_sender_name()
                    if user_data.get("nickname") != user_name:
                        await self.db.update_user_data(
                            target_user_id, nickname=user_name
                        )
                        user_data["nickname"] = user_name  # 同时更新本地副本

                # 如果有专门的昵称服务，则使用它，否则回退到数据库中的昵称
                nickname_api = shared_services.get("nickname_api")
                if nickname_api:
                    display_name = await nickname_api.get_nickname(target_user_id)

                if not display_name:
                    db_nickname = user_data.get("nickname")
                    user_id_str = user_data.get("user_id", target_user_id)
                    display_name = db_nickname or user_id_str

                # 对机器人自己的名称进行特殊覆盖
                if str(target_user_id) == "1902929802":
                    display_name = "菲比"

                title = (
                    "✨ 您的签到信息 ✨"
                    if str(target_user_id) == str(event.get_sender_id())
                    else f"✨ {display_name} 的签到信息 ✨"
                )

                fortune_text = ""
                if user_data.get("last_sign") == today_str:
                    fortune = user_data.get("last_fortune_result")
                    effect_desc = FORTUNE_EFFECTS.get(fortune, {}).get(
                        "description", "无特殊效果"
                    )
                    fortune_text = f"🔮 今日运势: 【{fortune or 'N/A'}】\n✨ 运势效果: {effect_desc}"
                else:
                    fortune_text = "🔮 今日运势: 尚未签到"

                if user_data.get("lucky_clover_buff_date") == today_str:
                    fortune_text += "\n🍀 幸运加持: 今日抽奖好运概率提升！"

                # 组装最终的结果字符串
                result_text = (
                    f"{title}\n"
                    f"--------------------\n"
                    f"👤 昵称: {display_name}\n"
                    f"💳 用户ID: {user_data['user_id']}\n"
                    f"💰 当前金币: {user_data['coins']}\n"
                    f"📅 累计签到: {user_data['total_days']} 天\n"
                    f"🔄 连续签到: {user_data['continuous_days']} 天\n"
                    f"⏰ 上次签到: {user_data['last_sign']}\n"
                    f"--------------------\n"
                    f"{fortune_text}"
                )
                yield event.plain_result(result_text)

            else:
                # --- 5. 处理用户无数据的情况 ---
                # 如果查询的是机器人，则显示特殊消息
                if str(target_user_id) == "1902929802":
                    not_found_msg = "菲比不需要签到哦~"
                else:
                    # 对自己查询和查询他人使用不同的消息
                    is_self_query = str(target_user_id) == str(event.get_sender_id())
                    not_found_msg = (
                        "你还没有签到过哦，发送“/签到”来开始吧！"
                        if is_self_query
                        else f"用户 {target_user_id} 还没有签到记录哦。"
                    )

                yield event.plain_result(not_found_msg)

            # 停止事件传播，防止被其他插件或 LLM 继续处理
            event.stop_event()

        except Exception as e:
            logger.error(f"执行/查询命令时发生错误: {e}", exc_info=True)
            yield event.plain_result("查询失败了，请稍后再试或联系管理员。")

    # ---------------------------------------------------------------------------------
    # 抽奖逻辑重构
    # ---------------------------------------------------------------------------------

    async def _validate_lottery_attempt(
        self, event: AstrMessageEvent, bet_amount_str: str
    ) -> tuple[str | None, dict | None, int | None]:
        """
        验证抽奖尝试的有效性，包括参数、用户状态、次数和余额。
        返回 (错误信息, 更新后的用户数据, 下注金额)。如果验证通过，错误信息为None。
        """
        # 1. 解析和验证下注金额
        if not bet_amount_str:
            return (
                f"请输入您要抽奖的金额！\n用法: `抽奖 <金额>`\n(最低: {MIN_LOTTERY_BET}, 最高: {MAX_LOTTERY_BET})",
                None,
                None,
            )
        try:
            bet_amount = int(bet_amount_str)
            if not (MIN_LOTTERY_BET <= bet_amount <= MAX_LOTTERY_BET):
                return (
                    f"下注金额超出范围！\n单次抽奖金额必须在 {MIN_LOTTERY_BET} 到 {MAX_LOTTERY_BET} 之间。",
                    None,
                    None,
                )
        except ValueError:
            return "请输入一个有效的数字作为抽奖金额！", None, None

        # 2. 获取用户数据并检查是否存在
        user_id = event.get_sender_id()
        user_data = await self.db.get_user_data(user_id)
        if not user_data:
            return "您还没有签到记录，请先“签到”一次后再来抽奖哦~", None, None

        # 3. 每日状态重置 (抽奖次数、圣辉次数等)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        if user_data.get("last_lottery_date") != today_str:
            await self.db.update_user_data(
                user_id,
                lottery_count=0,
                extra_lottery_attempts=0,
                holy_light_uses_today=0,
            )
            user_data = await self.db.get_user_data(user_id)

        # 4. 检查并消耗抽奖道具 (抽奖券、四叶草)
        consume_msg = await self._check_and_consume_lottery_items(event, user_data)
        if consume_msg:
            # 道具消耗会影响用户数据（如金币、抽奖次数），所以需要重新获取
            await event.send(event.plain_result(consume_msg))
            user_data = await self.db.get_user_data(user_id)

        # 5. 检查抽奖次数
        lottery_count = user_data.get("lottery_count", 0)
        extra_attempts = user_data.get("extra_lottery_attempts", 0)
        total_attempts_today = MAX_LOTTERY_PER_DAY + extra_attempts
        if lottery_count >= total_attempts_today:
            return (
                f"您今天的抽奖次数已用完 ({lottery_count}/{total_attempts_today})，明天再来吧！",
                user_data,
                bet_amount,
            )

        # 6. 检查金币余额
        current_coins = user_data.get("coins", 0)
        if current_coins < bet_amount:
            return (
                f"金币不足！本次抽奖需要 {bet_amount} 金币，您当前只有 {current_coins} 金币。",
                user_data,
                bet_amount,
            )

        return None, user_data, bet_amount

    def _apply_lottery_buffs(
        self, user_data: dict
    ) -> tuple[list[int], float, float, str, str]:
        """
        根据用户当前的运势和道具，计算最终的抽奖权重、倍率等参数。
        返回 (生效权重列表, 生效奖池命中率, 生效倍率修正, Buff信息文本, 用于日志的运势)
        """
        effective_tier_weights = TIER_WEIGHTS.copy()
        effective_jackpot_chance = JACKPOT_WIN_CHANCE
        effective_prize_mult_mod = 1.0
        fortune_buff_message = ""
        current_fortune_for_log = "未签到"
        today_str = datetime.date.today().strftime("%Y-%m-%d")

        # 1. 计算运势效果
        if user_data.get("last_sign") == today_str:
            user_fortune = user_data.get("last_fortune_result")
            if user_fortune:
                current_fortune_for_log = user_fortune
                if user_fortune in FORTUNE_EFFECTS:
                    effect = FORTUNE_EFFECTS[user_fortune]

                    # 圣辉特殊逻辑：检查使用次数
                    if effect.get("special_effect") == "holy_light":
                        holy_light_uses = user_data.get("holy_light_uses_today", 0)
                        if holy_light_uses < 3:
                            fortune_buff_message = f"\n🔮 今日运势【{user_fortune}】效果发动 ({holy_light_uses + 1}/3)：\n{effect['description']}"
                            effective_jackpot_chance *= effect.get(
                                "jackpot_chance_mult", 1.0
                            )
                            effective_prize_mult_mod = effect.get("prize_mult_mod", 1.0)
                            # 权重调整
                            positive_indices = [
                                TIER_NAME_TO_INDEX[name]
                                for name in [
                                    "💥传说大奖💥",
                                    "🎉稀有大奖🎉",
                                    "✨幸运奖励✨",
                                ]
                            ]
                            negative_indices = [
                                TIER_NAME_TO_INDEX[name]
                                for name in ["😅普通结果😅", "💨血本无归💨"]
                            ]
                            weight_to_redistribute = sum(
                                effective_tier_weights[i] for i in negative_indices
                            )
                            original_positive_weights = [
                                TIER_WEIGHTS[i] for i in positive_indices
                            ]
                            total_positive_base_weight = sum(original_positive_weights)
                            if total_positive_base_weight > 0:
                                for i, base_weight in zip(
                                    positive_indices, original_positive_weights
                                ):
                                    share = base_weight / total_positive_base_weight
                                    effective_tier_weights[i] += (
                                        weight_to_redistribute * share
                                    )
                            for i in negative_indices:
                                effective_tier_weights[i] = 0
                        else:
                            fortune_buff_message = f"\n🔮 今日运势【{user_fortune}】效果已用尽 (3/3)，本次抽奖无加成。"
                    else:  # 其他运势通用逻辑
                        fortune_buff_message = f"\n🔮 今日运势【{user_fortune}】效果发动：\n{effect['description']}"
                        effective_jackpot_chance *= effect.get(
                            "jackpot_chance_mult", 1.0
                        )
                        effective_prize_mult_mod = effect.get("prize_mult_mod", 1.0)
                        tier_shift = effect.get("tier_shift")
                        if tier_shift:
                            from_idx, to_idx = (
                                TIER_NAME_TO_INDEX[tier_shift["from"]],
                                TIER_NAME_TO_INDEX[tier_shift["to"]],
                            )
                            actual_amount = min(
                                effective_tier_weights[from_idx], tier_shift["amount"]
                            )
                            effective_tier_weights[from_idx] -= actual_amount
                            effective_tier_weights[to_idx] += actual_amount

        # 2. 计算幸运四叶草效果
        if user_data.get("lucky_clover_buff_date") == today_str:
            fortune_buff_message += "\n🍀 幸运四叶草效果发动：好运概率提升！"
            from_normal_idx, from_loss_idx = (
                TIER_NAME_TO_INDEX["😅普通结果😅"],
                TIER_NAME_TO_INDEX["💨血本无归💨"],
            )
            to_rare_idx, to_lucky_idx = (
                TIER_NAME_TO_INDEX["🎉稀有大奖🎉"],
                TIER_NAME_TO_INDEX["✨幸运奖励✨"],
            )
            actual_move_1 = min(effective_tier_weights[from_normal_idx], 5)
            effective_tier_weights[from_normal_idx] -= actual_move_1
            effective_tier_weights[to_lucky_idx] += actual_move_1
            actual_move_2 = min(effective_tier_weights[from_loss_idx], 3)
            effective_tier_weights[from_loss_idx] -= actual_move_2
            effective_tier_weights[to_rare_idx] += actual_move_2

        return (
            effective_tier_weights,
            effective_jackpot_chance,
            effective_prize_mult_mod,
            fortune_buff_message,
            current_fortune_for_log,
        )

    async def _perform_lottery_draw(
        self,
        event: AstrMessageEvent,
        bet_amount: int,
        tier_weights: list[int],
        jackpot_chance: float,
        prize_mod: float,
    ) -> tuple[dict, int, int, str, int]:
        """
        执行核心的抽奖和奖池计算。
        返回 (抽中的奖项, 常规奖金, 奖池奖金, 奖池信息文本, 最终奖池金额)
        """
        # 1. 抽奖，决定基础奖励
        chosen_tier = random.choices(LOTTERY_TIERS, weights=tier_weights, k=1)[0]
        min_mult, max_mult = chosen_tier[2]
        final_multiplier = random.uniform(min_mult, max_mult) * prize_mod
        prize_from_spin = int(bet_amount * final_multiplier)

        # 2. 奖池计算
        current_pool = int(
            await self.db.get_setting("jackpot_pool", str(JACKPOT_INITIAL_AMOUNT))
        )
        final_pool_amount = current_pool
        jackpot_won_amount = 0
        jackpot_message = ""
        pool_needs_update = False

        if random.random() < jackpot_chance:  # 命中奖池
            jackpot_won_amount = current_pool
            jackpot_message = (
                f"\n--------------------\n"
                f"🎊🎊🎊 终极大奖 🎊🎊🎊\n"
                f"难以置信！神迹降临！您额外命中了价值 {jackpot_won_amount} 金币的超级奖池！"
            )
            final_pool_amount = JACKPOT_INITIAL_AMOUNT  # 重置奖池
            pool_needs_update = True
            await self.db.log_jackpot_win(
                event.get_sender_id(), event.get_sender_name(), jackpot_won_amount
            )
            # 触发成就
            if (
                shared_services
                and bet_amount > 0
                and jackpot_won_amount >= bet_amount * 100
            ):
                achievement_api = shared_services.get("achievement_api")
                if achievement_api:
                    await achievement_api.unlock_achievement(
                        user_id=event.get_sender_id(),
                        achievement_id="lottery_jackpot_100x",
                        event=event,
                    )
        elif bet_amount > prize_from_spin:  # 未命中奖池且亏损，部分亏损注入奖池
            coins_lost = bet_amount - prize_from_spin
            pool_add = int(coins_lost * 0.01)
            final_pool_amount += pool_add
            pool_needs_update = True

        if pool_needs_update:
            await self.db.set_setting("jackpot_pool", str(final_pool_amount))

        return (
            chosen_tier,
            prize_from_spin,
            jackpot_won_amount,
            jackpot_message,
            final_pool_amount,
        )

    # ---------------------------------------------------------------------------------
    # 重构后精简的 `lottery` 主函数
    # ---------------------------------------------------------------------------------

    @filter.command("抽奖", alias={"lottery"})
    async def lottery(self, event: AstrMessageEvent, bet_amount_str: str = ""):
        try:
            # 步骤 1: 验证抽奖的先决条件 (金额、次数、余额等)
            error_msg, user_data, bet_amount = await self._validate_lottery_attempt(
                event, bet_amount_str
            )
            if error_msg:
                yield event.plain_result(error_msg)
                return

            # 步骤 2: 基于用户运势和道具计算生效的抽奖参数
            weights, jackpot_chance, prize_mod, buff_msg, fortune_log = (
                self._apply_lottery_buffs(user_data)
            )

            # 步骤 3: 执行抽奖，获取奖励和奖池结果
            (
                tier,
                spin_prize,
                jackpot_prize,
                jackpot_msg,
                final_pool,
            ) = await self._perform_lottery_draw(
                event, bet_amount, weights, jackpot_chance, prize_mod
            )

            # 步骤 4: 结算，更新数据库并生成最终消息
            # a. 计算金币和次数变化
            current_coins = user_data.get("coins", 0)
            lottery_count = user_data.get("lottery_count", 0)
            total_prize = spin_prize + jackpot_prize
            new_coins = current_coins - bet_amount + total_prize

            # b. 如果使用了圣辉，增加其计数器
            holy_light_uses_increment = (
                1
                if user_data.get("last_fortune_result") == "圣辉"
                and user_data.get("holy_light_uses_today", 0) < 3
                else 0
            )
            new_holy_light_uses = (
                user_data.get("holy_light_uses_today", 0) + holy_light_uses_increment
            )

            # c. 更新数据库
            await self.db.update_user_data(
                event.get_sender_id(),
                coins=new_coins,
                lottery_count=lottery_count + 1,
                last_lottery_date=datetime.date.today().strftime("%Y-%m-%d"),
                holy_light_uses_today=new_holy_light_uses,
            )

            # d. 记录日志
            await self.db.log_coins(event.get_sender_id(), -bet_amount, "抽奖花费")
            if spin_prize > 0:
                await self.db.log_coins(
                    event.get_sender_id(), spin_prize, "抽奖常规奖励"
                )
            if jackpot_prize > 0:
                await self.db.log_coins(
                    event.get_sender_id(), jackpot_prize, "🎉赢得奖池大奖！"
                )

            total_multiplier = total_prize / bet_amount if bet_amount > 0 else 0
            await self.db.log_lottery_play(
                event.get_sender_id(),
                bet=bet_amount,
                prize=total_prize,
                multiplier=total_multiplier,
                jackpot=(jackpot_prize > 0),
                fortune=fortune_log,
            )
            # 检查总倍率是否 > 0 且 < 0.01
            if 0 < total_multiplier < 0.01:
                achievement_api = shared_services.get("achievement_api")
                if achievement_api:
                    await achievement_api.unlock_achievement(
                        user_id=event.get_sender_id(),
                        achievement_id="lottery_near_zero_multiplier",
                        event=event,  # 这是“与空气斗智斗勇”的ID
                    )
            # e. 准备并发送最终消息
            display_name = user_data.get("nickname", event.get_sender_name())
            remaining_attempts = (
                MAX_LOTTERY_PER_DAY + user_data.get("extra_lottery_attempts", 0)
            ) - (lottery_count + 1)
            final_message_from_tier = tier[3].format(
                multiplier=(spin_prize / bet_amount if bet_amount > 0 else 0)
            )

            result_msg = (
                f"👤 {display_name}的抽奖:\n"
                f"🎲 命运轮盘转动... 🎲"
                f"{buff_msg}\n"
                f"--------------------\n"
                f"您抽中了: {tier[0]}\n"
                f"{final_message_from_tier}\n"
                f"您投入了 {bet_amount} 金币，通过本次轮盘获得 {spin_prize} 金币！"
                f"{jackpot_msg}\n"
                f"--------------------\n"
                f"💰 当前总金币: {new_coins}\n"
                f"🌊 当前奖池累积: {final_pool} 金币\n"
                f"今日剩余抽奖次数: {remaining_attempts}"
            )
            yield event.plain_result(result_msg)

        except Exception as e:
            logger.error(f"抽奖失败: {e}", exc_info=True)
            yield event.plain_result("抽奖机好像坏掉了喵~ 请联系管理员。")

    @filter.command("梭哈", alias={"allin"})
    async def allin(self, event: AstrMessageEvent):
        """
        使用全部金币进行抽奖（已适配道具消耗逻辑）。
        """
        try:
            user_id = event.get_sender_id()

            # --- 在调用道具函数前，先获取一次用户数据 ---
            user_data = await self.db.get_user_data(user_id)
            if not user_data:
                yield event.plain_result("您还没有签到记录，无法进行梭哈。")
                return

            # 步骤 1: 将获取到的 user_data 传递给道具消耗函数
            consume_msg = await self._check_and_consume_lottery_items(event, user_data)
            if consume_msg:
                yield event.plain_result(consume_msg)

            # 步骤 2: 在道具结算完毕后，【必须】重新获取用户最新的数据
            # 因为道具消耗会改变金币、抽奖次数等
            user_data_after_consume = await self.db.get_user_data(user_id)
            if not user_data_after_consume:
                # 理论上不会发生，但作为安全检查
                yield event.plain_result("处理道具后出错，找不到您的账户。")
                return

# 步骤 3: 获取道具消耗后【剩余】的金币
            coins_after_consume = user_data_after_consume.get("coins", 0)
            if coins_after_consume <= 0:
                yield event.plain_result(
                    "您没有金币可以梭哈了！(可能因为使用道具后余额不足)"
                )
                return

            # --- ✨ 核心修改点：取 拥有金币 和 最大上限 中的较小值 ---
            actual_bet = min(coins_after_consume, MAX_LOTTERY_BET)

            # 步骤 4: 调用 lottery 函数，传入限制后的金额
            async for result in self.lottery(event, str(actual_bet)):
                yield result

        except Exception as e:
            logger.error(f"梭哈失败: {e}", exc_info=True)
            yield event.plain_result("梭哈好像坏掉了喵~ 请联系管理员。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置奖池", alias={"手动重置奖池"})
    async def manual_reset_jackpot(self, event: AstrMessageEvent):
        """
        [管理员命令] 手动将奖池金额重置为初始值。
        """
        try:
            await self.db.set_setting("jackpot_pool", str(JACKPOT_INITIAL_AMOUNT))
            logger.info(f"管理员 ({event.get_sender_id()}) 手动重置了奖池。")
            yield event.plain_result(
                f"✅ 操作成功！\n奖池金额已手动重置为初始值: {JACKPOT_INITIAL_AMOUNT}"
            )
        except Exception as e:
            logger.error(f"手动重置奖池失败: {e}", exc_info=True)
            yield event.plain_result("❌ 操作失败，发生内部错误。请检查日志。")

        event.stop_event()

    @filter.command("抽奖详细", alias={"抽奖概率"})
    async def lottery_details(self, event: AstrMessageEvent):
        """显示当前抽奖的详细概率分布和期望值"""
        try:
            current_pool = int(
                await self.db.get_setting("jackpot_pool", str(JACKPOT_INITIAL_AMOUNT))
            )
            total_ev, tier_details = self._calculate_lottery_ev()

            details_text = ["--- 🎲 抽奖概率详细信息 🎲 ---"]
            details_text.append(f"🌊 当前奖池金额: {current_pool} 金币")
            details_text.append(
                f"🎯 基础命中概率: {JACKPOT_WIN_CHANCE * 100:.3f}% (可能受每日运势影响)"
            )
            details_text.append("--------------------")

            for detail in tier_details:
                name, prob_percent, min_m, max_m = (
                    detail["name"],
                    detail["probability"] * 100,
                    detail["mult_range"][0],
                    detail["mult_range"][1],
                )
                details_text.append(
                    f"{name}: {prob_percent:.2f}% 概率, 倍率 [{min_m:.2f} ~ {max_m:.2f}]"
                )

            details_text.append("--------------------")
            details_text.append(f"📈 总期望倍率 (不含奖池和运势): {total_ev:.4f}x")

            yield event.plain_result("\n".join(details_text))
            event.stop_event()
        except Exception as e:
            logger.error(f"获取抽奖详情失败: {e}", exc_info=True)
            yield event.plain_result("获取抽奖详情失败了喵~")

    @filter.command("排行", alias={"财富榜", "金币排行", "ranking"})
    async def ranking(self, event: AstrMessageEvent):
        """查看签到排行榜"""
        try:
            ranking_data = await self.db.get_ranking(limit=10)
            header = "🏆 福布斯财富榜 🏆\n--------------------\n"
            if not ranking_data:
                yield event.plain_result("现在还没有人签到哦，快来争做第一名！")
                event.stop_event()
                return

            # 1. 尝试获取 nickname_api
            nickname_api = shared_services.get("nickname_api")
            display_names = {}
            if nickname_api:
                # 2. 批量获取所有昵称，API内部已处理好所有回退逻辑
                user_ids_on_ranking = [row["user_id"] for row in ranking_data]
                display_names = await nickname_api.get_nicknames_batch(
                    user_ids_on_ranking
                )

            entries = []
            for i, row in enumerate(ranking_data, 1):
                user_id = row["user_id"]
                coins = row["coins"]
                total_days = row["total_days"]

                # 3. 直接从结果中取用，无需再写 or a or b 的复杂逻辑
                # 如果API不存在，display_names为空字典，.get默认返回None，
                # 最终会回退到row['nickname']或user_id，完全兼容
                display_name = display_names.get(user_id) or row["nickname"] or user_id

                entries.append(
                    f"🏅 第 {i} 名: {display_name}    {coins} 金币 (签到{total_days}天)"
                )

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)
            event.stop_event()
        except Exception as e:
            logger.error(f"获取排行榜失败: {e}", exc_info=True)
            yield event.plain_result("排行榜不见了喵~")

    @filter.command("转账", alias={"v"})
    async def transfer_coins(self, event: AstrMessageEvent):
        """向其他用户转账金币，支持@和用户ID（带阶梯税率和新手保护）"""
        try:
            sender_id = event.get_sender_id()
            sender_name = event.get_sender_name()

            recipient_id = None
            amount_str = ""

            for component in event.message_obj.message:
                if isinstance(component, Comp.At):
                    if not recipient_id:
                        recipient_id = component.qq
                elif isinstance(component, Comp.Plain):
                    amount_str += component.text.strip()

            amount_match = re.search(r"\d+", amount_str)
            amount = int(amount_match.group(0)) if amount_match else None

            if not recipient_id or amount is None:
                yield event.plain_result(
                    "❌ 命令格式错误！\n正确用法: `/转账 @用户 <金额>`"
                )
                event.stop_event()
                return

            if str(sender_id) == str(recipient_id):
                yield event.plain_result("😅 不能给自己转账哦！")
                event.stop_event()
                return

            if amount <= 0:
                yield event.plain_result("❌ 转账金额必须是大于0的整数！")
                event.stop_event()
                return

            sender_data = await self.db.get_user_data(sender_id)
            if not sender_data:
                yield event.plain_result("请先签到一次，创建您的账户。")
                event.stop_event()
                return

            recipient_data = await self.db.get_user_data(recipient_id)
            if not recipient_data:
                yield event.plain_result(
                    f"❌ 找不到用户 {recipient_id}。\n请确认对方已经签到过。"
                )
                event.stop_event()
                return

            sender_coins = sender_data.get("coins", 0)

            if sender_coins < 1000:
                fee_rate = 0.0  # 新手保护，免手续费
            elif sender_coins < 10000:
                fee_rate = 0.10
            elif sender_coins < 50000:
                fee_rate = 0.15
            elif sender_coins < 200000:
                fee_rate = 0.20
            elif sender_coins < 500000:
                fee_rate = 0.25
            else:  #  >= 100000
                fee_rate = 0.30

            # 如果费率不为0，才计算手续费，且最低为1
            fee = 0
            if fee_rate > 0:
                fee = max(1, int(amount * fee_rate))

            total_cost = amount + fee

            fee_message = ""
            if fee > 0:
                fee_rate_percent = int(fee_rate * 100)
                fee_message = f"手续费({fee_rate_percent}%，最低1金币): {fee} 金币\n"
            else:
                fee_message = "本次转账免手续费\n"

            if sender_coins < total_cost:
                yield event.plain_result(
                    f"💸 金币不足！\n"
                    f"转账 {amount} 金币\n"
                    f"{fee_message}"  # 使用动态生成的消息
                    f"总计需要: {total_cost} 金币\n"
                    f"您当前只有 {sender_coins} 金币。"
                )
                event.stop_event()
                return

            recipient_name = recipient_data.get("nickname") or recipient_id

            new_sender_coins = sender_coins - total_cost
            new_recipient_coins = recipient_data.get("coins", 0) + amount
            await self.db.update_user_data(
                sender_id, coins=new_sender_coins, nickname=sender_name
            )
            await self.db.update_user_data(recipient_id, coins=new_recipient_coins)

            await self.db.log_coins(sender_id, -amount, f"转账给用户 {recipient_id}")

            if fee > 0:
                fee_rate_percent = int(fee_rate * 100)
                await self.db.log_coins(
                    sender_id, -fee, f"转账手续费 ({fee_rate_percent}%)"
                )

            await self.db.log_coins(
                recipient_id, amount, f"收到来自用户 {sender_id} 的转账"
            )
            await self.db.log_transfer(
                sender_id, sender_name, recipient_id, recipient_name, amount
            )

            success_fee_message = ""
            if fee > 0:
                fee_rate_percent = int(fee_rate * 100)
                success_fee_message = (
                    f"(手续费: {fee} 金币, 税率: {fee_rate_percent}%)\n"
                )
            else:
                success_fee_message = "(新手保护期，免除手续费)\n"

            yield event.plain_result(
                f"✅ 转账成功！\n"
                f"您向用户 {recipient_name} 转账了 {amount} 金币。\n"
                f"{success_fee_message}"  # 使用动态成功的消息
                f"💰 您当前的金币: {new_sender_coins}"
            )
            event.stop_event()
        except Exception as e:
            logger.error(f"转账失败: {e}", exc_info=True)
            yield event.plain_result("转账时发生内部错误，请联系管理员。")

    @filter.command(
        "救济金",
        alias={"低保", "v我点", "救救我", "救救孩子", "分点钱", "vivo50", "v我50"},
    )
    async def relief_fund(self, event: AstrMessageEvent):
        """每日一次，从Bot（公共银行）处领取救济金。"""
        try:
            user_id = event.get_sender_id()
            user_name = event.get_sender_name()
            bot_id = event.message_obj.self_id
            today_str = datetime.date.today().strftime("%Y-%m-%d")

            # 1. 检查用户账户是否存在
            user_data = await self.db.get_user_data(user_id)
            if not user_data:
                yield event.plain_result("您还没有签到记录，请先“签到”一次再来领取哦~")
                return

            # 2. 检查今天是否已经领取过
            if user_data.get("last_relief_fund_date") == today_str:
                yield event.plain_result("您今天已经领取过菲比的救济金了，明天再来吧！")
                return

            # 3. 检查Bot（银行）是否有足够的资金
            bot_coins = await self.api.get_coins(bot_id)
            if bot_coins < 5000:  # 银行至少需要有5000金币才能发放最低50的救济金
                yield event.plain_result("抱歉，菲比的钱包空空...暂时无法帮助你...")
                return

            # 4. 计算救济金金额
            min_amount = 50
            if bot_coins < 100000:
                max_amount = int(bot_coins * 0.01)  # 最多是银行余额的1%
            else:
                max_amount = 1000
            # 确保最大值不小于最小值
            if max_amount < min_amount:
                max_amount = min_amount

            relief_amount = random.randint(min_amount, max_amount)

            # 5. 执行转账操作 (使用 EconomyAPI)
            # 从 bot 账户扣钱
            bot_transfer_success = await self.api.add_coins(
                bot_id, -relief_amount, f"向用户 {user_id} 发放救济金"
            )
            if not bot_transfer_success:
                # 理论上前面已经检查过余额，但为了安全起见
                logger.error(f"发放救济金失败：扣除Bot({bot_id})余额时失败。")
                yield event.plain_result(
                    "系统内部错误，菲比发放救济金失败，请联系管理员。"
                )
                return

            # 给用户加钱
            user_transfer_success = await self.api.add_coins(
                user_id, relief_amount, "领取每日救济金"
            )

            # 6. 更新用户的领取记录
            await self.db.update_user_data(user_id, last_relief_fund_date=today_str)

            # 7. 发送成功消息
            new_user_coins = user_data.get("coins", 0) + relief_amount
            yield event.plain_result(
                f"✨ 每日菲比馈赠已到账！ ✨\n"
                f"--------------------\n"
                f"你从菲比那获得了 {relief_amount} 金币的救济金。\n"
                f"💰 余额: {new_user_coins}"
            )

        except Exception as e:
            logger.error(f"领取救济金失败: {e}", exc_info=True)
            yield event.plain_result("领取救济金时发生错误，请联系管理员。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("系统注册", alias={"adminreg"})
    async def admin_register_user(self, event: AstrMessageEvent, target: str):
        """
        [管理员] 为指定用户或Bot在经济系统中手动创建一个账户。
        如果账户已存在，则不会进行任何操作。
        """
        target_id = None
        # 判断目标是 'bot' 还是一个具体的用户ID
        if target.lower() == "bot":
            target_id = event.message_obj.self_id
        elif target.isdigit():
            target_id = target
        else:
            yield event.plain_result(
                "❌ 目标格式错误。\n用法: `/系统注册 <用户ID或'bot'>`"
            )
            return

        # 检查账户是否已存在
        existing_data = await self.db.get_user_data(target_id)
        if existing_data:
            display_name = existing_data.get("nickname") or target_id
            yield event.plain_result(
                f"ℹ️ 用户 {display_name} 已存在于系统中，无需重复注册。"
            )
            return

        # 尝试从 nickname_api 获取昵称
        nickname_api = shared_services.get("nickname_api")
        display_name = target_id  # 默认显示ID
        if nickname_api:
            custom_nickname = await nickname_api.get_nickname(target_id)
            if custom_nickname:
                display_name = custom_nickname

        # 创建一个初始的、干净的账户数据
        # 注意：签到天数等信息保持为0，金币也为0
        await self.db.update_user_data(
            user_id=target_id,
            nickname=display_name,
            coins=0,
            total_days=0,
            continuous_days=0,
            last_sign=None,  # 未签到状态
        )

        logger.info(
            f"管理员 {event.get_sender_id()} 为 {target_id} 创建了经济账户，昵称为 {display_name}"
        )
        yield event.plain_result(
            f"✅ 成功！\n已为用户 {display_name} ({target_id}) 在经济系统中创建了一个初始账户。"
        )

    @filter.command("安全转账", alias={"sv"})
    async def safe_transfer_coins(self, event: AstrMessageEvent):
        """
        向其他用户进行安全转账。
        用户指定的金额是【含税】的总扣款额，系统会自动计算收款方实际所得。
        """
        try:
            sender_id = event.get_sender_id()
            sender_name = event.get_sender_name()

            recipient_id = None
            amount_str = ""

            for component in event.message_obj.message:
                if isinstance(component, Comp.At):
                    if not recipient_id:
                        recipient_id = component.qq
                elif isinstance(component, Comp.Plain):
                    amount_str += component.text.strip()

            amount_match = re.search(r"\d+", amount_str)
            # 这个金额是用户愿意付出的总成本
            total_deduction = int(amount_match.group(0)) if amount_match else None

            if not recipient_id or total_deduction is None:
                yield event.plain_result(
                    "❌ 命令格式错误！\n正确用法: `/安全转账 <总金额> @用户`"
                )
                return

            if sender_id == recipient_id:
                yield event.plain_result("😅 不能给自己转账哦！")
                return

            if total_deduction <= 0:
                yield event.plain_result("❌ 转账总额必须是大于0的整数！")
                return

            sender_data = await self.db.get_user_data(sender_id)
            if not sender_data:
                yield event.plain_result("请先签到一次，创建您的账户。")
                return

            recipient_data = await self.db.get_user_data(recipient_id)
            if not recipient_data:
                yield event.plain_result(
                    f"❌ 找不到用户 {recipient_id}。\n请确认对方已经签到过。"
                )
                return

            sender_coins = sender_data.get("coins", 0)

            # --- 1：安全检查 ---
            # 直接检查用户余额是否足够支付他想花费的总金额
            if sender_coins < total_deduction:
                yield event.plain_result(
                    f"💸 金币不足！\n"
                    f"您试图转出总计 {total_deduction} 金币，但您当前只有 {sender_coins} 金币。"
                )
                return

            # (税率计算逻辑保持不变)
            if sender_coins < 1000:
                fee_rate = 0.0
            elif sender_coins < 10000:
                fee_rate = 0.10
            elif sender_coins < 50000:
                fee_rate = 0.15
            elif sender_coins < 200000:
                fee_rate = 0.20
            elif sender_coins < 500000:
                fee_rate = 0.25
            else:
                fee_rate = 0.30

            # --- 反推金额和手续费 ---
            amount_to_recipient = 0
            fee = 0
            if fee_rate > 0:
                # 根据公式 T = A * (1 + R) 反推 A (收款方所得)
                # A = T / (1 + R)
                amount_to_recipient = int(total_deduction / (1 + fee_rate))
                fee = total_deduction - amount_to_recipient
                # 确保在有税率的情况下，手续费至少为1
                if fee == 0 and amount_to_recipient > 0:
                    fee = 1
                    amount_to_recipient -= 1
            else:  # 无手续费
                amount_to_recipient = total_deduction
                fee = 0

            # 如果计算后收款方所得小于等于0，则认为转账无意义
            if amount_to_recipient <= 0:
                yield event.plain_result(
                    f"❌ 转账总额 {total_deduction} 过低，在扣除手续费后收款方无法收到任何金币。"
                )
                return

            # (后续数据库操作和日志记录)
            recipient_name = recipient_data.get("nickname") or recipient_id

            new_sender_coins = sender_coins - total_deduction
            new_recipient_coins = recipient_data.get("coins", 0) + amount_to_recipient
            await self.db.update_user_data(
                sender_id, coins=new_sender_coins, nickname=sender_name
            )
            await self.db.update_user_data(recipient_id, coins=new_recipient_coins)

            # 记录日志时，分别记录转给对方的金额和手续费
            await self.db.log_coins(
                sender_id, -amount_to_recipient, f"安全转账给用户 {recipient_id}"
            )
            if fee > 0:
                fee_rate_percent = int(fee_rate * 100)
                await self.db.log_coins(
                    sender_id, -fee, f"安全转账手续费 ({fee_rate_percent}%)"
                )

            await self.db.log_coins(
                recipient_id,
                amount_to_recipient,
                f"收到来自用户 {sender_id} 的安全转账",
            )
            await self.db.log_transfer(
                sender_id,
                sender_name,
                recipient_id,
                recipient_name,
                amount_to_recipient,
            )

            # (构建成功的返回消息)
            fee_rate_percent = int(fee_rate * 100)
            success_fee_message = (
                f"(手续费: {fee} 金币, 税率: {fee_rate_percent}%)\n"
                if fee > 0
                else "(新手保护期，免除手续费)\n"
            )

            yield event.plain_result(
                f"✅ 安全转账成功！\n"
                f"您总计消费: {total_deduction} 金币。\n"
                f"--------------------\n"
                f"收款用户 {recipient_name} 获得了 {amount_to_recipient} 金币。\n"
                f"{success_fee_message}"
                f"💰 您剩余的金币: {new_sender_coins}"
            )

        except Exception as e:
            logger.error(f"安全转账失败: {e}", exc_info=True)
            yield event.plain_result("安全转账时发生内部错误，请联系管理员。")

    @filter.command("转账记录", alias={"交易记录", "收支记录"})
    async def transfer_history(self, event: AstrMessageEvent):
        """查看最近10条转账记录"""
        try:
            user_id = event.get_sender_id()
            history = await self.db.get_transfer_history(user_id, limit=15)

            header = "📜 您最近的15条转账记录 📜\n--------------------\n"
            if not history:
                yield event.plain_result(header + "您还没有任何转账记录。")
                return

            entries = []
            for record in history:
                dt_object = datetime.datetime.fromisoformat(record["timestamp"])
                formatted_time = dt_object.strftime("%m-%d %H:%M")
                # 判断是转出还是转入
                if record["sender_id"] == user_id:
                    # 这是我发出的转账
                    recipient_display = (
                        record["recipient_name"] or record["recipient_id"]
                    )
                    entries.append(
                        f"[{formatted_time}] 🔴 转给 {recipient_display} {record['amount']} 金币 "
                    )
                else:
                    # 这是我收到的转账
                    sender_display = record["sender_name"] or record["sender_id"]
                    entries.append(
                        f"[{formatted_time}] 🟢 收到 {sender_display} {record['amount']} 金币"
                    )

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(f"获取转账记录失败: {e}", exc_info=True)
            yield event.plain_result("查询转账记录时出错，请联系管理员。")

    @filter.command("转入记录", alias={"收款记录"})
    async def incoming_history(self, event: AstrMessageEvent):
        """只查看收款记录"""
        try:
            user_id = event.get_sender_id()
            history = await self.db.get_incoming_transfers(user_id, limit=15)

            header = "📜 您最近的15条收款记录 📜\n--------------------\n"
            if not history:
                yield event.plain_result(header + "您还没有任何收款记录。")
                return

            entries = []
            for record in history:
                dt_object = datetime.datetime.fromisoformat(record["timestamp"])
                formatted_time = dt_object.strftime("%m-%d %H:%M")
                sender_display = record["sender_name"] or record["sender_id"]
                entries.append(
                    f"[{formatted_time}] 🟢 收到 {sender_display} {record['amount']} 金币"
                )

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(f"获取转入记录失败: {e}", exc_info=True)
            yield event.plain_result("查询收款记录时出错，请联系管理员。")

    @filter.command("转出记录", alias={"付款记录"})
    async def outgoing_history(self, event: AstrMessageEvent):
        """只查看付款记录"""
        try:
            user_id = event.get_sender_id()
            history = await self.db.get_outgoing_transfers(user_id, limit=15)

            header = "📜 您最近的15条付款记录 📜\n--------------------\n"
            if not history:
                yield event.plain_result(header + "您还没有任何付款记录。")
                return

            entries = []
            for record in history:
                dt_object = datetime.datetime.fromisoformat(record["timestamp"])
                formatted_time = dt_object.strftime("%m-%d %H:%M")
                recipient_display = record["recipient_name"] or record["recipient_id"]
                entries.append(
                    f"[{formatted_time}] 🔴 转给 {recipient_display} {record['amount']} 金币 "
                )

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(f"获取转出记录失败: {e}", exc_info=True)
            yield event.plain_result("查询付款记录时出错，请联系管理员。")

    @filter.command("奖池信息", alias={"奖池", "奖池详细"})
    async def jackpot_info(self, event: AstrMessageEvent):
        """查看当前奖池累计金额"""
        try:
            current_pool = int(
                await self.db.get_setting("jackpot_pool", str(JACKPOT_INITIAL_AMOUNT))
            )
            result_text = (
                f"🌊 当前奖池累计金额 🌊\n"
                f"--------------------\n"
                f"💰 金币: {current_pool}\n"
                f"📜 中奖概率: {JACKPOT_WIN_CHANCE}"
            )
            yield event.plain_result(result_text)
            event.stop_event()
        except Exception as e:
            logger.error(f"获取奖池信息失败: {e}", exc_info=True)
            yield event.plain_result("获取奖池信息失败了喵~")

    @filter.command("获奖记录", alias={"jackpot", "中奖记录"})
    async def jackpot_history(self, event: AstrMessageEvent):
        """查看历史获得奖池的用户记录"""
        try:
            records = await self.db.get_jackpot_wins(limit=5)  # 最多显示最近5条
            header = "🏆 历史大奖赢家 (最近5条) 🏆\n--------------------\n"

            if not records:
                yield event.plain_result(header + "目前还没有人赢得过奖池大奖哦！")
                event.stop_event()
                return

            entries = []
            for record in records:
                # 数据库返回的是 UTC 时间，我们格式化一下
                dt_object = datetime.datetime.fromisoformat(record["timestamp"])
                dt_object_utc8 = dt_object + datetime.timedelta(hours=0)
                formatted_time = dt_object_utc8.strftime("%m-%d %H:%M")
                entries.append(
                    f"[{formatted_time}] 幸运儿 {record['nickname']} 赢得了 {record['amount']} 金币！"
                )

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)
            event.stop_event()
        except Exception as e:
            logger.error(f"获取获奖记录失败: {e}", exc_info=True)
            yield event.plain_result("获取获奖记录失败了喵~")

    @filter.command("抽奖记录", alias={"lottery_history", "lotteryhistory"})
    async def lottery_history(self, event: AstrMessageEvent):
        """查看最近10条抽奖记录"""
        try:
            user_id = event.get_sender_id()
            # 3. 将记录数量限制改为10
            history = await self.db.get_lottery_history(user_id, limit=10)

            header = "📜 您最近的10条抽奖记录 📜\n--------------------\n"
            if not history:
                yield event.plain_result(header + "您还没有任何抽奖记录。")
                return

            entries = []
            for record in history:
                # 1. 解析时间字符串并转换为 UTC+8
                dt_object = datetime.datetime.fromisoformat(record["timestamp"])
                dt_object_utc8 = dt_object + datetime.timedelta(hours=8)
                formatted_time = dt_object_utc8.strftime("%m-%d %H:%M")

                bet = record["bet_amount"]
                prize = record["prize_won"]
                multiplier = record["multiplier"]

                # 判断输赢的图标
                if record["is_jackpot"]:
                    icon = "🎊"
                elif prize > bet:
                    icon = "🟢"
                else:
                    icon = "🔴"

                # 2. 构建新的输出格式
                entry_text = (
                    f"[{formatted_time}] {icon} "
                    f"投入: {bet}, 抽中: {prize} (倍率{multiplier:.2f}x)"
                )

                if record["is_jackpot"]:
                    entry_text += " 🎉终极大奖!"

                entries.append(entry_text)

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(f"获取抽奖记录失败: {e}", exc_info=True)
            yield event.plain_result("查询抽奖记录时出错，请联系管理员。")

    @filter.command("签到帮助", alias={"sign_help"})
    async def sign_help(self, event: AstrMessageEvent):
        """显示帮助信息，并使用新的 Forwarder 类发送"""
        help_text = (
            "--- 📝 签到插件帮助 📝 ---\n"
            " /签到           - 进行每日签到并获取运势\n"
            " /查询 [@某人]   - 查看自己或他人的信息及今日运势\n"
            " /排行           - 查看金币排行榜\n"
            " /抽奖 <金额>      - 投入指定金额抽奖 (受每日运势影响！)\n"
            " /梭哈           - 投入所有金币抽奖\n"
            " /下注 <数字> <金额>- 参与或修改当前场次的竞猜\n"
            " /转账 <金额>@某人 - 向他人转账金币 (10%手续费)\n"
            " /抽奖详细         - 查看当前抽奖概率和奖池金额\n"
            " /奖池信息         - 查看当前奖池累计金额\n"
            " /获奖记录         - 查看最近的奖池大奖赢家\n"
            " /抽奖记录         - 查看您最近的抽奖历史\n"
            " /运势历史         - 查看你最近的运势记录\n"
            " /转账记录         - 查看您最近的转账流水\n"
            " /收款记录         - 只看您收到的款项\n"
            " /付款记录         - 只看您转出的款项\n"
            " /签到帮助         - 显示此帮助信息"
        )

        # 3. 直接调用实例的 create_from_text 方法
        forward_container = self.forwarder.create_from_text(help_text)

        # 4. 发送结果
        yield event.chain_result([forward_container])
        event.stop_event()

    @filter.command("运势历史", alias={"运势记录"})
    async def fortune_history(self, event: AstrMessageEvent):
        """查看历史运势记录"""
        try:
            user_id = event.get_sender_id()
            history = await self.db.get_fortune_history(user_id, limit=5)
            header = "📜 历史运势记录 (最近5条) 📜\n--------------------\n"
            if not history:
                yield event.plain_result("你还没有进行过占卜哦~")
                event.stop_event()
                return
            entries = []
            for record in history:
                dt_object = datetime.datetime.fromisoformat(record["timestamp"])
                formatted_time = dt_object.strftime("%Y-%m-%d %H:%M")
                entries.append(
                    f"[{formatted_time}] 抽到: 【{record['result']}】 ({record['value']}/500)"
                )
            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)
            event.stop_event()
        except Exception as e:
            logger.error(f"获取运势历史失败: {e}", exc_info=True)
            yield event.plain_result("查看运势历史失败了喵~")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("修改金币", alias={"setcoin"})
    async def modify_coins(self, event: AstrMessageEvent):
        """
        [管理员指令] 修改指定用户的金币。
        用法:
        /修改金币 <金额> -> 修改自己的金币
        /修改金币 <金额> @用户 -> 修改被@用户的金币
        """
        target_user_id = None
        amount_str = None

        # 1. 解析参数：从消息内容中分离出 @用户 和 金额
        # 遍历消息的所有部分（包括文本、@等）
        plain_text_parts = []
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                # 如果有@信息，就记录下来
                target_user_id = component.qq
            elif isinstance(component, Comp.Plain):
                # 将所有纯文本部分收集起来
                plain_text_parts.append(component.text.strip())

        # 从纯文本中查找数字作为金额
        full_text = " ".join(plain_text_parts)
        # 使用正则表达式查找第一个出现的数字串
        amount_match = re.search(r"\d+", full_text)
        if amount_match:
            amount_str = amount_match.group(0)

        # 2. 如果没有@任何人，目标就是自己
        if target_user_id is None:
            target_user_id = event.get_sender_id()

        # 3. 校验金额是否有效
        if amount_str is None:
            yield event.plain_result(
                "❌ 命令格式错误！\n请提供要修改的金额。\n用法: /修改金币 <金额> [@用户]"
            )
            return

        try:
            new_amount = int(amount_str)
            if new_amount < 0:
                yield event.plain_result("金币数量不能为负数！")
                return
        except ValueError:
            # 理论上正则保证了这是数字，但为了安全还是保留
            yield event.plain_result("金额必须是一个有效的整数！")
            return

        # 4. 执行数据库操作 (这部分逻辑和您原来的一样)
        try:
            user_data = await self.db.get_user_data(target_user_id)
            old_amount = user_data.get("coins", 0) if user_data else 0

            await self.db.update_user_data(target_user_id, coins=new_amount)

            change_amount = new_amount - old_amount
            reason = f"管理员 ({event.get_sender_id()}) 修改"
            await self.db.log_coins(target_user_id, change_amount, reason)

            target_display_name = (
                user_data.get("nickname") if user_data else None
            ) or target_user_id

            # 判断是给自己还是给别人修改，以提供更清晰的反馈
            if target_user_id == event.get_sender_id():
                yield event.plain_result(
                    f"✅ 操作成功！\n您的金币已从 {old_amount} 修改为 {new_amount}。"
                )
            else:
                yield event.plain_result(
                    f"✅ 操作成功！\n用户 {target_display_name} 的金币已从 {old_amount} 修改为 {new_amount}。"
                )

            event.stop_event()
        except Exception as e:
            logger.error(f"修改金币失败: {e}", exc_info=True)
            yield event.plain_result("修改金币时发生内部错误，请检查日志。")
