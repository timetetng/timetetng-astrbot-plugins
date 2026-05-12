# astrbot_plugin_achievement/achievements/lottery_achievements.py
# 定义与经济系统（抽奖、运势）联动的成就

# --- 检查函数定义 ---


async def check_bad_luck_on_good_fortune(apis: dict, user_id: str) -> bool:
    """检查：连续三次在运势为大吉的情况下抽到负面奖励"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False

    history = await economy_api.get_lottery_history(user_id, limit=3)
    if len(history) < 3:
        return False

    # 检查最近3次记录是否都满足条件
    for record in history:
        if not (
            record.get("fortune_at_time") == "大吉"
            and record.get("prize_won") < record.get("bet_amount")
        ):
            return False
    return True


async def check_fortune_reversal(apis: dict, user_id: str) -> bool:
    """检查：上一次运势是大吉，下一次（最近一次）运势是凶"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False

    history = await economy_api.get_fortune_history(user_id, limit=2)
    if len(history) < 2:
        return False

    # API返回的列表，索引0是最新的记录
    latest_fortune = history[0].get("fortune_result")
    previous_fortune = history[1].get("fortune_result")

    return latest_fortune == "凶" and previous_fortune == "大吉"


async def check_good_luck_on_bad_fortune(apis: dict, user_id: str) -> bool:
    """检查：连续3次在运势为凶时抽出2以上倍率"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False

    history = await economy_api.get_lottery_history(user_id, limit=3)
    if len(history) < 3:
        return False

    for record in history:
        try:
            # 将 '2.50x' 这样的字符串转换为浮点数
            multiplier = float(record.get("multiplier", "0x").replace("x", ""))
        except (ValueError, TypeError):
            multiplier = 0.0

        if not (record.get("fortune_at_time") == "凶" and multiplier >= 2.0):
            return False
    return True


async def check_lucky_streak(apis: dict, user_id: str, streak_length: int) -> bool:
    """通用检查函数：检查连续N次抽奖结果为正面"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False

    history = await economy_api.get_lottery_history(user_id, limit=streak_length)
    if len(history) < streak_length:
        return False

    for record in history:
        if record.get("prize_won") < record.get("bet_amount"):
            return False
    return True


async def check_fucky_streak(apis: dict, user_id: str, streak_length: int) -> bool:
    """通用检查函数：检查连续N次抽奖结果为负面"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False

    history = await economy_api.get_lottery_history(user_id, limit=streak_length)
    if len(history) < streak_length:
        return False

    for record in history:
        if record.get("prize_won") >= record.get("bet_amount"):
            return False
    return True


async def check_lucky_streak_6(apis: dict, user_id: str) -> bool:
    return await check_lucky_streak(apis, user_id, 6)


async def check_lucky_streak_10(apis: dict, user_id: str) -> bool:
    return await check_lucky_streak(apis, user_id, 10)


async def check_fucky_streak_10(apis: dict, user_id: str) -> bool:
    return await check_fucky_streak(apis, user_id, 10)


# --- 成就列表定义 ---

ACHIEVEMENTS = [
    {
        "id": "lottery_bad_luck_on_good_fortune",
        "title": "大吉吗？大吉吧！",
        "description": "连续三次在运势为“大吉”的情况下抽到负面奖励",
        "icon_path": "https://img.icons8.com/?size=160&id=X5V5DMvABgCK&format=png",
        "rarity": "epic",
        "reward_coins": 250,
        "check_func": check_bad_luck_on_good_fortune,
    },
    {
        "id": "lottery_fortune_reversal",
        "title": "一念神魔",
        "description": "上一次抽取的运势是“大吉”，而紧接着下一次就是“大凶”",
        "icon_path": "https://img.icons8.com/fluency/96/yin-yang.png",
        "rarity": "rare",
        "reward_coins": 100,
        "check_func": check_fortune_reversal,
    },
    {
        "id": "lottery_good_luck_on_bad_fortune",
        "title": "逆天改命",
        "description": "连续3次在运势为“大凶”时抽出2倍以上的奖励",
        "icon_path": "https://img.icons8.com/fluency/96/muscle.png",
        "rarity": "epic",
        "reward_coins": 1000,
        "check_func": check_good_luck_on_bad_fortune,
    },
    {
        "id": "lottery_lucky_streak_6",
        "title": "开了",
        "description": "连续6次抽奖结果为正面奖励",
        "icon_path": "https://img.51miz.com/Element/00/77/20/09/b4a65fc9_E772009_8162182a.png",
        "rarity": "legendary",
        "reward_coins": 6666,
        "check_func": check_lucky_streak_6,
    },
    {
        "id": "lottery_lucky_streak_10",
        "title": "桂！",
        "description": "连续10次抽奖结果为正面奖励",
        "icon_path": "https://img.51miz.com/Element/00/77/20/13/ba2c86f3_E772013_a26152bb.png",
        "rarity": "miracle",
        "reward_coins": 100000,
        "check_func": check_lucky_streak_10,
        "hidden": True,
    },
    {
        "id": "lottery_fucky_streak_10",
        "title": "关了",
        "description": "连续10次抽奖结果为负面奖励",
        "icon_path": "https://img.icons8.com/?size=96&id=KhAF6lQhRcXx&format=png",
        "rarity": "mythic",
        "reward_coins": 1145,
        "check_func": check_fucky_streak_10,
        "hidden": True,
    },
    {
        "id": "lottery_jackpot_100x",
        "title": "一夜暴富",
        "description": "中一次大奖，且奖金是本金的100倍以上",
        "icon_path": "https://img.icons8.com/fluency/96/money-bag.png",
        "rarity": "mythic",
        "reward_coins": 10000,
        "check_func": None,
    },
    {
        "id": "lottery_holy_radiance",
        "title": "古云：圣辉",
        "description": "抽中了传说中的运势“圣辉”",
        "icon_path": "https://img.icons8.com/fluency/96/sun.png",
        "rarity": "mythic",
        "reward_coins": 10000,
        "check_func": None,
    },
    {
        "id": "lottery_near_zero_multiplier",
        "title": "其实它...比奖池难出",
        "description": "在一次抽奖中倍率低于0.01x",
        "icon_path": "https://img.icons8.com/fluency/96/wind.png",  # 找了一个风的图标来代表“空气”
        "rarity": "legendary",
        "reward_coins": 1000,
        "hidden": True,  # 这是一个隐藏成就
        "check_func": None,
    },
]
