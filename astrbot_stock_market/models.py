# stock_market/models.py

import random
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from enum import Enum


# --- 市场状态枚举 ---
class MarketStatus(Enum):
    CLOSED = "已休市"
    OPEN = "交易中"


class MarketCycle(Enum):
    BULL_MARKET = "牛市"
    BEAR_MARKET = "熊市"
    NEUTRAL_MARKET = "盘整市"


class VolatilityRegime(Enum):
    LOW = "低波动期"
    HIGH = "高波动期"


class DailyBias(Enum):
    UP = "上涨日"
    DOWN = "下跌日"
    SIDEWAYS = "盘整日"


# ▼▼▼【兼容性修改】重新引入 Trend 枚举 ▼▼▼
class Trend(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


# ▲▲▲【修改结束】▲▲▲


# --- 数据类 ---
@dataclass
class DailyScript:
    date: date
    bias: DailyBias
    expected_range_factor: float
    target_close: float


@dataclass
class MarketSimulator:
    """宏观市场模拟器"""

    cycle: MarketCycle = MarketCycle.NEUTRAL_MARKET
    volatility_regime: VolatilityRegime = VolatilityRegime.LOW
    steps_in_current_cycle: int = 0
    steps_in_current_vol_regime: int = 0
    min_cycle_duration: int = 7
    min_vol_duration: int = 7

    def update(self, logger):
        """每日更新一次宏观状态"""
        self.steps_in_current_cycle += 1
        if (
            self.steps_in_current_cycle > self.min_cycle_duration
            and random.random() < 1 / 7
        ):
            old_cycle_name = self.cycle.value
            self.cycle = random.choice([c for c in MarketCycle if c != self.cycle])
            self.steps_in_current_cycle = 0
            logger.info(
                f"[宏观周期转换] 市场从【{old_cycle_name}】进入【{self.cycle.value}】!"
            )

        self.steps_in_current_vol_regime += 1
        if (
            self.steps_in_current_vol_regime > self.min_vol_duration
            and random.random() < 1 / 5
        ):
            # 修复 F841: 移除了未使用的 old_vol_name 赋值
            self.volatility_regime = (
                VolatilityRegime.HIGH
                if self.volatility_regime == VolatilityRegime.LOW
                else VolatilityRegime.LOW
            )
            self.steps_in_current_vol_regime = 0
            logger.info(f"[市场情绪转换] 市场进入【{self.volatility_regime.value}】!")


@dataclass
class VirtualStock:
    """虚拟股票的内存数据结构"""

    stock_id: str
    name: str
    current_price: float
    volatility: float = 0.05
    industry: str = "综合"
    previous_close: float = 0.0
    fundamental_value: float = 200.0
    daily_script: DailyScript | None = None

    # ▼▼▼【V2.1 核心改动】▼▼▼
    # 新的“动能波”字段
    intraday_momentum: float = 0.0
    momentum_target_peak: float = 0.0
    momentum_duration_ticks: int = 0
    momentum_current_tick: int = 0

    # 为了兼容 main.py 而保留/重加的旧字段 (将由新算法在后台更新)
    intraday_trend: Trend = Trend.NEUTRAL
    intraday_trend_duration: int = 0
    # ▲▲▲【改动结束】▲▲▲

    price_history: deque = field(default_factory=lambda: deque(maxlen=60))
    daily_close_history: deque = field(default_factory=lambda: deque(maxlen=20))
    kline_history: deque = field(default_factory=lambda: deque(maxlen=9000))
    market_pressure: float = 0.0
    is_listed_company: bool = False
    owner_id: str | None = None
    total_shares: int = 0

    def get_last_day_close(self) -> float:
        return self.previous_close if self.previous_close > 0 else self.current_price

    def get_momentum(self) -> float:
        if len(self.daily_close_history) < 5:
            return 0.0
        changes = [
            1 if self.daily_close_history[i] > self.daily_close_history[i - 1] else -1
            for i in range(1, len(self.daily_close_history))
        ]
        weights = list(range(1, len(changes) + 1))
        return sum(c * w for c, w in zip(changes, weights)) / sum(weights)

    def update_fundamental_value(self):
        self.fundamental_value *= random.uniform(0.999, 1.001)
