import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List

# 严格按照您提供的“经济系统API文档”中的模板进行导入
from ..common.services import shared_services

# 严格按照您提供的“插件开发文档”进行导入
from astrbot.api.star import Star, register, Context
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter


# 存储每个用户当天已获取的额度
daily_allowance_tracker: Dict[str, int] = {}


@register(
    "llm_banker",
    "Gemini",
    "让LLM拥有自己的钱包，并能通过函数工具与用户进行转账或“抢劫”等金融互动，并每日自动对富豪收税。",
    "1.2.0",
    "https://github.com/your-repo/astrbot_plugin_llm_banker",
)
class LLMBankerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.economy_api = None
        self.stock_api = None

        # [修改] 初始化任务句柄，用于后续的清理
        self._reset_task_handle: asyncio.Task | None = None
        self._tax_task_handle: asyncio.Task | None = None

        # 创建一个异步任务来安全地初始化API和定时任务
        asyncio.create_task(self.initialize_plugin())

    async def wait_for_api(self, api_name: str, timeout: int = 30):
        """通用API等待函数 (已按文档修正)"""
        logger.info(f"正在等待 {api_name} 加载...")
        start_time = asyncio.get_event_loop().time()
        while True:
            api_instance = shared_services.get(api_name)
            if api_instance:
                logger.info(f"{api_name} 已成功加载。")
                return api_instance
            if asyncio.get_event_loop().time() - start_time > timeout:
                logger.warning(f"等待 {api_name} 超时，相关功能将受限！")
                return None
            await asyncio.sleep(1)

    async def initialize_plugin(self):
        """
        异步初始化插件，获取API并启动每日任务。
        """
        self.economy_api = await self.wait_for_api("economy_api")
        self.stock_api = await self.wait_for_api("stock_market_api")

        if self.economy_api and self.stock_api:
            logger.info("LLM Banker 插件核心API加载完成，功能已就绪。")
            # [修改] 存储任务句柄
            self._reset_task_handle = asyncio.create_task(self._daily_reset_task())
            self._tax_task_handle = asyncio.create_task(
                self._daily_tax_collection_task()
            )
        else:
            logger.error("一个或多个核心API未能加载，LLM Banker 插件无法正常运行！")

    async def _daily_reset_task(self):
        """每日午夜清空用户津贴记录"""
        try:  # [修改] 添加异常捕获以响应取消
            while True:
                now = datetime.now()
                midnight = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=1, microsecond=0
                )
                seconds_until_midnight = (midnight - now).total_seconds()
                logger.info(
                    f"LLM Banker: 将在 {seconds_until_midnight:.0f} 秒后重置每日津贴。"
                )
                await asyncio.sleep(seconds_until_midnight)
                global daily_allowance_tracker
                daily_allowance_tracker.clear()
                logger.info("LLM Banker: 每日用户津贴已重置。")
        except asyncio.CancelledError:
            logger.info("LLM Banker: 每日津贴重置任务被终止。")
            raise  # 重新抛出异常以确认取消

    async def _daily_tax_collection_task(self):
        """每日按时对总资产排名前10的玩家征税"""
        try:  # [修改] 添加异常捕获以响应取消
            while True:
                # 1. 获取配置的税收时间
                tax_time_str = self.config.get("tax_collection_time", "00:00:05")
                try:
                    target_time = datetime.strptime(tax_time_str, "%H:%M:%S").time()
                except ValueError:
                    logger.error(
                        f"配置的收税时间 '{tax_time_str}' 格式无效，将使用默认值 00:00:05。"
                    )
                    target_time = datetime.strptime("00:00:05", "%H:%M:%S").time()

                # 2. 计算下一次执行时间
                now = datetime.now()
                today_target = datetime.combine(now.date(), target_time)

                if now >= today_target:
                    tomorrow_date = now.date() + timedelta(days=1)
                    next_tax_time = datetime.combine(tomorrow_date, target_time)
                else:
                    next_tax_time = today_target

                seconds_until_tax_time = (next_tax_time - now).total_seconds()

                logger.info(
                    f"LLM Banker: 将在 {seconds_until_tax_time:.0f} 秒后 (即 {next_tax_time}) 开始征收资产税。"
                )
                await asyncio.sleep(seconds_until_tax_time)

                # 3. --- 执行收税逻辑 ---
                logger.info("LLM Banker: 开始执行每日资产税征收流程...")

                if not self.stock_api or not self.economy_api:
                    logger.error("收税任务失败：API未加载。")
                    continue

                tax_rates: List[Any] = self.config.get("tax_rates", [])  # 改为 Any

                if not isinstance(tax_rates, list) or len(tax_rates) != 10:
                    logger.warning(
                        f"配置中的 'tax_rates' 项缺失或格式不正确（应为10个浮点数的列表），跳过本次收税。当前值: {tax_rates}"
                    )
                    continue

                try:
                    ranking = await self.stock_api.get_total_asset_ranking(limit=10)

                    if not ranking:
                        logger.info("无法获取资产排行榜，无人需要交税。")
                        continue

                    logger.info(f"成功获取到 {len(ranking)} 位玩家的资产排行。")

                    bot_id = self.config.get("bot_user_id")

                    if not bot_id:
                        logger.error("收税失败：机器人账户 bot_user_id 未配置。")
                        continue

                    for i, player_data in enumerate(ranking):
                        user_id = player_data.get("user_id")

                        # --- [修复 1: 处理 total_assets] ---
                        assets_data = player_data.get("total_assets")
                        total_assets_numeric = 0.0

                        if isinstance(assets_data, (list, tuple)) and assets_data:
                            try:
                                total_assets_numeric = float(assets_data[0])
                            except (ValueError, TypeError, IndexError):
                                logger.warning(
                                    f"玩家 {user_id} 的资产数据是序列，但第一个元素不是有效数字或为空: {assets_data}"
                                )
                                total_assets_numeric = 0.0
                        elif isinstance(assets_data, (int, float)):
                            total_assets_numeric = float(assets_data)
                        else:
                            logger.warning(
                                f"玩家 {user_id} 的资产数据格式无法识别 (非数字/序列)，将计为0. 数据: {assets_data} (类型: {type(assets_data)})"
                            )
                            total_assets_numeric = 0.0

                        # --- [修复 2: 处理 tax_rate] ---
                        try:
                            tax_rate = float(tax_rates[i])
                        except (ValueError, TypeError, IndexError):
                            logger.error(
                                f"玩家 {user_id} 的税率 tax_rates[{i}] (值: '{tax_rates[i]}') 无法转换为浮点数，跳过此玩家。"
                            )
                            continue  # 跳过这个玩家
                        # --- [修复 2 结束] ---

                        tax_amount = int(total_assets_numeric * tax_rate)

                        if tax_amount <= 0:
                            continue

                        logger.info(
                            f"向排行第 {i + 1} 的玩家 {user_id} (总资产: {assets_data}) 征收 {tax_rate * 100:.2f}% 的税，金额: {tax_amount}"
                        )

                        op1 = await self.economy_api.add_coins(
                            user_id, -tax_amount, "每日资产税"
                        )
                        op2 = await self.economy_api.add_coins(
                            bot_id, tax_amount, f"收取来自 {user_id} 的每日资产税"
                        )

                        if not (op1 and op2):
                            logger.error(f"向玩家 {user_id} 收税时发生错误，尝试回滚。")
                            await self.economy_api.add_coins(
                                user_id, tax_amount, "收税失败回滚"
                            )
                            await self.economy_api.add_coins(
                                bot_id, -tax_amount, "收税失败回滚"
                            )

                    logger.info("LLM Banker: 每日资产税征收流程执行完毕。")

                except Exception as e:
                    logger.error(f"执行收税任务时发生意外错误: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("LLM Banker: 每日收税任务被终止。")
            raise  # 重新抛出异常以确认取消

    @filter.llm_tool(name="query_my_bot_balance")
    async def query_my_balance(self, event: AstrMessageEvent) -> str:
        """
        查询我（机器人）自己有多少金币。此工具能让我了解自己的财务状况，以便做出下一步决策。
        Args:
            None
        """
        if not self.economy_api:
            return "经济系统未就绪，我查不了账。"

        bot_id = self.config.get("bot_user_id")
        if not bot_id:
            return "我的管家没给我配置账户ID，我没有钱。"

        balance = await self.economy_api.get_coins(bot_id)
        threshold = self.config.get("low_balance_threshold", 20000)

        if balance < threshold:
            return f"我目前只有 {balance} 金币了，钱包告急，得省着点花才行。"
        else:
            return f"我当前拥有 {balance} 金币，资金非常充裕！"

    @filter.llm_tool(name="evaluate_user_financial_status")
    async def evaluate_user_financial_status(
        self, event: AstrMessageEvent, target_user_id: str
    ) -> str:
        """
        查询并评估一个用户的财务状况，帮助我（机器人）决定是否要与他们进行金钱互动（比如给他们钱或从他们那里拿钱）。
        Args:
            target_user_id(string): 需要查询其财务状况的目标用户的ID。
        """
        if not self.economy_api:
            return "经济系统未就绪，无法评估。"
        bot_id = self.config.get("bot_user_id")
        if not bot_id:
            return "我的账户未配置，无法进行比较。"

        bot_balance = await self.economy_api.get_coins(bot_id)
        user_balance = await self.economy_api.get_coins(target_user_id)
        rob_ratio = self.config.get("robbery_threshold_ratio", 5.0)

        if bot_balance > 0 and user_balance > bot_balance * rob_ratio:
            ratio = user_balance / bot_balance
            return f"分析报告：用户 {target_user_id} 拥有 {user_balance} 金币，而我只有 {bot_balance} 金币。他的财富是我的 {ratio:.1f} 倍！他简直富得流油，也许是时候让他为社区做点“贡献”了。"
        elif user_balance < bot_balance * 0.1:
            return f"分析报告：用户 {target_user_id} 只有 {user_balance} 金币，看起来他需要一些帮助。"
        else:
            return f"分析报告：用户 {target_user_id} 拥有 {user_balance} 金币，财务状况尚可。"

    @filter.llm_tool(name="transfer_coins_to_user")
    async def transfer_coins_to_user(
        self, event: AstrMessageEvent, target_user_id: str, amount: int, reason: str
    ) -> Dict[str, Any]:
        """
        从我的账户给指定用户转账一定数量的金币。可以用于奖励、资助或任何我想给钱的场合。
        Args:
            target_user_id(string): 接收金币的用户的唯一ID。
            amount(number): 我想转账给用户的金币数量，必须是正整数。
            reason(string): 本次转账的原因，例如“表现出色的奖励”。
        """
        if not self.economy_api:
            return {"success": False, "error": "经济系统API未连接。"}

        bot_id = self.config.get("bot_user_id")
        if not bot_id:
            return {"success": False, "error": "机器人账户未在配置中设定。"}

        if target_user_id == bot_id:
            return {"success": False, "error": "我不能给自己转账。"}

        if amount <= 0:
            return {"success": False, "error": "转账金额必须是正数。"}

        max_amount = self.config.get("max_transaction_amount", 10000)
        if amount > max_amount:
            return {"success": False, "error": f"单次转账金额不能超过 {max_amount}。"}

        global daily_allowance_tracker
        current_allowance = daily_allowance_tracker.get(target_user_id, 0)
        daily_limit = self.config.get("daily_user_allowance_limit", 50000)
        if current_allowance + amount > daily_limit:
            return {
                "success": False,
                "error": "该用户今日已达到获取上限，无法再向其转账。",
            }

        bot_balance = await self.economy_api.get_coins(bot_id)
        low_balance_threshold = self.config.get("low_balance_threshold", 20000)
        if bot_balance < low_balance_threshold:
            return {
                "success": False,
                "error": f"我的余额已低于警戒线({low_balance_threshold})，现在不能乱花钱。",
            }
        if bot_balance < amount:
            return {
                "success": False,
                "error": f"我的钱不够了，当前只有 {bot_balance} 金币。",
            }

        op1 = await self.economy_api.add_coins(
            bot_id, -amount, f"转账给 {target_user_id} ({reason})"
        )
        op2 = await self.economy_api.add_coins(
            target_user_id, amount, f"收到机器人转账 ({reason})"
        )

        if op1 and op2:
            daily_allowance_tracker[target_user_id] = current_allowance + amount
            return {
                "success": True,
                "message": f"操作成功，已向用户 {target_user_id} 转账 {amount} 金币。",
            }
        else:
            await self.economy_api.add_coins(bot_id, amount, "交易失败回滚")
            await self.economy_api.add_coins(target_user_id, -amount, "交易失败回滚")
            return {"success": False, "error": "转账过程中发生未知错误，交易已取消。"}

    @filter.llm_tool(name="take_coins_from_user")
    async def take_coins_from_user(
        self, event: AstrMessageEvent, target_user_id: str, amount: int, reason: str
    ) -> Dict[str, Any]:
        """
        从指定用户账户扣除一定数量的金币并转入我的账户。可以用于惩罚、收取费用，或者当我发现某个用户过于富有想“调节”一下时使用。
        Args:
            target_user_id(string): 需要被扣款的用户的唯一ID。
            amount(number): 我想从用户那里拿走的金币数量，必须是正整数。
            reason(string): 本次扣款的原因，例如“违反社区规定”或“财富再分配税”。
        """
        if not self.economy_api:
            return {"success": False, "error": "经济系统API未连接。"}

        bot_id = self.config.get("bot_user_id")
        if not bot_id:
            return {"success": False, "error": "机器人账户未在配置中设定。"}

        if target_user_id == bot_id:
            return {"success": False, "error": "我不能从自己身上拿钱。"}

        if amount <= 0:
            return {"success": False, "error": "操作金额必须是正数。"}

        max_amount = self.config.get("max_transaction_amount", 10000)
        if amount > max_amount:
            return {"success": False, "error": f"单次操作金额不能超过 {max_amount}。"}

        op1 = await self.economy_api.add_coins(
            target_user_id, -amount, f"被机器人扣款 ({reason})"
        )
        op2 = await self.economy_api.add_coins(
            bot_id, amount, f"从 {target_user_id} 处收款 ({reason})"
        )

        if op1 and op2:
            return {
                "success": True,
                "message": f"操作成功，已从用户 {target_user_id} 处取走 {amount} 金币。",
            }
        else:
            await self.economy_api.add_coins(target_user_id, amount, "交易失败回滚")
            await self.economy_api.add_coins(bot_id, -amount, "交易失败回滚")
            return {"success": False, "error": "扣款过程中发生未知错误，交易已取消。"}

    # 插件终止函数
    async def terminate(self):
        """
        插件被卸载/停用/重载时调用的清理函数。
        """
        logger.info("LLM Banker 插件正在终止，开始清理后台定时任务...")

        if self._reset_task_handle and not self._reset_task_handle.done():
            self._reset_task_handle.cancel()
            logger.info("每日津贴重置任务已请求取消。")

        if self._tax_task_handle and not self._tax_task_handle.done():
            self._tax_task_handle.cancel()
            logger.info("每日收税任务已请求取消。")
