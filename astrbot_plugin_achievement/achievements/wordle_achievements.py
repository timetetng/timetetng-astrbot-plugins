# astrbot_plugin_achievement/achievements/wordle_achievements.py
# 定义与猜单词游戏联动的成就

# --- 检查函数定义 ---


async def check_wordle_wins_20(apis: dict, user_id: str) -> bool:
    """检查用户猜单词胜利次数是否超过20次"""
    wordle_api = apis.get("wordle_api")
    if not wordle_api:
        return False
    stats = await wordle_api.get_user_stats(user_id)
    return stats and stats.get("win_count", 0) > 20


async def check_wordle_wins_100(apis: dict, user_id: str) -> bool:
    """检查用户猜单词胜利次数是否超过100次"""
    wordle_api = apis.get("wordle_api")
    if not wordle_api:
        return False
    stats = await wordle_api.get_user_stats(user_id)
    return stats and stats.get("win_count", 0) > 100


async def check_wordle_dividend_5(apis: dict, user_id: str) -> bool:
    """检查用户猜单词获得分红次数是否超过5次"""
    wordle_api = apis.get("wordle_api")
    if not wordle_api:
        return False
    stats = await wordle_api.get_user_stats(user_id)
    return stats and stats.get("dividend_count", 0) > 5


# --- 成就列表定义 ---

ACHIEVEMENTS = [
    {
        "id": "wordle_win_20",
        "title": "猜单词糕手",
        "description": "猜对单词20次以上",
        "icon_path": "https://img.icons8.com/fluency/96/trophy.png",
        "rarity": "epic",
        "reward_coins": 500,
        "check_func": check_wordle_wins_20,
        "hidden": False,
    },
    {
        "id": "wordle_win_100",
        "title": "君英语本当上手",
        "description": "猜对单词100次以上",
        "icon_path": "https://img.icons8.com/fluency/96/books.png",
        "rarity": "mythic",
        "reward_coins": 10000,
        "check_func": check_wordle_wins_100,
        "hidden": True,
    },
    {
        "id": "wordle_dividend_5",
        "title": "你是好人",
        "description": "在猜单词中获得分红超过5次",
        "icon_path": "https://img.icons8.com/fluency/96/collaboration.png",
        "rarity": "rare",
        "reward_coins": 1000,
        "check_func": check_wordle_dividend_5,
    },
    {
        "id": "wordle_first_try_win",
        "title": "忘关？",
        "description": "在猜单词的第一次机会就猜中",
        "icon_path": "https://img.51miz.com/Element/00/77/20/13/ba2c86f3_E772013_a26152bb.png",
        "rarity": "mythic",
        "reward_coins": 6666,
        "hidden": True,
    },
]
