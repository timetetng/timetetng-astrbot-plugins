# astrbot_plugin_industry/service.py

import time
import random
import asyncio
from typing import Optional, Dict, Any, List
from ..common.services import shared_services
from astrbot.api import logger
from astrbot.api.star import Context, Star
from . import data_manager
from . import config
from collections import defaultdict
class CompanyService:
    # +++ 核心修改 1：接收并保存 plugin 实例 +++
    def __init__(self, plugin_instance: Star):
        self.plugin = plugin_instance # 保存传入的插件实例
        self.economy_api = None
        self.nickname_api = None
        self.stock_api = None
        asyncio.create_task(self.initialize())


    async def initialize(self) -> bool:
        """异步初始化服务，等待依赖API"""
        logger.info("[产业插件] 正在等待经济系统API加载...")
        timeout_seconds = 30 
        start_time = asyncio.get_event_loop().time()
        
        while self.economy_api is None:
            self.economy_api = shared_services.get("economy_api")
            if self.economy_api is not None:
                logger.info("[产业插件] 经济系统API已成功加载。")
                break

            if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                logger.error("[产业插件] 等待经济系统API超时！虚拟产业插件将无法正常工作！")
                return False

            await asyncio.sleep(1)

        # +++ 新增: 等待股票插件的API +++
        logger.info("[产业插件] 正在等待股票市场API加载...")
        timeout_seconds = 30 
        start_time = asyncio.get_event_loop().time()
        
        while self.stock_api is None:
            self.stock_api = shared_services.get("stock_market_api")
            if self.stock_api is not None:
                logger.info("[产业插件] 股票市场API已成功加载。")
                break

            if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                logger.warning("[产业插件] 等待股票市场API超时！上市相关功能将不可用。")
                break # 即使超时也要继续，不阻塞核心功能

            await asyncio.sleep(1)

        self.nickname_api = shared_services.get("nickname_api")
        if self.nickname_api:
            logger.info("[产业插件] 昵称系统API已成功加载。")
        else:
            logger.warning("[产业插件] 未能获取昵称系统API，将使用默认昵称。")
            
        return True

    def _generate_stock_ticker(self, company_name: str) -> str:
        """根据公司名生成一个唯一的4位大写字母股票代码"""
        # 这是一个简单的实现，你可以根据需要变得更复杂
        import re
        # 提取所有汉字或字母
        chars = re.findall('[\u4e00-\u9fa5a-zA-Z]', company_name)
        if len(chars) >= 4:
            ticker = "".join(random.sample(chars, 4)).upper()
        else:
            ticker = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=4))
        
        # 在真实场景中，还需要检查ticker是否已存在
        return ticker

    async def company_ipo(self, user_id: str, custom_ticker: str) -> str:
        """处理公司上市 (IPO) 的逻辑 (V4 - 使用固定费用)"""
        if not self.economy_api: return "错误：经济系统不可用。"
        if not self.stock_api: return "错误：股票市场服务不可用，无法进行上市。"

        company = await data_manager.get_company(user_id)
        if not company: return "您还没有公司呢。"

        if company['level'] < config.IPO_MIN_LEVEL:
            return f"❌ 上市失败：公司等级需要达到 Lv.{config.IPO_MIN_LEVEL}。"
        if company.get('is_public'):
            return "您的公司已经是上市公司了。"

        import re
        processed_ticker = custom_ticker.upper()
        if not (2 <= len(processed_ticker) <= 5 and re.match("^[A-Z]+$", processed_ticker)):
            return f"❌ 无效的股票代码「{custom_ticker}」。代码必须是2到5位纯英文字母。"
        
        is_available = await self.stock_api.is_ticker_available(processed_ticker)
        if not is_available:
            return f"❌ 股票代码「{processed_ticker}」已被占用，请换一个。"
        
        ticker = processed_ticker

        listing_fee = config.IPO_LISTING_FEE
        capital_injection = config.IPO_CAPITAL_INJECTION
        
        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < listing_fee:
            return f"启动资金不足！上市需要手续费 {listing_fee:,.0f} 金币。"

        await self.economy_api.add_coins(user_id, -listing_fee, "公司上市手续费")

        level_info = config.COMPANY_LEVELS.get(company['level'])
        current_assets = level_info['assets']
        initial_price = round(current_assets / config.IPO_TOTAL_SHARES, 2)
        
        register_success = await self.stock_api.register_stock(
            ticker=ticker,
            company_name=company['name'],
            initial_price=initial_price,
            total_shares=config.IPO_TOTAL_SHARES,
            owner_id=user_id
        )

        if not register_success:
            await self.economy_api.add_coins(user_id, listing_fee, "上市失败，手续费返还")
            return "向股票市场注册时发生未知错误，请联系管理员。费用已退还。"

        now = int(time.time())
        updates = {
            "is_public": 1, "stock_ticker": ticker, "total_shares": config.IPO_TOTAL_SHARES,
            "last_earnings_report_time": now, "last_income_claim_time": now
        }
        await data_manager.update_company(user_id, updates)

        await self.economy_api.add_coins(user_id, capital_injection, "公司上市融资")

        new_balance = await self.economy_api.get_coins(user_id)
        return (
            f"🎉 恭喜！您的公司「{company['name']}」已成功上市！\n"
            f"--------------------\n"
            f"股票代码: {ticker}\n"
            f"发行价格: {initial_price:,.2f} 金币/股\n"
            f"融资净额: +{capital_injection:,.0f} 金币\n"
            f"当前余额: {new_balance:,.0f} 金币\n"
            f"--------------------\n"
            f"您的公司已进入新的发展阶段！请使用 `/公司财报` 周期性地获取分红。"
        )
        
    async def perform_corporate_action(self, user_id: str, action_keyword: str) -> str:
        """执行一项公司行动，为下次财报提供加成"""
        if not self.economy_api or not self.stock_api:
            return "错误：依赖服务不可用。"

        company = await data_manager.get_company(user_id)
        if not company or not company.get('is_public'):
            return "只有上市公司才能执行公司行动。"

        # 1. 检查冷却时间
        now = int(time.time())
        time_since_last_action = now - company.get('last_corporate_action_time', 0)
        if time_since_last_action < config.CORPORATE_ACTION_COOLDOWN_SECONDS:
            remaining_time = config.CORPORATE_ACTION_COOLDOWN_SECONDS - time_since_last_action
            hours, rem = divmod(remaining_time, 3600); minutes, _ = divmod(rem, 60)
            return f"决策过密！距离下一次可执行公司行动还需：{int(hours)}小时 {int(minutes)}分钟。"

        # 2. 验证行动类型并获取配置
        action_config = config.CORPORATE_ACTIONS.get(action_keyword)
        if not action_config:
            return f"未知的公司行动「{action_keyword}」。"

        # 3. 计算成本
        price = await self.stock_api.get_stock_price(company['stock_ticker'])
        if price is None: return "错误：无法获取公司市值，请稍后再试。"
        
        market_cap = price * company['total_shares']
        cost = int(market_cap * action_config['cost_market_cap_pct'])

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < cost:
            return f"资金不足！执行「{action_config['name']}」需要 {cost:,.0f} 金币。"

        # 4. 执行操作：扣款、添加效果、更新冷却
        await self.economy_api.add_coins(user_id, -cost, f"公司行动: {action_config['name']}")

        bonus_min, bonus_max = action_config['earnings_bonus_range']
        bonus_potency = round(random.uniform(bonus_min, bonus_max), 3)

        # 添加一个一次性的、将在财报结算时消耗的效果
        await data_manager.add_effect(
            user_id=user_id,
            effect_type='earnings_modifier',
            potency=bonus_potency,
            duration_seconds=config.EARNINGS_REPORT_CYCLE_SECONDS + 3600, # 确保比财报周期长
            is_consumed_on_use=True
        )

        await data_manager.update_company(user_id, {"last_corporate_action_time": now})

        new_balance = await self.economy_api.get_coins(user_id)
        return (
            f"📈 决策已执行！\n"
            f"--------------------\n"
            f"行动类型: 「{action_config['name']}」\n"
            f"投资金额: -{cost:,.0f} 金币\n"
            f"预期效果: 为您的下一次财报提供约 +{(bonus_potency-1):.1%} 的业绩加成！\n"
            f"💰 当前余额: {new_balance:,.0f} 金币"
        )
    async def get_earnings_report(self, user_id: str) -> str:
        """处理上市公司发布财报的逻辑 (V4 - 混合加权分红版)"""
        if not self.economy_api or not self.stock_api: return "错误：依赖服务不可用。"

        company = await data_manager.get_company(user_id)
        if not company or not company.get('is_public'):
            return "只有上市公司才能发布财报。"

        now = int(time.time())
        time_since_last_report = now - company['last_earnings_report_time']
        
        if time_since_last_report < config.EARNINGS_REPORT_CYCLE_SECONDS:
            remaining_time = config.EARNINGS_REPORT_CYCLE_SECONDS - time_since_last_report
            hours, rem = divmod(remaining_time, 3600); minutes, _ = divmod(rem, 60)
            return f"距离下一次可发布财报还需：{int(hours)}小时 {int(minutes)}分钟。"
        
        # --- 财报加成逻辑 (保留不变) ---
        action_bonuses = await data_manager.get_active_effects(user_id, 'earnings_modifier')
        total_bonus_modifier = 1.0
        bonus_messages = []
        if action_bonuses:
            for effect in action_bonuses:
                total_bonus_modifier *= effect['potency']
                for action in config.CORPORATE_ACTIONS.values():
                    if action['earnings_bonus_range'][0] <= effect['potency'] <= action['earnings_bonus_range'][1]:
                        bonus_messages.append(f"「{action['name']}」投资生效 (+{(effect['potency']-1):.1%})")
                        break
                await data_manager.consume_effect(effect['effect_id'])
        
        # --- 核心修改：分别计算两种分红并加权 ---

        # 步骤A: 计算“等级基础分红”
        level_info = config.COMPANY_LEVELS.get(company['level'])
        base_income_per_hour = level_info['income_per_hour']
        cycle_hours = config.EARNINGS_REPORT_CYCLE_SECONDS / 3600
        level_based_dividend = base_income_per_hour * cycle_hours

        # 步骤B: 计算“市值绩效分红”
        ticker = company['stock_ticker']
        current_price = await self.stock_api.get_stock_price(ticker)
        if current_price is None:
            return f"错误：无法获取公司 {ticker} 的当前股价，请稍后再试。"
        market_cap = current_price * company['total_shares']
        market_cap_based_dividend = market_cap * config.DIVIDEND_YIELD_RATE

        # 步骤C: 按配置的权重，将两者合并为最终的基础分红
        base_dividend = (level_based_dividend * config.LEVEL_DIVIDEND_WEIGHT) + \
                        (market_cap_based_dividend * config.MARKET_CAP_DIVIDEND_WEIGHT)

        # --- 修改结束 ---

        # 1. 计算最终业绩 (后续逻辑不变)
        performance_modifier = round(random.uniform(*config.EARNINGS_PERFORMANCE_RANGE), 3)
        final_modifier = performance_modifier * total_bonus_modifier
        final_dividend = int(base_dividend * final_modifier)

        # 2. 发放分红 & 更新时间戳
        await self.economy_api.add_coins(user_id, final_dividend, f"{company['name']} 混合财报分红")
        await data_manager.update_company(user_id, {"last_earnings_report_time": now})
        
        # 3. 影响股价
        await self.stock_api.report_earnings(company['stock_ticker'], final_modifier)
        
        # 4. 构建消息
        if final_modifier > 1.1: report_text = "业绩远超预期"
        elif final_modifier > 1.0: report_text = "业绩稳健增长"
        elif final_modifier < 0.9: report_text = "业绩严重下滑"
        else: report_text = "业绩表现平平"

        new_balance = await self.economy_api.get_coins(user_id)
        
        final_message = (
            f"📊「{company['name']}」季度财报发布！\n"
            f"--------------------\n"
            f"当前公司市值: {market_cap:,.0f} 金币\n"
            f"业绩评价: 【{report_text}】 (总修正: {final_modifier:.2f})\n"
        )
        if bonus_messages:
            final_message += "\n".join(bonus_messages) + "\n"

        final_message += (
            f"董事长分红: {final_dividend:,.0f} 金币\n"
            f"(分红构成: {config.LEVEL_DIVIDEND_WEIGHT:.0%}来自等级基础, {config.MARKET_CAP_DIVIDEND_WEIGHT:.0%}来自市值表现)\n"
            f"当前余额: {new_balance:,.0f} 金币\n"
            f"--------------------\n"
            f"本次财报已影响公司股价，请前往市场查看。"
        )
        return final_message
    def _get_current_bonuses(self, company_data: Dict, active_effects: List[Dict]) -> Dict:
        """根据公司数据和活动效果，计算并返回最终的各项加成系数 (已支持PR类buff)"""
        bonuses = { "operations": 1.0, "research": 1.0, "pr": 1.0 }
        if not company_data: return bonuses
            
        ops_level = company_data.get("dept_ops_level", 0)
        res_level = company_data.get("dept_res_level", 0)
        pr_level = company_data.get("dept_pr_level", 0)

        # --- 核心修复：使用正确的键名 "operations_bonus", "research_bonus", "pr_bonus" ---
        if ops_level > 0: bonuses["operations"] = config.DEPARTMENT_LEVELS[ops_level]["operations_bonus"]
        if res_level > 0: bonuses["research"] = config.DEPARTMENT_LEVELS[res_level]["research_bonus"]
        if pr_level > 0: bonuses["pr"] = config.DEPARTMENT_LEVELS[pr_level]["pr_bonus"]
            
        # 疊加所有有时效性的效果
        for effect in active_effects:
            if effect['effect_type'] == 'income_modifier':
                bonuses["operations"] *= effect['potency']
            # +++ 新增：处理PR加成效果 +++
            elif effect['effect_type'] == 'pr_modifier':
                bonuses["pr"] *= effect['potency']

        return bonuses

    async def _apply_cost_modifiers(self, user_id: str, initial_cost: float) -> (float, bool):
        """
        检查并应用所有一次性的成本修正效果(如技术封锁)。
        Args:
            user_id: 用户的ID。
            initial_cost: 未应用debuff前的原始成本。
        Returns:
            A tuple containing:
                - final_cost (float): 应用debuff后的最终成本。
                - applied (bool): 是否成功应用了至少一个debuff。
        """
        final_cost = initial_cost
        cost_penalty_applied = False
        
        # 只获取那些需要在使用后被消耗的效果
        cost_effects = await data_manager.get_active_effects(user_id, 'cost_modifier')
        effects_to_consume = [eff for eff in cost_effects if eff.get('is_consumed_on_use')]
        
        if effects_to_consume:
            for effect in effects_to_consume:
                final_cost = round(final_cost * effect['potency'])
                await data_manager.consume_effect(effect['effect_id'])
                logger.info(f"已为用户 {user_id} 消耗debuff effect_id: {effect['effect_id']}，成本从 {initial_cost} 变为 {final_cost}")
            cost_penalty_applied = True
            
        return final_cost, cost_penalty_applied

    async def company_delist(self, user_id: str) -> str:
        """处理公司退市（私有化）的逻辑"""
        if not self.economy_api or not self.stock_api:
            return "错误：依赖服务（经济或股票市场）不可用。"

        company = await data_manager.get_company(user_id)
        if not company or not company.get('is_public'):
            return "您的公司目前是私有企业，无需退市。"

        ticker = company['stock_ticker']
        # +++ 核心修改：调用新的API获取市值 +++
        market_cap = await self.stock_api.get_market_cap(ticker)
        if market_cap is None:
            return "错误：无法获取您公司的当前市值，请稍后再试。"
        # +++ 修改结束 +++

        delist_cost = int(market_cap * (1 + config.DELISTING_PREMIUM_RATE))

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < delist_cost:
            return (f"❌ 退市失败：私有化需要从市场回购所有股票，总计需要 {delist_cost:,.0f} 金币 "
                    f"(基于当前市值 {market_cap:,.0f} 金币计算，并支付 {config.DELISTING_PREMIUM_RATE:.0%} 溢价)。\n"
                    f"您当前的资金不足。")

        # 2. 调用股票插件API，执行退市操作
        delist_success = await self.stock_api.delist_stock(ticker)
        if not delist_success:
            return "错误：股票市场服务未能成功处理退市请求，操作已取消，您的资金未被扣除。"

        # 3. 扣除费用
        await self.economy_api.add_coins(user_id, -delist_cost, f"公司 {company['name']} 私有化退市")

        # 4. 更新公司数据库状态，恢复为私有
        updates = {
            "is_public": 0,
            "stock_ticker": None,
            "total_shares": 0
        }
        await data_manager.update_company(user_id, updates)

        new_balance = await self.economy_api.get_coins(user_id)
        return (
            f"✅ 私有化成功！\n"
            f"--------------------\n"
            f"您的公司「{company['name']}」已成功退市，重新成为私有企业。\n"
            f"💵 退市费用: -{delist_cost:,.0f} 金币\n"
            f"💰 当前余额: {new_balance:,.0f} 金币\n"
            f"--------------------\n"
            f"公司已恢复为挂机收益模式，请使用 `/我的公司` 查看详情。"
        )

    async def create_company(self, user_id: str, company_name: str) -> str:
        """处理创建公司的逻辑"""
        if not self.economy_api:
            return "错误：经济系统不可用。"

        if await data_manager.get_company(user_id):
            return "您已经有一家公司了，不能重复创建哦。"

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < config.FOUNDATION_COST:
            return f"启动资金不足！创建公司需要 {config.FOUNDATION_COST:,.0f} 金币，您当前只有 {user_coins:,.0f} 金币。"

        success = await self.economy_api.add_coins(user_id, -config.FOUNDATION_COST, "创建公司启动资金")
        if not success:
            return "扣除启动资金失败，请稍后再试。"

        now = int(time.time())
        new_company = { "name": company_name, "created_at": now, "last_income_claim_time": now }

        if await data_manager.create_company(user_id, new_company):
            new_balance = await self.economy_api.get_coins(user_id)
            return (
                f"恭喜！您的公司「{company_name}」已成功创立！\n"
                f"--------------------\n"
                f"💵 启动资金: -{config.FOUNDATION_COST:,.0f} 金币\n"
                f"💰 当前余额: {new_balance:,.0f} 金币"
            )
        else:
            await self.economy_api.add_coins(user_id, config.FOUNDATION_COST, "创建公司失败，资金返还")
            return "创建公司失败，发生了一个内部错误，您的资金已退回。"

    async def dissolve_company(self, user_id: str) -> str:
        """处理出售/解散公司的逻辑"""
        # 1. 检查依赖并获取公司数据
        if not self.economy_api:
            return "错误：经济系统不可用。"

        company = await data_manager.get_company(user_id)
        if not company:
            return "您还没有公司，无法执行此操作。"

        company_name = company['name']
        company_value = 0
        value_type = ""

        # 2. 根据公司类型判断其价值
        if company.get('is_public'):
            if not self.stock_api:
                return "错误：股票市场服务不可用，无法计算上市公司市值。"
            
            ticker = company['stock_ticker']
            market_cap = await self.stock_api.get_market_cap(ticker)
            if market_cap is None:
                return "错误：无法获取您公司的当前市值，请稍后再试。"
            
            company_value = market_cap
            value_type = "当前市值"

            # 出售前必须先从市场退市
            delist_success = await self.stock_api.delist_stock(ticker)
            if not delist_success:
                return "错误：从股票市场退市时发生问题，操作已取消。"

        else: # 私有公司
            level = company.get('level', 1)
            company_value = config.COMPANY_LEVELS.get(level, {}).get('assets', 0)
            value_type = "公司资产"

        # 3. 计算回收金额 (60%)
        payout_rate = 0.60
        payout_amount = int(company_value * payout_rate)

        # 4. 执行数据库操作
        await self.economy_api.add_coins(user_id, payout_amount, f"出售公司「{company_name}」")

        # 删除公司数据及其所有相关效果
        delete_company_ok = await data_manager.delete_company(user_id)
        delete_effects_ok = await data_manager.delete_all_effects_for_user(user_id)

        if not (delete_company_ok and delete_effects_ok):
            logger.critical(f"为用户 {user_id} 清理公司数据时出错，但资金可能已发放！请手动检查数据库！")
            return "公司数据清理时发生了一个严重错误，但资金已结算。请立即联系管理员检查您的账户状态。"

        # 5. 构建成功消息
        new_balance = await self.economy_api.get_coins(user_id)
        return (
            f"✅ 公司已成功出售！\n"
            f"--------------------\n"
            f"公司名称: 「{company_name}」\n"
            f"评估价值 ({value_type}): {company_value:,.0f} 金币\n"
            f"结算比例: {payout_rate:.0%}\n"
            f"返还资金: +{payout_amount:,.0f} 金币\n"
            f"💰 当前余额: {new_balance:,.0f} 金币\n"
            f"--------------------\n"
            f"江湖再见，祝您东山再起！"
        )

    # +++ 新增：统一的升级请求处理器 +++
    async def handle_upgrade_request(self, user_id: str) -> str:
        """
        根据公司类型（私有或上市），分发到不同的升级流程。
        这是 /升级公司 指令的统一入口。
        """
        company = await data_manager.get_company(user_id)
        if not company: 
            return "您还没有公司呢，请先使用 /开公司 [公司名] 来创建一家吧。"

        if company.get('is_public'):
            # 如果是上市公司，走“计划升级”流程
            return await self.plan_public_company_upgrade(user_id, company)
        else:
            # +++ 核心修正：调用处理私有公司的专用函数 +++
            return await self.upgrade_private_company(user_id, company)

    async def plan_public_company_upgrade(self, user_id: str, company: dict) -> str:
        """处理上市公司升级计划的启动 (公示期机制)"""
        # ... (函数前半部分的成本计算等逻辑完全不变，此处省略以保持简洁) ...
        
        if not self.economy_api: return "错误：经济系统不可用。"
        if not self.stock_api:
            self.stock_api = shared_services.get("stock_market_api")
        if not self.stock_api:
            return "错误：股票市场服务不可用，无法启动上市升级计划。"
        
        level = company['level']
        if level >= config.MAX_LEVEL: return "您的公司已经达到最高等级！"

        await data_manager.clear_expired_effects(user_id)
        bonuses = self._get_current_bonuses(company, [])
        research_discount = bonuses["research"]
        base_upgrade_cost = config.COMPANY_LEVELS[level]["upgrade_cost"]
        cost_after_discount = round(base_upgrade_cost * research_discount)
        final_cost, effects_to_consume = await self._apply_cost_modifiers(user_id, cost_after_discount)
        cost_penalty_applied = bool(effects_to_consume)

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < final_cost:
            return f"资金不足！启动升至 Lv.{level + 1} 的计划需要 {final_cost:,.0f} 金币。"

        success = await self.economy_api.add_coins(user_id, -final_cost, f"启动公司Lv.{level+1}升级计划")
        if not success: return "扣除升级费用失败，请稍后再试。"

        if cost_penalty_applied:
            for effect in effects_to_consume:
                await data_manager.consume_effect(effect['effect_id'])

        announcement_period_seconds = 30
        asyncio.create_task(self._finalize_public_company_upgrade(user_id, level, announcement_period_seconds))

        hours = announcement_period_seconds / 3600
        
        user_message = (
            f"✅ 上市公司升级计划已启动！\n"
            f"--------------------\n"
            f"资金 -{final_cost:,.0f} 金币已投入扩建。\n"
            f"升级将在约 {announcement_period_seconds} 秒后完成，届时公司基本面将迎来跃升。\n"
            f"市场已获知此消息，请关注后续股价变化。"
        )
        if cost_penalty_applied:
            user_message += "\n\n⚠️ 安全警报：由于之前的商业刺探，本次计划消耗了额外的资金！"

        # +++ 核心修改：直接硬编码“Napcat”平台 +++
        from astrbot.api.event import MessageChain
        announcement_message = f"【市场公告】\n📈 {company['name']}({company['stock_ticker']}) 宣布启动重大扩张计划，预计将在 {int(hours) if hours >= 1 else announcement_period_seconds} {'小时' if hours >= 1 else '秒'}后完成升级。请投资者关注后续市场变化。"
        
        for group_id in config.BROADCAST_GROUP_IDS:
            try:
                # 直接使用 "Napcat" 构建 UMO 字符串
                umo_string = f"Napcat:GroupMessage:{str(group_id)}"
                
                await self.plugin.context.send_message(umo_string, MessageChain().message(announcement_message))
                logger.info(f"已向群 {group_id} (UMO: {umo_string}) 成功广播市场公告。")
            except Exception as e:
                logger.error(f"向群 {group_id} 广播市场公告失败: {e}", exc_info=True)
        # +++ 修改结束 +++

        return user_message

    # +++ 3. 后台执行升级的最终步骤 +++
    async def _finalize_public_company_upgrade(self, user_id: str, original_level: int, delay_seconds: int):
        """在延迟后最终完成上市公司升级的后台任务"""
        await asyncio.sleep(delay_seconds)

        company = await data_manager.get_company(user_id)
        if not company or not company.get('is_public') or company['level'] != original_level:
            logger.warning(f"用户 {user_id} 的公司升级任务被取消，因为公司状态已改变。")
            return

        new_level = original_level + 1
        await data_manager.update_company(user_id, {"level": new_level})

        new_level_assets = config.COMPANY_LEVELS[new_level].get('assets', 0)
        new_intrinsic_value_per_share = round(new_level_assets / company['total_shares'], 2)

        if self.stock_api and hasattr(self.stock_api, 'set_intrinsic_value'):
            try:
                await self.stock_api.set_intrinsic_value(company['stock_ticker'], new_intrinsic_value_per_share)
                logger.info(f"用户 {user_id} 的公司已成功升级至 Lv.{new_level}，新的内在价值 {new_intrinsic_value_per_share} 已同步至股票市场。")
            except Exception as e:
                logger.error(f"调用 stock_api.set_intrinsic_value 失败: {e}")
        else:
            logger.error("股票API不存在或没有 set_intrinsic_value 方法，无法同步内在价值！")

    # +++ 4. 处理【私有公司】的即时升级函数 +++
    async def upgrade_private_company(self, user_id: str, company: dict) -> str:
        """处理【私有公司】的即时升级逻辑"""
        if not self.economy_api: return "错误：经济系统不可用。"

        if not company: return "您还没有公司呢，请先使用 /开公司 [公司名] 来创建一家吧。"

        level = company['level']
        if level >= config.MAX_LEVEL: return "您的公司已经达到最高等级，无需再升级了！"

        await data_manager.clear_expired_effects(user_id)
        income_effects = await data_manager.get_active_effects(user_id, 'income_modifier')
        
        bonuses = self._get_current_bonuses(company, income_effects)
        research_discount = bonuses["research"]
        
        base_upgrade_cost = config.COMPANY_LEVELS[level]["upgrade_cost"]
        
        cost_after_discount = round(base_upgrade_cost * research_discount)
        
        final_cost, effects_to_consume = await self._apply_cost_modifiers(user_id, cost_after_discount)
        cost_penalty_applied = bool(effects_to_consume)

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < final_cost:
            return f"资金不足！公司升至 {level + 1} 级需要 {final_cost:,.0f} 金币，您当前只有 {user_coins:,.0f} 金币。"
        
        success = await self.economy_api.add_coins(user_id, -final_cost, f"公司从Lv.{level}升至Lv.{level+1}")
        if not success: return "扣除升级费用失败，请稍后再试。"

        if cost_penalty_applied:
            for effect in effects_to_consume:
                await data_manager.consume_effect(effect['effect_id'])

        if await data_manager.update_company(user_id, {"level": level + 1}):
            new_balance = await self.economy_api.get_coins(user_id)
            
            final_message = (
                f"🎉 升级成功！您的公司已提升至 Lv.{level + 1}！\n"
                f"--------------------\n"
                f"💵 升级费用: -{final_cost:,.0f} 金币\n"
                f"💰 当前余额: {new_balance:,.0f} 金币"
            )
            if cost_penalty_applied:
                final_message += "\n\n⚠️ 安全警报：由于之前的商业刺探，本次升级消耗了额外的资金！"

            return final_message
        else:
            await self.economy_api.add_coins(user_id, final_cost, "公司升级失败，资金返还")
            return "公司升级失败，发生了一个内部错误，您的资金已退回。"
            
    async def rename_company(self, user_id: str, new_name: str) -> str:
        """处理公司改名的逻辑 (已修复debuff消耗漏洞)"""
        if not self.economy_api: return "错误：经济系统不可用。"

        company = await data_manager.get_company(user_id)
        if not company: return "您还没有公司呢，无法进行改名操作。"
        if company['name'] == new_name: return f"您的公司名已经是「{new_name}」了，无需更改。"
        
        await data_manager.clear_expired_effects(user_id)
        income_effects = await data_manager.get_active_effects(user_id, 'income_modifier')
        bonuses = self._get_current_bonuses(company, income_effects)
        
        base_cost = round(config.COMPANY_RENAME_COST * bonuses["research"])

        final_cost, effects_to_consume = await self._apply_cost_modifiers(user_id, base_cost)
        cost_penalty_applied = bool(effects_to_consume)

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < final_cost:
            return f"金币不足！公司改名需要 {final_cost:,.0f} 金币 (已计算折扣与附加费用)，您当前只有 {user_coins:,.0f} 金币。"

        success = await self.economy_api.add_coins(user_id, -final_cost, "公司改名费用")
        if not success: return "扣除改名费用失败，请稍后再试。"

        if cost_penalty_applied:
            for effect in effects_to_consume:
                await data_manager.consume_effect(effect['effect_id'])

        if await data_manager.update_company(user_id, {"name": new_name}):
            new_balance = await self.economy_api.get_coins(user_id)
            final_message = (
                f"✅ 公司改名成功！\n"
                f"--------------------\n"
                f"旧公司名: 「{company['name']}」\n"
                f"新公司名: 「{new_name}」\n"
                f"💵 改名费用: -{final_cost:,.0f} 金币\n"
                f"💰 当前余额: {new_balance:,.0f} 金币"
            )
            if cost_penalty_applied:
                final_message += "\n\n⚠️ 安全警报：由于之前的商业刺探，本次改名消耗了额外的资金！"
            return final_message
        else:
            await self.economy_api.add_coins(user_id, final_cost, "公司改名失败，资金返还")
            return "公司改名失败，发生了一个内部错误，您的资金已退回。"

    async def talent_poach(self, attacker_id: str, target_id: str) -> str:
        """处理人才挖角的逻辑 (V3 - 区分上市公司)"""
        if attacker_id == target_id: return "不能挖角自己哦。"
        if not self.economy_api: return "错误：经济系统不可用。"

        attacker_company = await data_manager.get_company(attacker_id)
        target_company = await data_manager.get_company(target_id)

        if not attacker_company: return "您还没有公司，无法发起商业行动。"
        if not target_company: return "目标用户没有公司，无法对其进行挖角。"
        if attacker_company['level'] < config.DEPARTMENT_UNLOCK_LEVEL: 
            return f"您的公司需要达到 Lv.{config.DEPARTMENT_UNLOCK_LEVEL} 才能发起商业行动。"

        # --- 成本计算 ---
        await data_manager.clear_expired_effects(attacker_id)
        attacker_income_effects = await data_manager.get_active_effects(attacker_id, 'income_modifier')
        attacker_pr_effects = await data_manager.get_active_effects(attacker_id, 'pr_modifier')
        attacker_effects = attacker_income_effects + attacker_pr_effects
        
        await data_manager.clear_expired_effects(target_id)
        target_income_effects = await data_manager.get_active_effects(target_id, 'income_modifier')
        target_pr_effects = await data_manager.get_active_effects(target_id, 'pr_modifier')
        target_effects = target_income_effects + target_pr_effects
        
        attacker_bonuses = self._get_current_bonuses(attacker_company, attacker_effects)
        target_bonuses = self._get_current_bonuses(target_company, target_effects)
        
        target_base_income = config.COMPANY_LEVELS[target_company['level']]['income_per_hour']
        target_income_per_hour = target_base_income * target_bonuses['operations']
        
        cost_hours = random.uniform(*config.TALENT_POACH_COST_HOURS_RANGE)
        base_cost = target_income_per_hour * cost_hours
        final_cost = round(base_cost * attacker_bonuses["research"])
        final_cost = max(final_cost, 7500)

        user_coins = await self.economy_api.get_coins(attacker_id)
        if user_coins < final_cost:
            return f"金币不足！基于目标公司的实力，发起人才挖角预估需要 {final_cost:,.0f} 金币。"
        
        await self.economy_api.add_coins(attacker_id, -final_cost, "发起人才挖角")

        # --- 成功率计算 ---
        attacker_pr_bonus = attacker_bonuses['pr']
        target_pr_bonus = target_bonuses['pr']
        
        success_chance = config.TALENT_POACH_BASE_CHANCE + (attacker_pr_bonus - target_pr_bonus) * config.TALENT_POACH_PR_FACTOR
        success_chance = max(config.TALENT_POACH_CHANCE_MIN, min(config.TALENT_POACH_CHANCE_MAX, success_chance)) 

        if random.random() < success_chance:
            # --- 成功逻辑 ---
            target_income_effects_check = await data_manager.get_active_effects(target_id, 'income_modifier')
            current_debuff_count = sum(1 for eff in target_income_effects_check if eff.get('potency', 1.0) < 1.0)

            if current_debuff_count >= config.MAX_INCOME_DEBUFFS_ON_TARGET and not target_company.get('is_public'):
                return (f"✅ 挖角成功 (成功率: {success_chance:.0%})！\n"
                        f"但目标公司已是人心惶惶，人才流失严重，你的行动未能造成进一步影响。\n"
                        f"💵 行动费用: -{final_cost:,.0f} 金币。")
            
            # --- 核心修改：区分私有和上市公司 ---
            is_target_public = target_company.get('is_public')

            # 对上市公司的额外股价冲击
            if is_target_public and self.stock_api:
                target_ticker = target_company['stock_ticker']
                await self.stock_api.report_event(target_ticker, config.STOCK_IMPACT_FROM_ATTACK)
            
            # 为攻击者添加 buff (通用)
            duration_hours = random.randint(*config.TALENT_POACH_DURATION_HOURS_RANGE)
            duration_seconds = duration_hours * 3600
            buff_potency = round(random.uniform(*config.TALENT_POACH_BUFF_POTENCY_RANGE), 2)
            await data_manager.add_effect(
                user_id=attacker_id, effect_type='income_modifier', potency=buff_potency,
                duration_seconds=duration_seconds, origin_user_id=target_id
            )

            # 根据目标类型施加不同的debuff
            if is_target_public:
                # 对上市公司施加财报减益
                debuff_config = config.TALENT_POACH_PUBLIC_DEBUFF
                debuff_potency = round(random.uniform(*debuff_config['value_range']), 3)
                await data_manager.add_effect(
                    user_id=target_id,
                    effect_type=debuff_config['effect_type'],
                    potency=debuff_potency,
                    duration_seconds=config.EARNINGS_REPORT_CYCLE_SECONDS + 3600,
                    origin_user_id=attacker_id,
                    is_consumed_on_use=debuff_config['is_consumed_on_use']
                )
                return (f"✅ 挖角成功 (成功率: {success_chance:.0%})！\n"
                        f"目标上市公司的核心团队出现动荡，股价受到冲击，且下次财报业绩将受到 {(debuff_potency - 1):.1%} 的负面影响！\n"
                        f"同时，在接下来{duration_hours}小时内，您的公司时薪将获得 +{(buff_potency - 1):.0%} 的加成。\n"
                        f"💵 行动费用: -{final_cost:,.0f} 金币。")
            else:
                # 对私有公司施加时薪减益
                debuff_potency = round(random.uniform(*config.TALENT_POACH_DEBUFF_POTENCY_RANGE), 2)
                await data_manager.add_effect(
                    user_id=target_id, effect_type='income_modifier', potency=debuff_potency,
                    duration_seconds=duration_seconds, origin_user_id=attacker_id
                )
                return (f"✅ 挖角成功 (成功率: {success_chance:.0%})！\n"
                        f"在接下来{duration_hours}小时内，您的公司时薪将获得 +{(buff_potency - 1) * 100 :.0f}% 的加成，而对方公司将遭受 -{(1 - debuff_potency) * 100 :.0f}% 的损失。\n"
                        f"💵 行动费用: -{final_cost:,.0f} 金币。")
        else:
            # --- 失败逻辑 ---
            penalty = final_cost # 失败罚款等于行动成本
            await self.economy_api.add_coins(attacker_id, -penalty, "人才挖角失败罚款")
            
            buff = config.TALENT_POACH_DEFENSE_BUFF
            await data_manager.add_effect(
                user_id=target_id, effect_type=buff['effect_type'], potency=buff['potency'],
                duration_seconds=buff['duration_seconds'], origin_user_id=attacker_id
            )
            
            return (f"❌ 挖角失败 (成功率: {success_chance:.0%})！\n"
                    f"对方公司的团队凝聚力很强，您的行动已暴露！\n"
                    f"💵 行动费用 {final_cost:,.0f} 金币打了水漂，并因声誉受损被处以等额罚款！\n"
                    f"--------------------\n"
                    f"🤝 目标公司提升了“团队凝聚力”，在接下来的一段时间内将更难被挖角。")

    async def get_department_profile(self, user_id: str, user_name: str) -> str:
        """查看所有部门的详情 (已修复 NameError)"""
        company = await data_manager.get_company(user_id)
        if not company: return "您还没有公司呢。"
        if company['level'] < config.DEPARTMENT_UNLOCK_LEVEL: return f"公司达到 Lv.{config.DEPARTMENT_UNLOCK_LEVEL} 后即可升级部门。"

        await data_manager.clear_expired_effects(user_id)
        active_effects = await data_manager.get_active_effects(user_id, 'income_modifier')
        
        # +++ 核心修复：在使用前，完整定义所有部门等级变量 +++
        ops_level = company.get("dept_ops_level", 0)
        res_level = company.get("dept_res_level", 0)
        pr_level = company.get("dept_pr_level", 0)
        # +++ 修复结束 +++
        
        # 获取别名
        ops_alias = company.get("dept_ops_alias") or "运营部"
        res_alias = company.get("dept_res_alias") or "研发部"
        pr_alias = company.get("dept_pr_alias") or "公关部"
        
        bonuses = self._get_current_bonuses(company, active_effects)
        
        ops_bonus_str = f"{(bonuses['operations'] - 1) * 100:,.1f}%"
        res_bonus_str = f"{(1 - bonuses['research']) * 100:,.1f}%"
        pr_bonus_str = f"{(bonuses['pr'] - 1) * 100:,.1f}%"
        
        profile = (
            f"🏢 {user_name} 的部门总览\n"
            f"--------------------\n"
            f"📈 {ops_alias} (Lv.{ops_level}) -> 时薪提升 {ops_bonus_str}\n"
            f"💼 {res_alias} (Lv.{res_level}) -> 成本降低 {res_bonus_str}\n"
            f"🤝 {pr_alias} (Lv.{pr_level}) -> 行动成功率 {pr_bonus_str}\n"
            f"--------------------\n"
            f"使用 `/升级部门 [部门名/别名]` 来提升等级。\n"
            f"使用 `/部门改名 [原名/别名] [新别名]` 来自定义名称。"
        )
        return profile

    async def upgrade_department(self, user_id: str, dept_name_or_alias: str) -> str:
        """升级指定的部门 (已修复debuff消耗漏洞)"""
        if not self.economy_api: return "错误：经济系统不可用。"
        
        company = await data_manager.get_company(user_id)
        if not company: return "您还没有公司。"

        dept_field_name = self._resolve_dept_alias(company, dept_name_or_alias)
        if not dept_field_name:
            return f"找不到名为「{dept_name_or_alias}」的部门或别名，请检查名称是否正确。"
        
        if company['level'] < config.DEPARTMENT_UNLOCK_LEVEL: return f"公司需达到 Lv.{config.DEPARTMENT_UNLOCK_LEVEL} 才能升级部门。"

        await data_manager.clear_expired_effects(user_id)
        income_effects = await data_manager.get_active_effects(user_id, 'income_modifier')

        dept_level = company.get(dept_field_name, 0)
        max_level_allowed = company['level'] - 1

        if dept_level >= 10: return f"您的「{dept_name_or_alias}」已达到最高等级！"
        if dept_level >= max_level_allowed: return f"请先提升公司主等级至 Lv.{dept_level + 2}，才能继续升级「{dept_name_or_alias}」。"
        
        bonuses = self._get_current_bonuses(company, income_effects)
        research_discount = bonuses["research"]
        
        next_level_cost = config.DEPARTMENT_LEVELS[dept_level + 1]['cost']
        base_cost = round(next_level_cost * research_discount)

        final_cost, effects_to_consume = await self._apply_cost_modifiers(user_id, base_cost)
        cost_penalty_applied = bool(effects_to_consume)

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < final_cost:
            return f"金币不足！升级「{dept_name_or_alias}」需要 {final_cost:,.0f} 金币，您当前只有 {user_coins:,.0f} 金币。"

        success = await self.economy_api.add_coins(user_id, -final_cost, f"升级 {dept_name_or_alias}")
        if not success: return "扣款失败，请重试。"

        if cost_penalty_applied:
            for effect in effects_to_consume:
                await data_manager.consume_effect(effect['effect_id'])

        new_level = dept_level + 1
        if await data_manager.update_company(user_id, {dept_field_name: new_level}):
            new_balance = await self.economy_api.get_coins(user_id)
            
            effect_str = ""
            new_level_config = config.DEPARTMENT_LEVELS[new_level]
            
            if dept_field_name == "dept_ops_level":
                bonus = (new_level_config["operations_bonus"] - 1) * 100
                effect_str = f"📈 最新效果: 时薪提升 {bonus:,.1f}%"
            elif dept_field_name == "dept_res_level":
                bonus = (1 - new_level_config["research_bonus"]) * 100
                effect_str = f"💼 最新效果: 成本降低 {bonus:,.1f}%"
            elif dept_field_name == "dept_pr_level":
                bonus = (new_level_config["pr_bonus"] - 1) * 100
                effect_str = f"🤝 最新效果: 行动成功率 {bonus:,.1f}%"

            final_message = (f"🚀 「{dept_name_or_alias}」升级成功！已达到 Lv.{new_level}！\n"
                             f"--------------------\n"
                             f"{effect_str}\n"
                             f"💵 升级费用: -{final_cost:,.0f} 金币\n"
                             f"💰 当前余额: {new_balance:,.0f} 金币")
            
            if cost_penalty_applied:
                final_message += "\n\n⚠️ 安全警报：由于之前的商业刺探，本次升级消耗了额外的资金！"
                
            return final_message
        else:
            await self.economy_api.add_coins(user_id, final_cost, "部门升级失败返款")
            return "部门升级失败，资金已退还。"

    async def _handle_random_event(self, user_id: str, company: dict) -> Optional[Dict]:
            """处理随机事件 (V2 - 兼容私有和上市公司)"""
            now = int(time.time())
            last_event_time = company.get('last_event_time', 0)

            if now - last_event_time < config.EVENT_COOLDOWN_SECONDS:
                return None
            if random.random() > config.EVENT_PROBABILITY:
                return None

            # --- 核心改造：根据公司类型选择事件池 ---
            is_public = company.get('is_public', False)
            events = config.PUBLIC_RANDOM_EVENTS if is_public else config.RANDOM_EVENTS

            event_weights = [e.get('weight', 1) for e in events]
            if not events or not any(w > 0 for w in event_weights):
                return None

            chosen_event = random.choices(events, weights=event_weights, k=1)[0]

            event_result = {"new_balance": await self.economy_api.get_coins(user_id)}
            value_min, value_max = chosen_event['value_range']
            effect_type = chosen_event['effect_type']

            # --- 新增对上市公司事件类型的处理 ---
            if effect_type == 'stock_price_change':
                if not self.stock_api: return None  # 股票服务不可用则跳过
                percent_change = round(random.uniform(value_min, value_max), 4)
                await self.stock_api.report_event(company['stock_ticker'], percent_change)
                display_value = abs(percent_change)
                event_result["message"] = chosen_event['message'].format(value=display_value)

            elif effect_type == 'earnings_modifier':
                potency = round(random.uniform(value_min, value_max), 3)
                await data_manager.add_effect(
                    user_id=user_id, effect_type='earnings_modifier', potency=potency,
                    duration_seconds=config.EARNINGS_REPORT_CYCLE_SECONDS + 3600,  # 确保比财报周期长
                    is_consumed_on_use=True
                )
                # 根据potency是大于1还是小于1来决定显示增加还是减少的百分比
                display_value = abs(1 - potency)
                event_result["message"] = chosen_event['message'].format(value=display_value)

            # --- 处理通用的和私有公司的事件类型 ---
            elif effect_type in ['scaled_fixed', 'income_multiple']:
                amount, final_hours = 0, 0
                if effect_type == 'scaled_fixed':
                    base_value = random.randint(int(value_min), int(value_max))
                    # 上市公司的固定资本事件乘以更高基数，使其更有意义
                    multiplier = 3 if is_public else 1
                    amount = base_value * company['level'] * multiplier
                elif effect_type == 'income_multiple':  # 此类型对上市公司无意义
                    if is_public: return None
                    level_info = config.COMPANY_LEVELS.get(company['level'])
                    multiplier = random.randint(int(value_min), int(value_max))
                    final_hours = multiplier
                    amount = level_info['income_per_hour'] * multiplier

                if chosen_event['type'] == 'negative':
                    amount = -amount
                
                await self.economy_api.add_coins(user_id, amount, "公司随机事件")

                new_balance = await self.economy_api.get_coins(user_id)
                display_value = final_hours if effect_type == 'income_multiple' else abs(amount)
                event_result.update({
                    "message": chosen_event['message'].format(value=display_value),
                    "amount": amount, "new_balance": new_balance
                })

            elif effect_type == 'level_change':  # 此类型对上市公司无意义
                if is_public: return None
                current_level = company['level']
                level_change = value_min # 此事件的范围通常是固定的-1

                if current_level + level_change < 1:
                    await data_manager.delete_company(user_id)
                    event_result = {"message": chosen_event['message'] + "\n您的公司已宣告破产，一切归零！", "bankrupt": True}
                else:
                    new_level = current_level + level_change
                    await data_manager.update_company(user_id, {"level": new_level})
                    new_balance = await self.economy_api.get_coins(user_id)
                    event_result = {
                        "message": chosen_event['message'] + f"\n您的公司评级已下降至 Lv.{new_level}！",
                        "amount": 0, "new_balance": new_balance
                    }

            # 确保有事件发生才更新冷却时间
            if "message" in event_result:
                await data_manager.update_company(user_id, {"last_event_time": now})
                return event_result
                
            return None # 如果没有任何事件类型匹配，则不返回任何内容

    async def get_company_profile(self, user_id: str, user_name: str) -> str:
        """获取公司信息"""
        if not self.economy_api: return "错误：经济系统不可用。"
        
        company = await data_manager.get_company(user_id)
        if not company: return "您还没有公司呢，请先使用 /开公司 [公司名] 来创建一家吧。"

        now = int(time.time())
        profile = ""
        
        # --- 步骤 1: 统一处理随机事件 ---
        # 注意：这里的事件处理逻辑可能也需要根据公司类型做判断，
        # 但根据你的要求，我只修改显示部分。
        # 如果 _handle_random_event 内部没有区分公司类型，你可能后续也需要调整它。
        event_details = await self._handle_random_event(user_id, company)
        if event_details and event_details.get("bankrupt"):
            return event_details["message"]
        
        company = await data_manager.get_company(user_id) # 重新获取，以防事件导致公司破产
        if not company: return "数据异常：结算后找不到公司信息。可能刚刚破产。"
        
        last_view_time = company.get('last_profile_view_time', 0)

        display_name = user_name
        if self.nickname_api:
            custom_name = await self.nickname_api.get_nickname(user_id)
            if custom_name: display_name = custom_name

        # --- 步骤 2: 根据公司类型组装核心信息 ---
        if company.get('is_public'):
            # --- 上市公司逻辑 ---
            if not self.stock_api: return "股票市场服务当前不可用，无法获取公司市值。"

            ticker = company['stock_ticker']
            market_cap = await self.stock_api.get_market_cap(ticker)
            price = await self.stock_api.get_stock_price(ticker)

            market_cap_str = "无法获取 (市场服务异常)"
            if market_cap is not None and price is not None:
                market_cap_str = f"{market_cap:,.0f} 金币 (股价: ${price:.2f})"

            time_since_last_report = now - company['last_earnings_report_time']
            remaining_time = config.EARNINGS_REPORT_CYCLE_SECONDS - time_since_last_report
            next_report_info = "财报已可发布！请使用 /公司财报"
            if remaining_time > 0:
                hours, rem = divmod(remaining_time, 3600)
                minutes, _ = divmod(rem, 60)
                next_report_info = f"下一份财报: {int(hours)}小时{int(minutes)}分钟后"
            
            profile = (
                f"🏢「{company['name']}」 (上市公司)\n"
                f"--------------------\n"
                f"👤 董事长: {display_name}\n"
                f"⭐ 公司等级: Lv.{company['level']}\n"
                f"💹 股票代码: {ticker}\n"
                f"💰 公司市值: {market_cap_str}\n"
                f"📋 {next_report_info}\n"
            )
        else:
            # --- 私有公司逻辑 ---
            await data_manager.clear_expired_effects(user_id)
            income_modifier_effects = await data_manager.get_active_effects(user_id, 'income_modifier')
            pr_modifier_effects = await data_manager.get_active_effects(user_id, 'pr_modifier')
            all_bonus_effects = income_modifier_effects + pr_modifier_effects
            bonuses = self._get_current_bonuses(company, all_bonus_effects)
            
            level_info = config.COMPANY_LEVELS.get(company['level'])
            base_income = level_info['income_per_hour']
            final_income_per_hour = round(base_income * bonuses['operations'])
            unclaimed_seconds = now - company['last_income_claim_time']
            net_income = int(unclaimed_seconds * (final_income_per_hour / 3600))

            if net_income > 0:
                await self.economy_api.add_coins(user_id, net_income, "公司挂机收益")
                await data_manager.update_company(user_id, {"last_income_claim_time": now})
            
            bonus_income = final_income_per_hour - base_income
            income_str = f"{base_income:,.0f}" + (f" ({'+' if bonus_income > 0 else ''}{bonus_income:,.0f})" if bonus_income != 0 else "")
            next_level_info = f"下一级所需资金：{level_info['upgrade_cost']:,.0f} 金币\n" if company['level'] < config.MAX_LEVEL else "已达到最高等级\n"
            
            # +++ 新增/修改部分开始 +++
            # 将随机事件倒计时的计算和显示逻辑，完全放在私有公司的处理分支内
            time_since_last_event = now - company.get('last_event_time', 0)
            remaining_cooldown = config.EVENT_COOLDOWN_SECONDS - time_since_last_event
            
            if remaining_cooldown > 0:
                hours, rem = divmod(remaining_cooldown, 3600)
                minutes, seconds = divmod(rem, 60)
                event_cooldown_str = f"⏳ 距离下次随机事件还有 {int(hours)}小时{int(minutes)}分钟{int(seconds)}秒\n"
            else:
                event_cooldown_str = "💥 随机事件已准备就绪！\n"

            profile = (
                f"🏢「{company['name']}」的公司信息\n"
                f"--------------------\n"
                f"👤 董事长: {display_name}\n"
                f"⭐ 公司等级: Lv.{company['level']}\n"
                f"💼 公司资产: {level_info['assets']:,.0f} 金币\n"
                f"💰 盈利能力: {income_str} 金币/小时\n"
                f"{event_cooldown_str}" # 在这里添加事件倒计时信息
                f"{next_level_info}"
            )
            profile += f"本次为您结算了 {unclaimed_seconds} 秒的挂机收益，共 {net_income:,.0f} 金币。\n" if unclaimed_seconds > 1 else "暂无挂机收益可结算。\n"

        # --- 步骤 3: 统一附加所有状态效果 ---
        await data_manager.clear_expired_effects(user_id)
        
        income_effects = await data_manager.get_active_effects(user_id, 'income_modifier')
        cost_effects = await data_manager.get_active_effects(user_id, 'cost_modifier')
        espionage_effects = await data_manager.get_active_effects(user_id, 'espionage_chance_modifier')
        pr_effects = await data_manager.get_active_effects(user_id, 'pr_modifier')
        all_effects = income_effects + cost_effects + espionage_effects + pr_effects

        if all_effects:
            profile += "--------------------\n"
            profile += "当前状态效果:\n"
            for effect in sorted(all_effects, key=lambda x: x['effect_type']):
                potency = effect['potency']
                remaining_time = effect['expires_at'] - now
                hours, rem = divmod(remaining_time, 3600); minutes, _ = divmod(rem, 60)
                effect_type = effect['effect_type']
                
                if effect_type == 'income_modifier':
                    status_icon = "📈" if potency > 1.0 else "📉"
                    status_text = "士气高涨" if potency > 1.0 else "人才流失"
                    profile += f"{status_icon} {status_text} (收益 {potency:.0%}), 剩余 {int(hours)}小时{int(minutes)}分钟\n"
                
                elif effect_type == 'cost_modifier':
                    status_icon = "🔒"
                    status_text = "技术封锁"
                    cost_increase_percent = (potency - 1) * 100
                    profile += f"{status_icon} {status_text} (所有成本 +{cost_increase_percent:.0f}%), 剩余 {int(hours)}小时{int(minutes)}分钟\n"
                
                elif effect_type == 'espionage_chance_modifier':
                    status_icon = "🛡️"
                    status_text = "安保强化"
                    profile += f"{status_icon} {status_text} (刺探成功率降低 {abs(potency)*100:.0f}%), 剩余 {int(hours)}小时{int(minutes)}分钟\n"

                elif effect_type == 'pr_modifier':
                    status_icon = "🤝"
                    status_text = "团队凝聚力"
                    profile += f"{status_icon} {status_text} (公关系数提升 {(potency - 1)*100:.0f}%), 剩余 {int(hours)}小时{int(minutes)}分钟\n"
        
        # +++ V3 新增：攻击战报 ---
        new_debuffs = await data_manager.get_new_debuffs_since(user_id, last_view_time)
        if new_debuffs:
            attacks_by_origin = defaultdict(lambda: {'poach': 0, 'espionage': 0})
            origin_ids = {eff['origin_user_id'] for eff in new_debuffs if eff['origin_user_id']}
            
            nicknames = {}
            if self.nickname_api and origin_ids:
                nicknames = await self.nickname_api.get_nicknames_batch(list(origin_ids))

            for debuff in new_debuffs:
                origin_id = debuff.get('origin_user_id')
                if not origin_id: continue

                if debuff['effect_type'] == 'income_modifier':
                    attacks_by_origin[origin_id]['poach'] += 1
                elif debuff['effect_type'] == 'cost_modifier':
                    attacks_by_origin[origin_id]['espionage'] += 1
            
            if attacks_by_origin:
                report_lines = ["--------------------", "🚨 安全警报：近期公司遭受攻击！"]
                for origin_id, counts in attacks_by_origin.items():
                    attacker_name = nicknames.get(origin_id, f"未知对手({origin_id[-4:]})")
                    parts = []
                    if counts['poach'] > 0: parts.append(f"{counts['poach']}次人才挖角")
                    if counts['espionage'] > 0: parts.append(f"{counts['espionage']}次商业刺探")
                    report_lines.append(f"- 来自「{attacker_name}」的 {', '.join(parts)}")
                profile += "\n" + "\n".join(report_lines)

        # --- 步骤 4: 统一附加事件信息 ---
        if event_details:
            event_message = (f"\n🚨 突发事件 🚨\n{event_details['message']}")
            if 'amount' in event_details:
                sign = "+" if event_details['amount'] > 0 else ""
                event_message += (f"\n金币变动: {sign}{event_details['amount']:,.0f}\n"
                                  f"当前余额: {event_details['new_balance']:,.0f}")
            profile += "\n" + event_message

        # --- 步骤 5: 更新最后查看时间 ---
        await data_manager.update_company(user_id, {"last_profile_view_time": now})

        return profile.strip()
        
    async def _apply_cost_modifiers(self, user_id: str, initial_cost: float) -> (float, List[Dict]):
        """
        计算应用所有一次性成本修正效果后的最终成本。
        此函数不再消耗debuff，而是返回待消耗的debuff列表。
        
        Args:
            user_id: 用户的ID。
            initial_cost: 未应用debuff前的原始成本。
            
        Returns:
            A tuple containing:
                - final_cost (float): 应用debuff后的最终成本。
                - effects_to_consume (List[Dict]): 一个包含了所有被计算在内的待消耗debuff的列表。
        """
        final_cost = initial_cost
        
        cost_effects = await data_manager.get_active_effects(user_id, 'cost_modifier')
        effects_to_consume = [eff for eff in cost_effects if eff.get('is_consumed_on_use')]
        
        if effects_to_consume:
            for effect in effects_to_consume:
                final_cost = round(final_cost * effect['potency'])
                
        return final_cost, effects_to_consume

    async def get_company_ranking(self, limit: int = 10) -> str:
        """获取公司排行榜 (V2 - 兼容市值排名)"""
        all_companies = await data_manager.get_all_companies()
        if not all_companies: return "现在还没有人开公司呢，快来抢占先机！"

        # +++ 核心改造：获取并计算所有公司的真实价值 +++
        ranking_data = []
        for company in all_companies:
            asset_value = 0
            display_type = "资产"
            if company.get('is_public') and self.stock_api:
                price = await self.stock_api.get_stock_price(company['stock_ticker'])
                if price:
                    asset_value = price * company['total_shares']
                    display_type = "市值"
            else:
                asset_value = config.COMPANY_LEVELS.get(company['level'], {}).get('assets', 0)
            
            ranking_data.append({
                "data": company,
                "asset_value": asset_value,
                "display_type": display_type
            })

        # 按真实价值排序
        sorted_ranking = sorted(ranking_data, key=lambda x: x['asset_value'], reverse=True)

        # 获取昵称
        user_ids = [item['data']['user_id'] for item in sorted_ranking[:limit]]
        nicknames = {}
        if self.nickname_api:
            nicknames = await self.nickname_api.get_nicknames_batch(user_ids)
            
        # 构建排行榜消息
        ranking_list = ["🏆 公司市值排行榜 🏆\n--------------------"]
        for i, item in enumerate(sorted_ranking[:limit]):
            company = item['data']
            user_id, level = company['user_id'], company['level']
            display_name = nicknames.get(user_id, f"用户({user_id[-4:]})")
            
            rank_icon = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f" {i+1}."
            
            ranking_list.append(
                f"{rank_icon} {display_name} - 「{company['name']}」 "
                f"(Lv.{level}, {item['display_type']}: {item['asset_value']:,.0f})"
            )
            
        return "\n".join(ranking_list)

    async def industrial_espionage(self, attacker_id: str, target_id: str) -> str:
        """处理商业间谍的逻辑 (已应用反刷钱平衡机制及TypeError修复)"""
        if attacker_id == target_id: return "不能刺探自己的公司。"
        if not self.economy_api: return "错误：经济系统不可用。"

        attacker_company = await data_manager.get_company(attacker_id)
        target_company = await data_manager.get_company(target_id)

        if not attacker_company: return "您还没有公司，无法发起商业行动。"
        if not target_company: return "目标用户没有公司。"
        if attacker_company['level'] < config.DEPARTMENT_UNLOCK_LEVEL: 
            return f"您的公司需要达到 Lv.{config.DEPARTMENT_UNLOCK_LEVEL} 才能发起商业行动。"

        # --- 成本计算 ---
        await data_manager.clear_expired_effects(attacker_id)
        # +++ 核心修复：分别获取所需效果并合并 +++
        attacker_income_effects = await data_manager.get_active_effects(attacker_id, 'income_modifier')
        attacker_pr_effects = await data_manager.get_active_effects(attacker_id, 'pr_modifier')
        attacker_effects = attacker_income_effects + attacker_pr_effects
        attacker_bonuses = self._get_current_bonuses(attacker_company, attacker_effects)
        
        await data_manager.clear_expired_effects(target_id)
        target_income_effects = await data_manager.get_active_effects(target_id, 'income_modifier')
        target_pr_effects = await data_manager.get_active_effects(target_id, 'pr_modifier')
        target_effects = target_income_effects + target_pr_effects
        target_bonuses = self._get_current_bonuses(target_company, target_effects)
        # +++ 修复结束 +++

        target_level = target_company['level']
        target_level_info = config.COMPANY_LEVELS.get(target_level)
        if not target_level_info:
            return f"错误：无法获取目标公司 Lv.{target_level} 的配置信息。"
            
        base_income = target_level_info['income_per_hour']
        operations_multiplier = target_bonuses.get("operations", 1.0)
        target_income_per_hour = round(base_income * operations_multiplier)

        cost_hours = random.uniform(*config.INDUSTRIAL_ESPIONAGE_COST_HOURS_RANGE)
        base_cost = target_income_per_hour * cost_hours
        final_cost = round(base_cost * attacker_bonuses["research"])
        final_cost = max(final_cost, 5000) 

        user_coins = await self.economy_api.get_coins(attacker_id)
        if user_coins < final_cost:
            return f"金币不足！基于目标公司的实力，发起商业刺探预估需要 {final_cost:,.0f} 金币。"
        
        await self.economy_api.add_coins(attacker_id, -final_cost, "发起商业刺探")

        # --- 成功率计算 ---
        target_defense_effects = await data_manager.get_active_effects(target_id, 'espionage_chance_modifier')
        defense_modifier = sum(effect['potency'] for effect in target_defense_effects)

        attacker_level = attacker_company['level']
        target_level = target_company['level']
        attacker_pr_level = attacker_company.get('dept_pr_level', 0)
        target_pr_level = target_company.get('dept_pr_level', 0)
        level_modifier = (attacker_level - target_level) * config.ESPIONAGE_LEVEL_FACTOR
        pr_modifier = (attacker_pr_level - target_pr_level) * config.ESPIONAGE_PR_FACTOR
        
        success_chance = config.ESPIONAGE_BASE_CHANCE + level_modifier + pr_modifier + defense_modifier
        success_chance = max(config.ESPIONAGE_CHANCE_MIN, min(config.ESPIONAGE_CHANCE_MAX, success_chance))
        
        if random.random() < success_chance:
            # --- 成功逻辑 ---
            target_cost_effects = await data_manager.get_active_effects(target_id, 'cost_modifier')
            
            if len(target_cost_effects) >= config.MAX_COST_DEBUFFS_ON_TARGET:
                min_m, max_m = config.INDUSTRIAL_ESPIONAGE_REWARD_COST_MULTIPLIER_RANGE
                reward_multiplier = random.uniform(min_m, max_m)
                final_reward = round(final_cost * reward_multiplier)
                await self.economy_api.add_coins(attacker_id, final_reward, "商业破坏行动成功奖励")

                return (f"✅ 破坏成功 (成功率: {success_chance:.0%})！\n"
                        f"但目标公司的技术已被全面封锁，你的行动未能造成进一步影响。\n"
                        f"--------------------\n"
                        f"💵 行动投资: -{final_cost:,.0f} 金币\n"
                        f"💰 投资回报: +{final_reward:,.0f} 金币！")

            if target_company.get('is_public') and self.stock_api:
                target_ticker = target_company['stock_ticker']
                await self.stock_api.report_event(target_ticker, config.STOCK_IMPACT_FROM_ATTACK)

            min_m, max_m = config.INDUSTRIAL_ESPIONAGE_REWARD_COST_MULTIPLIER_RANGE
            reward_multiplier = random.uniform(min_m, max_m)
            final_reward = round(final_cost * reward_multiplier)
            await self.economy_api.add_coins(attacker_id, final_reward, "商业破坏行动成功奖励")

            debuff_potency = round(random.uniform(*config.INDUSTRIAL_ESPIONAGE_DEBUFF_POTENCY_RANGE), 2)

            await data_manager.add_effect(
                user_id=target_id,
                effect_type='cost_modifier',
                potency=debuff_potency,
                duration_seconds=config.INDUSTRIAL_ESPIONAGE_DEBUFF_DURATION_SECONDS,
                origin_user_id=attacker_id,
                is_consumed_on_use=True
            )
            
            return (f"✅ 破坏成功 (成功率: {success_chance:.0%})！\n"
                    f"您对目标公司造成了严重的商业打击！\n"
                    f"--------------------\n"
                    f"💵 行动投资: -{final_cost:,.0f} 金币\n"
                    f"💰 投资回报: +{final_reward:,.0f} 金币！\n"
                    f"🎯 目标已陷入“技术封锁”，下次升级或改名成本将增加！")
        else:
            # --- 失败逻辑 ---
            penalty_multiplier = round(random.uniform(*config.INDUSTRIAL_ESPIONAGE_PENALTY_MULTIPLIER_RANGE), 2)
            penalty = round(final_cost * penalty_multiplier)
            
            await self.economy_api.add_coins(attacker_id, -penalty, "商业刺探失败罚款")
            
            buff = config.ESPIONAGE_DEFENSE_BUFF
            await data_manager.add_effect(
                user_id=target_id,
                effect_type=buff['effect_type'],
                potency=buff['potency'],
                duration_seconds=buff['duration_seconds'],
                origin_user_id=attacker_id
            )
            
            return (f"❌ 刺探失败 (成功率: {success_chance:.0%})！\n"
                    f"行动已暴露！你的计划不仅让你损失了 {final_cost:,.0f} 金币的投资，"
                    f"还被处以 {penalty:,.0f} 金币的巨额罚款！\n"
                    f"--------------------\n"
                    f"🛡️ 目标公司加强了安保措施，在接下来的一段时间内将更难被刺探。")

    def _resolve_dept_alias(self, company_data: Dict, name_or_alias: str) -> Optional[str]:
        """根据部门名或别名，解析出其在数据库中的标准字段名"""
        # 别名 -> 标准名 映射
        alias_map = {
            company_data.get("dept_ops_alias"): "dept_ops_level",
            company_data.get("dept_res_alias"): "dept_res_level",
            company_data.get("dept_pr_alias"): "dept_pr_level",
        }
        # 移除 None 键，防止用户别名恰好是 "None" 字符串时出问题
        alias_map.pop(None, None) 
        
        # 标准名 -> 标准名 映射
        name_map = {
            "运营部": "dept_ops_level",
            "研发部": "dept_res_level",
            "公关部": "dept_pr_level",
        }

        # 优先匹配别名，再匹配标准名
        if name_or_alias in alias_map:
            return alias_map[name_or_alias]
        if name_or_alias in name_map:
            return name_map[name_or_alias]
            
        return None # 找不到匹配

        # +++ 新增：部门改名逻辑 +++
    async def set_department_alias(self, user_id: str, old_name: str, new_alias: str) -> str:
        """为部门设置或更改别名 (已修复debuff消耗漏洞)"""
        if not self.economy_api: return "错误：经济系统不可用。"

        company = await data_manager.get_company(user_id)
        if not company: return "您还没有公司。"

        field_name_to_change = self._resolve_dept_alias(company, old_name)
        if not field_name_to_change:
            return f"找不到名为「{old_name}」的部门或别名。"

        if self._resolve_dept_alias(company, new_alias) is not None and new_alias != old_name:
            return f"别名「{new_alias}」已被使用或与系统默认名称冲突，请换一个。"

        await data_manager.clear_expired_effects(user_id)
        income_effects = await data_manager.get_active_effects(user_id, 'income_modifier')
        bonuses = self._get_current_bonuses(company, income_effects)
        base_cost = round(config.DEPARTMENT_RENAME_COST * bonuses["research"])
        
        final_cost, effects_to_consume = await self._apply_cost_modifiers(user_id, base_cost)
        cost_penalty_applied = bool(effects_to_consume)

        user_coins = await self.economy_api.get_coins(user_id)
        if user_coins < final_cost:
            return f"金币不足！部门改名需要 {final_cost:,.0f} 金币。"
        
        success = await self.economy_api.add_coins(user_id, -final_cost, f"部门改名为 {new_alias}")
        if not success: return "扣款失败，请重试。"

        if cost_penalty_applied:
            for effect in effects_to_consume:
                await data_manager.consume_effect(effect['effect_id'])

        alias_field_name = field_name_to_change.replace("_level", "_alias")
        if await data_manager.update_company(user_id, {alias_field_name: new_alias}):
            new_balance = await self.economy_api.get_coins(user_id)
            final_message = (f"✅ 部门改名成功！\n"
                             f"您已将「{old_name}」更名为「{new_alias}」。\n"
                             f"💵 改名费用: -{final_cost:,.0f} 金币\n"
                             f"💰 当前余额: {new_balance:,.0f} 金币")
            
            if cost_penalty_applied:
                final_message += "\n\n⚠️ 安全警报：由于之前的商业刺探，本次改名消耗了额外的资金！"
                
            return final_message
        else:
            await self.economy_api.add_coins(user_id, final_cost, "部门改名失败返款")
            return "部门改名失败，资金已退还。"