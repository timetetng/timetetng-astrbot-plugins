from typing import TYPE_CHECKING

from .config import (
    COST_PRESSURE_FACTOR,
    MAX_SLIPPAGE_DISCOUNT,
    SELL_FEE_RATE,
    SELL_LOCK_MINUTES,
    SLIPPAGE_FACTOR,
)
from .models import MarketStatus

if TYPE_CHECKING:
    from .main import StockMarketRefactored


class TradingManager:
    def __init__(self, plugin: "StockMarketRefactored"):
        self.plugin = plugin

    async def perform_buy(
        self, user_id: str, identifier: str, quantity: int
    ) -> tuple[bool, str]:
        """执行买入操作的核心内部函数。"""
        # ▼▼▼【核心修正】▼▼▼
        # 不要读取 self.plugin.market_status，因为它可能是过时的。
        # 直接调用 get_market_status_and_wait() 进行实时检查。
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"
        # ▲▲▲【修正结束】▲▲▲

        if not self.plugin.economy_api:
            return False, "经济系统未启用，无法进行交易！"
        if quantity <= 0:
            return False, "❌ 购买数量必须是一个正整数。"
        stock = await self.plugin.find_stock(identifier)
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。"
        cost = round(stock.current_price * quantity, 2)
        balance = await self.plugin.economy_api.get_coins(user_id)
        if balance < cost:
            return False, f"💰 金币不足！需要 {cost:.2f}，你只有 {balance:.2f}。"
        success = await self.plugin.economy_api.add_coins(
            user_id, -int(cost), f"购买 {quantity} 股 {stock.name}"
        )
        if not success:
            return False, "❗ 扣款失败，购买操作已取消。"
        await self.plugin.db_manager.add_holding(
            user_id, stock.stock_id, quantity, stock.current_price
        )
        pressure_generated = (cost**0.98) * COST_PRESSURE_FACTOR
        stock.market_pressure += pressure_generated
        return True, (
            f"✅ 买入成功！\n以 ${stock.current_price:.2f}/股 的价格买入 {quantity} 股 {stock.name}，花费 {cost:.2f} 金币。\n"
            f"⚠️ 注意：买入的股票将在 {SELL_LOCK_MINUTES} 分钟后解锁，方可卖出。"
        )

    async def perform_sell(
        self, user_id: str, identifier: str, quantity_to_sell: int
    ) -> tuple[bool, str, dict | None]:
        """执行卖出操作的核心内部函数。"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            # 注意：此函数返回三个值，所以这里也要返回三个值 (bool, str, None)
            return (
                False,
                f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。",
                None,
            )
        # ▲▲▲【修正结束】▲▲▲

        if not self.plugin.economy_api:
            return False, "经济系统未启用，无法进行交易！", None
        if quantity_to_sell <= 0:
            return False, "❌ 出售数量必须是一个正整数。", None
        stock = await self.plugin.find_stock(identifier)
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。", None
        total_sellable = await self.plugin.db_manager.get_sellable_quantity(
            user_id, stock.stock_id
        )
        if total_sellable < quantity_to_sell:
            hint = await self.plugin.db_manager.get_next_unlock_time_str(
                user_id, stock.stock_id
            )
            return (
                False,
                f"❌ 可卖数量不足！\n您想卖 {quantity_to_sell} 股，但只有 {total_sellable} 股可卖。{hint or ''}",
                None,
            )
        success, message, data = await self._execute_sell_order(
            user_id, stock.stock_id, quantity_to_sell, stock.current_price
        )
        return success, message, data

    async def _execute_sell_order(
        self, user_id: str, stock_id: str, quantity_to_sell: int, current_price: float
    ) -> tuple[bool, str, dict]:
        """执行卖出操作的核心经济逻辑。"""
        # ... (此方法内部代码无需修改)
        total_cost_basis = await self.plugin.db_manager.execute_fifo_sell(
            user_id, stock_id, quantity_to_sell
        )
        price_discount_percent = min(
            quantity_to_sell * SLIPPAGE_FACTOR, MAX_SLIPPAGE_DISCOUNT
        )
        actual_sell_price = current_price * (1 - price_discount_percent)
        gross_income = round(actual_sell_price * quantity_to_sell, 2)
        fee = round(gross_income * SELL_FEE_RATE, 2)
        net_income = gross_income - fee
        profit_loss = gross_income - total_cost_basis
        await self.plugin.economy_api.add_coins(
            user_id,
            int(net_income),
            f"出售 {quantity_to_sell} 股 {self.plugin.stocks[stock_id].name}",
        )
        pressure_generated = (gross_income**0.95) * COST_PRESSURE_FACTOR
        self.plugin.stocks[stock_id].market_pressure -= pressure_generated
        pnl_emoji = "🎉" if profit_loss > 0 else "😭" if profit_loss < 0 else "😐"
        slippage_info = (
            f"(因大单抛售产生 {price_discount_percent:.2%} 滑点)\n"
            if price_discount_percent >= 0.001
            else ""
        )
        message = (
            f"✅ 卖出成功！{slippage_info}"
            f"成交数量: {quantity_to_sell} 股\n"
            f"当前市价: ${current_price:.2f}\n"
            f"您的成交均价: ${actual_sell_price:.2f}\n"
            f"成交总额: {gross_income:.2f} 金币\n"
            f"手续费(1%): -{fee:.2f} 金币\n"
            f"实际收入: {net_income:.2f} 金币\n"
            f"{pnl_emoji} 本次交易盈亏: {profit_loss:+.2f} 金币"
        )
        return (
            True,
            message,
            {
                "net_income": net_income,
                "fee": fee,
                "profit_loss": profit_loss,
                "slippage_percent": price_discount_percent,
            },
        )

    async def perform_buy_all_in(
        self, user_id: str, identifier: str
    ) -> tuple[bool, str]:
        """执行梭哈买入操作"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"
        # ▲▲▲【修正结束】▲▲▲

        stock = await self.plugin.find_stock(identifier)
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。"
        if stock.current_price <= 0:
            return False, "❌ 股价异常，无法购买。"
        balance = await self.plugin.economy_api.get_coins(user_id)
        if balance < stock.current_price:
            return (
                False,
                f"💰 金币不足！\n股价为 ${stock.current_price:.2f}，而您只有 {balance:.2f} 金币，连一股都买不起。",
            )
        quantity_to_buy = int(balance // stock.current_price)
        if quantity_to_buy == 0:
            return (
                False,
                f"💰 金币不足！\n股价为 ${stock.current_price:.2f}，而您只有 {balance:.2f} 金币，连一股都买不起。",
            )
        return await self.perform_buy(user_id, identifier, quantity_to_buy)

    async def perform_sell_all_for_stock(
        self, user_id: str, identifier: str
    ) -> tuple[bool, str]:
        """执行全抛单支股票的操作"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"
        # ▲▲▲【修正结束】▲▲▲

        stock = await self.plugin.find_stock(identifier)
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。"
        quantity_to_sell = await self.plugin.db_manager.get_sellable_quantity(
            user_id, stock.stock_id
        )
        if quantity_to_sell == 0:
            return False, f"您当前没有可供卖出的 {stock.name} 股票。"
        success, message, _ = await self.perform_sell(
            user_id, identifier, quantity_to_sell
        )
        return success, message

    async def perform_sell_all_portfolio(self, user_id: str) -> tuple[bool, str]:
        """执行清仓操作"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"
        # ▲▲▲【修正结束】▲▲▲

        sellable_stocks = await self.plugin.db_manager.get_sellable_portfolio(user_id)
        if not sellable_stocks:
            return False, "您当前没有可供卖出的持仓。"
        total_net_income, total_profit_loss, total_fees = 0, 0, 0
        sell_details = []
        for stock_id, quantity_to_sell in sellable_stocks:
            stock = self.plugin.stocks.get(stock_id)
            if not stock:
                continue
            # perform_sell 内部已经有实时检查了，这里理论上可以不加，但为了逻辑清晰和保险起见，保留顶层检查。
            success, _, result_data = await self.perform_sell(
                user_id, stock_id, quantity_to_sell
            )
            if success:
                total_net_income += result_data["net_income"]
                total_profit_loss += result_data["profit_loss"]
                total_fees += result_data["fee"]
                pnl_str = f"盈亏 {result_data['profit_loss']:+.2f}"
                sell_details.append(
                    f" - {stock.name}: {quantity_to_sell}股, 收入 {result_data['net_income']:.2f} ({pnl_str})"
                )
        if not sell_details:
            return False, "清仓失败，未能成功卖出任何股票。"
        pnl_emoji = (
            "🎉" if total_profit_loss > 0 else "😭" if total_profit_loss < 0 else "😐"
        )
        details_str = "\n".join(sell_details)
        final_message = (
            f"🗑️ 已清仓所有可卖持股！\n{details_str}\n--------------------\n"
            f"总收入: {total_net_income:.2f} 金币\n"
            f"总手续费: -{total_fees:.2f} 金币\n"
            f"{pnl_emoji} 总盈亏: {total_profit_loss:+.2f} 金币"
        )
        return True, final_message
