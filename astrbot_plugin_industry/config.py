import os
# --- 核心配置 ---

# 公司创建的初始费用
FOUNDATION_COST = 50000

# 数据库文件路径
# 将数据库放在 AstrBot 的 data 目录中，是良好的实践
DATABASE_DIR = "data/plugin_data/industry"
DATABASE_FILE = os.path.join(DATABASE_DIR, "industry.db")

# --- 公司改名配置 ---
COMPANY_RENAME_COST = 100000

# 事件触发冷却时间（秒），86400秒 = 24小时
EVENT_COOLDOWN_SECONDS = 86400

# 事件触发概率
EVENT_PROBABILITY = 1

# 公司等级配置表
# 键是等级，值是该等级的属性
# upgrade_cost: 升到下一级所需的费用。None 表示已满级。
# assets: 该等级对应的公司固定资产
# income_per_hour: 该等级下，每小时产生的被动收入
COMPANY_LEVELS = {
    # --- 早期：快速成长，建立正反馈 (回报周期 < 6天) ---
    1: {"upgrade_cost": 30000, "assets": 50000, "income_per_hour": 150},
    2: {
        "upgrade_cost": 70000,
        "assets": 80000,
        "income_per_hour": 450,
    },  # 回报周期 ≈ 4.2天
    3: {
        "upgrade_cost": 150000,
        "assets": 150000,
        "income_per_hour": 1100,
    },  # 回报周期 ≈ 4.5天
    # --- 中期：放缓节奏，开始体现金币回收 (回报周期 6-9天) ---
    4: {
        "upgrade_cost": 500000,
        "assets": 330000,
        "income_per_hour": 2500,
    },  # 回报周期 ≈ 6.0天
    5: {
        "upgrade_cost": 1200000,
        "assets": 830000,
        "income_per_hour": 4500,
    },  # 回报周期 ≈ 7.0天
    6: {
        "upgrade_cost": 2800000,
        "assets": 2030000,
        "income_per_hour": 9000,
    },  # 回报周期 ≈ 8.1天
    # --- 后期：长线目标，核心金币回收 (回报周期 > 10天) ---
    7: {
        "upgrade_cost": 6000000,
        "assets": 4830000,
        "income_per_hour": 18000,
    },  # 回报周期 ≈ 10.0天
    8: {
        "upgrade_cost": 11000000,
        "assets": 10830000,
        "income_per_hour": 30000,
    },  # 回报周期 ≈ 11.4天
    9: {
        "upgrade_cost": 20000000,
        "assets": 21830000,
        "income_per_hour": 60000,
    },  # 回报周期 ≈ 12.7天
    # --- 顶级：毕业与象征 ---
    10: {"upgrade_cost": None, "assets": 41830000, "income_per_hour": 100000},
}
# 获取最高等级
MAX_LEVEL = max(COMPANY_LEVELS.keys())

# 随机事件列表 (已更新为动态范围数值)
# effect_type:
#   - 'scaled_fixed': 效果值 = 随机基础值 * 公司等级
#   - 'income_multiple': 效果值 = 随机小时倍数 * 公司时薪
# value_range: [最小值, 最大值] 的波动范围
RANDOM_EVENTS = [
    # --- 正面事件 ---
    {
        "type": "positive",
        "effect_type": "scaled_fixed",
        "value_range": [1000, 4000],
        "message": "🎉 市场风口！由于准确预测了市场趋势，您的公司获得了一笔 {value} 金币的额外奖金！",
        "weight": 30,
    },
    {
        "type": "positive",
        "effect_type": "income_multiple",
        "value_range": [4, 12],
        "message": "📈 技术革新！公司研发取得突破，生产效率大增，立即获得了相当于 {value} 小时挂机收益的奖励！",
        "weight": 15,
    },
    {
        "type": "positive",
        "effect_type": "scaled_fixed",
        "value_range": [3000, 10000],
        "message": "🤝 贵人相助！一位神秘的投资人看好您的公司潜力，并注入了一笔 {value} 金币的资金！",
        "weight": 7,
    },
    # --- 负面事件 ---
    {
        "type": "negative",
        "effect_type": "scaled_fixed",
        "value_range": [3000, 5000],
        "message": "📉 设备折旧！部分老旧设备需要维护，您为此支付了 {value} 金币的维修费用。",
        "weight": 20,
    },
    {
        "type": "negative",
        "effect_type": "scaled_fixed",
        "value_range": [6000, 12000],
        "message": "⚖️ 税务审查！因一笔账目不清，您不得不补缴了 {value} 金币的税款和罚金。",
        "weight": 10,
    },
    {
        "type": "negative",
        "effect_type": "income_multiple",
        "value_range": [4, 12],
        "message": "🚧 供应链危机！上游原材料价格暴涨，导致您损失了相当于 {value} 小时挂机收益的金额！",
        "weight": 25,
    },
    {
        "type": "negative",
        "effect_type": "level_change",
        "value_range": [-1, -1],
        "message": "🔥 灭顶之灾！一场突如其来的金融危机席卷了全球，您的公司遭受重创，资不抵债...",
        "weight": 1,
    },
]

# +++ 新增：上市公司专属随机事件 +++
# effect_type:
#  - 'stock_price_change': 效果值是一个百分比, 如 0.05 代表股价上涨5%
#  - 'earnings_modifier': 效果值是一个乘数, 为下次财报提供加成/减益
#  - 'scaled_fixed': 效果同私有公司，但基础值更高
PUBLIC_RANDOM_EVENTS = [
    # --- 正面事件 ---
    {
        "type": "positive",
        "effect_type": "stock_price_change",
        "value_range": [0.03, 0.08],
        "message": "📈 重大利好！公司核心产品取得重大技术突破，市场信心大增，股价立即上涨 {value:.1%}！",
        "weight": 35,
    },
    {
        "type": "positive",
        "effect_type": "earnings_modifier",
        "value_range": [1.15, 1.30],
        "message": "🤝 合作愉快！成功与巨头签订长期合同，分析师上调了您的盈利预期，下次财报将获得 {value:.1%} 的额外加成！",
        "weight": 30,
    },
    {
        "type": "positive",
        "effect_type": "scaled_fixed",
        "value_range": [50000, 150000],
        "message": "⚖️ 胜诉！您赢得了与竞争对手的重大专利诉讼，获得了一笔 {value:,.0f} 金币的一次性赔偿金！",
        "weight": 5,
    },
    # --- 负面事件 ---
    {
        "type": "negative",
        "effect_type": "stock_price_change",
        "value_range": [-0.09, -0.04],
        "message": "📉 重大利空！公司被爆出财务丑闻，声誉受损，股价应声下跌 {value:.1%}！",
        "weight": 35,
    },
    {
        "type": "negative",
        "effect_type": "earnings_modifier",
        "value_range": [0.75, 0.90],
        "message": "🚧 供应链危机！您的关键供应商宣布破产，生产成本激增，下次财报表现将受到 -{value:.1%} 的严重影响！",
        "weight": 30,
    },
    {
        "type": "negative",
        "effect_type": "scaled_fixed",
        "value_range": [80000, 200000],
        "message": "💸 天价罚单！因违反相关法规，您被监管机构处以 {value:,.0f} 金币的巨额罚款。",
        "weight": 5,
    },
]


# 部门系统解锁等级
DEPARTMENT_UNLOCK_LEVEL = 2

# --- 公司部门等级配置 (加成强化版) ---
# cost: 升到这一级所需的费用
# operations_bonus: 运营部加成 (时薪乘数)
# research_bonus: 研发部加成 (升级/研发成本折扣)
# pr_bonus: 公关部加成 (PVP与事件优势系数)
DEPARTMENT_LEVELS = {
    # 等级: { "cost": 升级费用, "operations_bonus": 1.0 + 百分比, "research_bonus": 1.0 - 百分比, ... }
    1: {
        "cost": 15000,
        "operations_bonus": 1.04,
        "research_bonus": 0.98,
        "pr_bonus": 1.03,
    },  # +4% | -2% | +3%
    2: {
        "cost": 40000,
        "operations_bonus": 1.08,
        "research_bonus": 0.96,
        "pr_bonus": 1.07,
    },  # +8% | -4% | +7%
    3: {
        "cost": 120000,
        "operations_bonus": 1.13,
        "research_bonus": 0.94,
        "pr_bonus": 1.12,
    },  # +13%| -6% | +12%
    4: {
        "cost": 350000,
        "operations_bonus": 1.18,
        "research_bonus": 0.92,
        "pr_bonus": 1.18,
    },  # +18%| -8% | +18%
    5: {
        "cost": 800000,
        "operations_bonus": 1.24,
        "research_bonus": 0.89,
        "pr_bonus": 1.25,
    },  # +24%| -11%| +25%
    6: {
        "cost": 2000000,
        "operations_bonus": 1.30,
        "research_bonus": 0.86,
        "pr_bonus": 1.32,
    },  # +30%| -14%| +32%
    7: {
        "cost": 5000000,
        "operations_bonus": 1.37,
        "research_bonus": 0.83,
        "pr_bonus": 1.40,
    },  # +37%| -17%| +40%
    8: {
        "cost": 9000000,
        "operations_bonus": 1.45,
        "research_bonus": 0.80,
        "pr_bonus": 1.48,
    },  # +45%| -20%| +48%
    9: {
        "cost": 18000000,
        "operations_bonus": 1.53,
        "research_bonus": 0.77,
        "pr_bonus": 1.57,
    },  # +53%| -23%| +57%
    10: {
        "cost": 35000000,
        "operations_bonus": 1.60,
        "research_bonus": 0.75,
        "pr_bonus": 1.65,
    },  # +60%| -25%| +65%
}

# --- 新增：玩家互动配置 ---
# 人才挖角基础费用范围
TALENT_POACH_COST_HOURS_RANGE = [5, 12]  # 挖角成本通常比刺探更高
# 商业间谍基础费用范围
INDUSTRIAL_ESPIONAGE_COST_HOURS_RANGE = [4, 10]

# +++ 新增：人才挖角效果配置 +++
# 效果持续时间范围 (小时)
TALENT_POACH_DURATION_HOURS_RANGE = [3, 8]
# 挖角成功方获得的时薪乘数范围
TALENT_POACH_BUFF_POTENCY_RANGE = [1.05, 1.25]
# 挖角失败方获得的时薪乘数范围
TALENT_POACH_DEBUFF_POTENCY_RANGE = [0.75, 0.90]

# +++ 新增：人才挖角成功率配置 +++
TALENT_POACH_BASE_CHANCE = 0.50  # 基础成功率 50%
TALENT_POACH_PR_FACTOR = 0.5  # 每点PR加成差值对成功率的影响系数 (就是你说的那个0.2)
TALENT_POACH_CHANCE_MIN = 0.05  # 最低成功率 10%
TALENT_POACH_CHANCE_MAX = 0.80  # 最高成功率 90%

# +++ 新增：商业间谍效果配置 +++
# 成功后，攻击方获得的奖励是本次行动成本的多少倍
INDUSTRIAL_ESPIONAGE_REWARD_COST_MULTIPLIER_RANGE = [1.5, 2]

# 成功后，目标方在下次升级时需要承受的成本增加百分比范围
INDUSTRIAL_ESPIONAGE_DEBUFF_POTENCY_RANGE = [1.15, 1.35]

# DEBUFF 持续时间 (秒)，设置一个较长的时间以确保其生效
INDUSTRIAL_ESPIONAGE_DEBUFF_DURATION_SECONDS = 86400 * 3

# 失败后，需要向“系统”支付的罚款倍数范围 (基于行动成本)，这笔钱会消失
INDUSTRIAL_ESPIONAGE_PENALTY_MULTIPLIER_RANGE = [1.4, 2.0]

# +++ 新增：商业间谍成功率配置 +++
ESPIONAGE_BASE_CHANCE = 0.40  # 基础成功率
# 每高一级，成功率降低3%；每低一级，成功率增加3%
ESPIONAGE_LEVEL_FACTOR = 0.03
# 公关部每高一级，成功率增加5%；每低一级，成功率降低5%
ESPIONAGE_PR_FACTOR = 0.05
ESPIONAGE_CHANCE_MIN = 0.05  # 最低成功率
ESPIONAGE_CHANCE_MAX = 0.80  # 最高成功率

# +++ 新增：部门改名配置 +++
DEPARTMENT_RENAME_COST = 20000  # 部门改名基础费用

# +++ 新增：玩家互动效果上限 +++
# 目标身上最多能承受的“人才流失”类debuff数量 (来自人才挖角)
MAX_INCOME_DEBUFFS_ON_TARGET = 3
# 目标身上最多能承受的“技术封锁”类debuff数量 (来自商业间谍)
MAX_COST_DEBUFFS_ON_TARGET = 2


# -------------公司上市配置 (V2 - 平衡版)------------------
# 公司上市的最低等级要求
IPO_MIN_LEVEL = 7

# 上市需要支付的手续费 (设定为Lv.5升Lv.6的费用左右)
IPO_LISTING_FEE = 4000000

# 上市成功后，给予董事长的一次性融资奖励 (必须远大于手续费)
IPO_CAPITAL_INJECTION = 3000000

# 公司上市时发行的总股本数量 (固定值，需要与股票插件的API匹配)
IPO_TOTAL_SHARES = 100000

# 财报结算周期 (秒)，例如 1天 = 86400
EARNINGS_REPORT_CYCLE_SECONDS = 86400 / 2

# 财报基础表现的波动范围 [最小值, 最大值]
# 1.0 代表不好不坏，大于1.0代表超预期，小于1.0代表不及预期
EARNINGS_PERFORMANCE_RANGE = [0.85, 1.25]

# 每次成功的商业攻击对上市公司股价造成的即时负面影响 (百分比)
STOCK_IMPACT_FROM_ATTACK = -0.03  # 股价降低3%

# +++ 新增：公司退市配置 +++
# 退市（私有化）时，需要在当前市价基础上支付的溢价率
# 0.1 代表需要支付 120% 的总市值作为退市费用
DELISTING_PREMIUM_RATE = 0.2

# 上市公司分红时的股息率
# 0.03 代表分红金额 = 总市值 * 3%
DIVIDEND_YIELD_RATE = 0.03
# 分红计算中，来自“公司等级”部分的权重
LEVEL_DIVIDEND_WEIGHT = 0.6  # 60% 的分红来自于稳定的等级收益

# 分红计算中，来自“公司市值”部分的权重
MARKET_CAP_DIVIDEND_WEIGHT = 1 - LEVEL_DIVIDEND_WEIGHT  # 40% 的分红来自于波动的市值表现

# +++ 新增：公司行动 (Corporate Actions) 配置 +++

# 公司行动的指令冷却时间 (秒)，例如 1天 = 86400
CORPORATE_ACTION_COOLDOWN_SECONDS = 86400 / 2

# 可用的公司行动列表
# cost_market_cap_pct: 消耗金币 = 公司当前市值 * 这个百分比
# earnings_bonus_range: [最小值, 最大值] 为下次财报提供的业绩修正系数
CORPORATE_ACTIONS = {
    # 内部关键字: {显示名称, 成本, 效果范围}
    "invest": {
        "name": "投资研发",
        "cost_market_cap_pct": 0.05,  # 消耗市值的 5%
        "earnings_bonus_range": [1.10, 1.25],  # 提供 +10% 到 +25% 的财报加成
    },
    "market": {
        "name": "市场营销",
        "cost_market_cap_pct": 0.02,  # 消耗市值的 2%
        "earnings_bonus_range": [1.05, 1.12],  # 提供 +5% 到 +12% 的财报加成
    },
}

# +++ 新增：人才挖角防御成功 Buff 配置 +++
# 成功防御后，目标方获得的“团队凝聚力”效果
# 效果：在持续时间内，增加公关(PR)系数，使其更难被挖角
TALENT_POACH_DEFENSE_BUFF = {
    "duration_seconds": 86400,  # 持续24小时
    "potency": 1.20,  # 临时增加 12% 的公关系数
    "effect_type": "pr_modifier",
    "description": "团队凝聚力",
}
# +++ 新增：商业间谍防御成功 Buff 配置 +++
# 成功防御后，目标方获得的“安保强化”效果
# 效果：在持续时间内，任何针对该公司的商业刺探基础成功率降低
ESPIONAGE_DEFENSE_BUFF = {
    "duration_seconds": 86400,  # 持续24小时
    "potency": -0.15,  # 刺探成功率修正 -15%
    "effect_type": "espionage_chance_modifier",
    "description": "安保强化",
}

# +++ V3 新增：上市公司被挖角debuff +++
# 效果：为下次财报提供一个一次性的负面修正
TALENT_POACH_PUBLIC_DEBUFF = {
    "effect_type": "earnings_modifier",
    "value_range": [0.90, 0.98],  # 为下次财报提供 -2% 到 -10% 的减益
    "is_consumed_on_use": True,
    "description": "核心团队动荡",
}

# +++ 新增：市场公告广播配置 +++
# 在这里填入您想作为“财经频道”的QQ群号，可以填多个
BROADCAST_GROUP_IDS = ["1053208414", "1050550421", "625684997"]
