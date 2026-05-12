# stock_market/config.py

import os
from datetime import time

# --- 目录与路径 ---
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "plugins_db",
    "stock_market",
)
os.makedirs(DATA_DIR, exist_ok=True)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# --- Web服务配置 ---
# !!! 重要：请将这里的 IP 地址换成您服务器IP !!!
SERVER_PUBLIC_IP = "127.0.0.1"
SERVER_PORT = 30005
SERVER_BASE_URL = f"http://{SERVER_PUBLIC_IP}:{SERVER_PORT}"
# 是否使用域名
IS_SERVER_DOMAIN = False
SERVER_DOMAIN = "https://example.domain"

# webAPI速率白名单
RATE_LIMIT_WHITELIST = [
    "127.0.0.1",  # 本地回环地址
    "192.168.1.0/24",  # 局域网192.168.1.0 到 192.168.1.255 范围内的地址
    "10.8.0.0/24",  # wireguard VPN 默认地址范围
]
# --- API 安全与JWT认证 ---
JWT_SECRET_KEY = "4d+/vzSlO9EsdI0/4oEtpS7wkfORC9JJd5fBvGJXEgYkym3jpPmozvvqTIVnXYC1cqdWpfMxfN7G+t1nJWau+g=="
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24 * 14  # Token有效期14天

# --- A股交易规则与市场状态 ---
T_OPEN = time(8, 0)
T_CLOSE = time(23, 59, 59)
SELL_LOCK_MINUTES = 60  # 买入后锁定60分钟
SELL_FEE_RATE = 0.01  # 卖出手续费率 1%

# --- V5.4 算法常量 ---
# 交易滑点配置
SLIPPAGE_FACTOR = 0.0000005  # 用于计算大额订单对价格的冲击
MAX_SLIPPAGE_DISCOUNT = 0.3  # 最大滑点为30%
# 分级动能波
BIG_WAVE_PROBABILITY = 0.03  # 每次尝试生成新波段时，是“大波段”的概率 (例如3%)

# “小波段”参数 (常规波动)
SMALL_WAVE_PEAK_MIN = 0.4  # 峰值范围
SMALL_WAVE_PEAK_MAX = 0.8
SMALL_WAVE_TICKS_MIN = 5  # 持续tick范围 (25-60分钟)
SMALL_WAVE_TICKS_MAX = 12

# “大波段”参数 (主升/主跌)
BIG_WAVE_PEAK_MIN = 1.0  # 峰值范围 (强度显著更高)
BIG_WAVE_PEAK_MAX = 1.6
BIG_WAVE_TICKS_MIN = 12  # 持续tick范围 (1-2小时)
BIG_WAVE_TICKS_MAX = 24

# 玩家交易对市场压力的影响
COST_PRESSURE_FACTOR = 0.0000005  # 交易额转换为市场压力点数的系数

# --- 涨跌停板机制 (新增) ---
# 限制涨跌幅的时间窗口（小时）。例如 1 表示检查过去 1 小时的价格变化。
PRICE_LIMIT_WINDOW_HOURS = 1

# 该时间窗口内允许的最大涨跌幅比例。0.50 表示 50%。
# 例如：如果1小时前价格为100，当前价格最高只能到150，最低只能到50。
PRICE_LIMIT_PERCENTAGE = 0.50
# 2. 当日总涨跌幅限制 (硬顶/硬底) [新增]
# 相对于昨日收盘价(今日开盘价)的最大允许涨跌幅。
# 1.0 表示 100%，即价格最多翻倍或腰斩。
DAILY_PRICE_LIMIT_PERCENTAGE = 1.0
# 上市公司API配置
EARNINGS_SENSITIVITY_FACTOR = 0.5
DEFAULT_LISTED_COMPANY_VOLATILITY = 0.025

# 内在价值更新对市场压力的影响
INTRINSIC_VALUE_PRESSURE_FACTOR = 5

# --- 原生股票随机事件 ---
NATIVE_EVENT_PROBABILITY_PER_TICK = 0.001  # 每5分钟有 0.1% 的概率

NATIVE_STOCK_RANDOM_EVENTS = [
    # 正面事件
    {
        "type": "positive",
        "effect_type": "price_change_percent",
        "value_range": [0.05, 0.12],
        "message": "📈 [行业利好] {stock_name}({stock_id})所在行业迎来政策扶持，市场前景看好，股价上涨 {value:.2%}！",
        "weight": 20,
        "industry": "科技",
    },
    {
        "type": "positive",
        "effect_type": "price_change_percent",
        "value_range": [0.03, 0.08],
        "message": "📈 [企业喜讯] {stock_name}({stock_id})宣布与巨头达成战略合作，股价受提振上涨 {value:.2%}！",
        "weight": 15,
    },
    {
        "type": "positive",
        "effect_type": "price_change_percent",
        "value_range": [0.10, 0.20],
        "message": "📈 [重大突破] {stock_name}({stock_id})公布了革命性的新技术，市场为之疯狂，股价飙升 {value:.2%}！",
        "weight": 5,
    },
    # 负面事件
    {
        "type": "negative",
        "effect_type": "price_change_percent",
        "value_range": [-0.10, -0.04],
        "message": "📉 [行业利空] 监管机构宣布对{stock_name}({stock_id})所在行业进行严格审查，股价应声下跌 {value:.2%}！",
        "weight": 20,
    },
    {
        "type": "negative",
        "effect_type": "price_change_percent",
        "value_range": [-0.15, -0.08],
        "message": "📉 [企业丑闻] {stock_name}({stock_id})被爆出数据泄露丑闻，信誉受损，投资者大量抛售，股价下跌 {value:.2%}！",
        "weight": 10,
    },
    {
        "type": "negative",
        "effect_type": "price_change_percent",
        "value_range": [-0.25, -0.18],
        "message": "📉 [核心产品缺陷] {stock_name}({stock_id})的核心产品被发现存在严重安全漏洞，面临大规模召回，股价暴跌 {value:.2%}！",
        "weight": 3,
    },
]
