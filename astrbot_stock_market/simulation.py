# stock_market/simulation.py

import asyncio
import math
import random
from datetime import date, datetime
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import MessageChain

from .config import (
    NATIVE_EVENT_PROBABILITY_PER_TICK,
    NATIVE_STOCK_RANDOM_EVENTS,
)
from .models import DailyBias, DailyScript, MarketCycle, Trend, VirtualStock

if TYPE_CHECKING:
    from .main import StockMarketRefactored


class MarketSimulation:
    def __init__(self, plugin: "StockMarketRefactored"):
        self.plugin = plugin
        self.task: asyncio.Task | None = None

    def start(self):
        """启动价格更新循环任务。"""
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._update_stock_prices_loop())
            logger.info("股票价格更新循环已启动。")

    def stop(self):
        """停止价格更新循环任务。"""
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("股票价格更新循环已停止。")

    def _generate_daily_script(
        self, stock: VirtualStock, current_date: date
    ) -> DailyScript:
        """为单支股票生成每日剧本 (V5.3 算法)。"""
        momentum = stock.get_momentum()
        last_close = stock.get_last_day_close()
        valuation_ratio = (
            last_close / stock.fundamental_value if stock.fundamental_value > 0 else 1.0
        )

        mean_reversion_pressure = 1.0
        if valuation_ratio < 0.7:
            mean_reversion_pressure = 1 / max(valuation_ratio, 0.1)
        elif valuation_ratio > 1.5:
            mean_reversion_pressure = valuation_ratio

        bias_weights = [1.0, 1.0, 1.0]
        if self.plugin.market_simulator.cycle == MarketCycle.BULL_MARKET:
            bias_weights[0] *= 2.0
        elif self.plugin.market_simulator.cycle == MarketCycle.BEAR_MARKET:
            bias_weights[2] *= 2.0
        if momentum > 0:
            bias_weights[0] *= 1 + momentum * 1.5
        elif momentum < 0:
            bias_weights[2] *= 1 - abs(momentum) * 1.5
        if valuation_ratio < 0.7:
            bias_weights[0] *= mean_reversion_pressure
        elif valuation_ratio > 1.5:
            bias_weights[2] *= mean_reversion_pressure
        bias = random.choices(
            [DailyBias.UP, DailyBias.SIDEWAYS, DailyBias.DOWN],
            weights=bias_weights,
            k=1,
        )[0]

        base_range = stock.volatility * random.uniform(0.7, 1.5)
        if self.plugin.market_simulator.volatility_regime.value == "高波动期":
            base_range *= 1.7
        if bias != DailyBias.SIDEWAYS:
            base_range *= 1.3

        price_change = last_close * base_range * random.uniform(0.4, 1.0)
        if bias == DailyBias.UP:
            target_close = last_close + price_change
        elif bias == DailyBias.DOWN:
            target_close = last_close - price_change
        else:
            target_close = last_close + (price_change / 2 * random.choice([-1, 1]))

        return DailyScript(
            date=current_date,
            bias=bias,
            expected_range_factor=base_range,
            target_close=max(0.01, target_close),
        )

    async def _handle_native_stock_random_event(
        self, stock: VirtualStock
    ) -> str | None:
        """处理原生虚拟股票的随机事件。"""
        if random.random() > NATIVE_EVENT_PROBABILITY_PER_TICK:
            return None

        eligible_events = [
            e
            for e in NATIVE_STOCK_RANDOM_EVENTS
            if e.get("industry") is None or e.get("industry") == stock.industry
        ]
        if not eligible_events:
            return None

        event_weights = [e.get("weight", 1) for e in eligible_events]
        chosen_event = random.choices(eligible_events, weights=event_weights, k=1)[0]

        if chosen_event.get("effect_type") == "price_change_percent":
            value_min, value_max = chosen_event["value_range"]
            percent_change = round(random.uniform(value_min, value_max), 4)
            new_price = round(stock.current_price * (1 + percent_change), 2)
            stock.current_price = max(0.01, new_price)
            return chosen_event["message"].format(
                stock_name=stock.name, stock_id=stock.stock_id, value=percent_change
            )

        return None

    async def _update_stock_prices_loop(self):
        """后台任务循环，更新股票价格 (V2.1 分级动能波 + V5.6 双重涨跌停板)。"""
        from .config import (
            BIG_WAVE_PEAK_MAX,
            BIG_WAVE_PEAK_MIN,
            BIG_WAVE_PROBABILITY,
            BIG_WAVE_TICKS_MAX,
            BIG_WAVE_TICKS_MIN,
            DAILY_PRICE_LIMIT_PERCENTAGE,
            PRICE_LIMIT_PERCENTAGE,
            # --- 新增配置项 ---
            PRICE_LIMIT_WINDOW_HOURS,
            SMALL_WAVE_PEAK_MAX,
            SMALL_WAVE_PEAK_MIN,
            SMALL_WAVE_TICKS_MAX,
            SMALL_WAVE_TICKS_MIN,
        )

        while True:
            try:
                new_status, wait_seconds = self.plugin.get_market_status_and_wait()
                self.plugin.market_status = new_status
                if new_status != self.plugin.market_status:
                    logger.info(
                        f"市场状态变更: {self.plugin.market_status.value} -> {new_status.value}"
                    )
                    self.plugin.market_status = new_status

                if self.plugin.market_status.value != "交易中":
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                    continue

                now = datetime.now()
                today = now.date()
                if self.plugin.last_update_date != today:
                    logger.info(f"新交易日 ({today}) 开盘，正在初始化市场...")
                    self.plugin.market_simulator.update(logger)
                    for stock in self.plugin.stocks.values():
                        # 在新的一天开始时，记录昨日收盘价 (previous_close)
                        if self.plugin.last_update_date:
                            stock.previous_close = stock.current_price
                            stock.daily_close_history.append(stock.current_price)
                        else:
                            stock.previous_close = stock.current_price

                        stock.update_fundamental_value()
                        stock.daily_script = self._generate_daily_script(stock, today)
                    self.plugin.last_update_date = today

                db_updates = []
                current_interval_minute = (now.minute // 5) * 5
                five_minute_start = now.replace(
                    minute=current_interval_minute, second=0, microsecond=0
                )

                for stock in self.plugin.stocks.values():
                    script = stock.daily_script
                    if not script:
                        continue

                    open_price = stock.current_price
                    event_message = None

                    if not stock.is_listed_company:
                        event_message = await self._handle_native_stock_random_event(
                            stock
                        )

                    if event_message:
                        logger.info(f"[随机市场事件] {event_message}")
                        message_chain = MessageChain().message(
                            f"【市场快讯】\n{event_message}"
                        )
                        subscribers_copy = list(self.plugin.broadcast_subscribers)
                        for umo in subscribers_copy:
                            try:
                                await self.plugin.context.send_message(
                                    umo, message_chain
                                )
                            except Exception as e:
                                logger.error(f"向订阅者 {umo} 推送消息失败: {e}")
                                if umo in self.plugin.broadcast_subscribers:
                                    self.plugin.broadcast_subscribers.remove(umo)

                        close_price = stock.current_price
                        high_price, low_price = (
                            max(open_price, close_price),
                            min(open_price, close_price),
                        )
                    else:
                        # --- ▼▼▼【核心算法 V2.1：动能波与随机游走】▼▼▼

                        # 1. 检查动能波是否结束
                        if stock.momentum_current_tick >= stock.momentum_duration_ticks:
                            stock.intraday_momentum = 0.0
                            stock.momentum_current_tick = 0
                            stock.momentum_duration_ticks = 0

                        # 2. 尝试生成新的动能波
                        if stock.momentum_duration_ticks == 0 and random.random() < 0.3:
                            bias = script.bias
                            weights = (
                                [0.6, 0.4]
                                if bias == DailyBias.UP
                                else [0.4, 0.6]
                                if bias == DailyBias.DOWN
                                else [0.5, 0.5]
                            )
                            direction = random.choices([1, -1], weights=weights)[0]

                            if random.random() < BIG_WAVE_PROBABILITY:
                                peak_magnitude = random.uniform(
                                    BIG_WAVE_PEAK_MIN, BIG_WAVE_PEAK_MAX
                                )
                                duration_ticks = random.randint(
                                    BIG_WAVE_TICKS_MIN, BIG_WAVE_TICKS_MAX
                                )
                            else:
                                peak_magnitude = random.uniform(
                                    SMALL_WAVE_PEAK_MIN, SMALL_WAVE_PEAK_MAX
                                )
                                duration_ticks = random.randint(
                                    SMALL_WAVE_TICKS_MIN, SMALL_WAVE_TICKS_MAX
                                )

                            stock.momentum_target_peak = direction * peak_magnitude
                            stock.momentum_duration_ticks = duration_ticks
                            stock.momentum_current_tick = 0

                        # 3. 更新动能波进度
                        if stock.momentum_duration_ticks > 0:
                            stock.momentum_current_tick += 1
                            progress = (
                                stock.momentum_current_tick
                                / stock.momentum_duration_ticks
                            )
                            momentum_factor = math.sin(progress * math.pi)
                            stock.intraday_momentum = (
                                stock.momentum_target_peak * momentum_factor
                            )

                        # 4. 计算各部分影响力
                        effective_volatility = (
                            script.expected_range_factor / math.sqrt(288) * 2.2
                        )
                        trend_influence = (
                            stock.intraday_momentum
                            * (open_price * effective_volatility)
                            * random.uniform(0.8, 1.2)
                        )
                        random_walk = (
                            open_price
                            * effective_volatility
                            * random.normalvariate(0, 0.8)
                        )

                        short_term_reversion_force = 0
                        if len(stock.price_history) >= 5:
                            sma5 = sum(list(stock.price_history)[-5:]) / 5
                            short_term_reversion_force = -(open_price - sma5) * 0.15

                        intraday_anchor_force = (
                            (script.target_close - open_price) / 288 * 0.05
                        )
                        pressure_influence = stock.market_pressure * 0.01
                        stock.market_pressure *= 0.8

                        # 5. 计算理论总变化量
                        total_change = (
                            trend_influence
                            + random_walk
                            + short_term_reversion_force
                            + intraday_anchor_force
                            + pressure_influence
                        )

                        # 计算出理论上的新价格
                        calculated_price = open_price + total_change

                        # --- ▼▼▼【核心修改：双重涨跌停板逻辑】▼▼▼

                        # 1. 应用【滑动窗口】限价 (限制短时波动)
                        # 将小时转换为 tick 数 (1 tick = 5分钟)
                        limit_ticks = int(PRICE_LIMIT_WINDOW_HOURS * 60 / 5)

                        # 获取参考价格 (回溯 N 小时)
                        ref_price_window = open_price
                        if len(stock.price_history) >= limit_ticks:
                            ref_price_window = stock.price_history[-limit_ticks]
                        elif stock.price_history:
                            ref_price_window = stock.price_history[
                                0
                            ]  # 历史不够时用最早记录
                        else:
                            ref_price_window = (
                                stock.previous_close
                                if stock.previous_close > 0
                                else open_price
                            )

                        if ref_price_window > 0:
                            window_max = ref_price_window * (1 + PRICE_LIMIT_PERCENTAGE)
                            window_min = ref_price_window * (1 - PRICE_LIMIT_PERCENTAGE)

                            if calculated_price > window_max:
                                calculated_price = window_max
                            elif calculated_price < window_min:
                                calculated_price = window_min

                        # 2. 应用【当日总幅】限价 (限制全天波动)
                        # 使用 stock.previous_close 作为当日基准价
                        ref_price_daily = (
                            stock.previous_close
                            if stock.previous_close > 0
                            else open_price
                        )

                        if ref_price_daily > 0:
                            daily_max = ref_price_daily * (
                                1 + DAILY_PRICE_LIMIT_PERCENTAGE
                            )
                            daily_min = ref_price_daily * (
                                1 - DAILY_PRICE_LIMIT_PERCENTAGE
                            )

                            # 再次执行截断，取交集（谁更严格听谁的）
                            if calculated_price > daily_max:
                                calculated_price = daily_max
                            elif calculated_price < daily_min:
                                calculated_price = daily_min

                        # 最终价格确认 (防止价格为负或0)
                        close_price = round(max(0.01, calculated_price), 2)

                        # --- ▲▲▲【涨跌停板逻辑结束】▲▲▲

                        # --- ▲▲▲【核心算法结束】▲▲▲

                        # ▼▼▼【兼容层】根据新动能更新旧趋势字段，以兼容main.py ▼▼▼
                        if stock.intraday_momentum > 0.15:
                            stock.intraday_trend = Trend.BULLISH
                        elif stock.intraday_momentum < -0.15:
                            stock.intraday_trend = Trend.BEARISH
                        else:
                            stock.intraday_trend = Trend.NEUTRAL
                        stock.intraday_trend_duration = max(
                            0,
                            stock.momentum_duration_ticks - stock.momentum_current_tick,
                        )
                        # ▲▲▲【兼容层结束】▲▲▲

                        absolute_volatility_base = open_price * (
                            script.expected_range_factor / math.sqrt(288)
                        )
                        high_price = round(
                            max(open_price, close_price)
                            + random.uniform(0, absolute_volatility_base * 0.8),
                            2,
                        )
                        low_price = round(
                            max(
                                0.01,
                                min(open_price, close_price)
                                - random.uniform(0, absolute_volatility_base * 0.8),
                            ),
                            2,
                        )
                        stock.current_price = close_price

                    stock.price_history.append(stock.current_price)
                    kline_entry = {
                        "date": five_minute_start.isoformat(),
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": stock.current_price,
                    }
                    stock.kline_history.append(kline_entry)
                    db_updates.append(
                        {
                            "stock_id": stock.stock_id,
                            "current_price": stock.current_price,
                            "kline": kline_entry,
                            "market_pressure": stock.market_pressure,
                        }
                    )

                if self.plugin.db_manager:
                    await self.plugin.db_manager.batch_update_stock_data(db_updates)

                now_after_update = datetime.now()
                seconds_to_wait = (
                    5 - (now_after_update.minute % 5)
                ) * 60 - now_after_update.second
                await asyncio.sleep(max(1, seconds_to_wait))

            except asyncio.CancelledError:
                logger.info("股票价格更新任务被取消。")
                break
            except Exception as e:
                logger.error(f"股票价格更新任务出现严重错误: {e}", exc_info=True)
                await asyncio.sleep(60)
