# plugins/astrbot_stock_market/api.py

from typing import TYPE_CHECKING, Any

# 仅用于类型提示，避免循环导入
if TYPE_CHECKING:
    from .main import StockMarketRefactored


class StockMarketAPI:
    """
    模拟炒股插件对外暴露的API。
    用于和虚拟产业插件等其他系统进行交互。
    """

    def __init__(self, plugin_instance: "StockMarketRefactored"):
        self._plugin = plugin_instance

    async def register_stock(
        self,
        ticker: str,
        company_name: str,
        initial_price: float,
        total_shares: int,
        owner_id: str,
    ) -> bool:
        return await self._plugin.api_register_stock(
            ticker, company_name, initial_price, total_shares, owner_id
        )

    async def get_stock_price(self, ticker: str) -> float | None:
        return await self._plugin.api_get_stock_price(ticker)

    async def is_ticker_available(self, ticker: str) -> bool:
        return await self._plugin.api_is_ticker_available(ticker)

    async def report_earnings(self, ticker: str, performance_modifier: float):
        await self._plugin.api_report_earnings(ticker, performance_modifier)

    async def report_event(self, ticker: str, price_impact_percentage: float):
        await self._plugin.api_report_event(ticker, price_impact_percentage)

    async def delist_stock(self, ticker: str) -> bool:
        return await self._plugin.api_delist_stock(ticker)

    async def set_intrinsic_value(self, ticker: str, value: float):
        await self._plugin.api_set_intrinsic_value(ticker, value)

    async def get_market_cap(self, ticker: str) -> float | None:
        return await self._plugin.api_get_market_cap(ticker)

    async def get_user_total_asset(self, user_id: str) -> dict[str, Any]:
        return await self._plugin.get_user_total_asset(user_id)

    async def get_total_asset_ranking(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self._plugin.get_total_asset_ranking(limit)
