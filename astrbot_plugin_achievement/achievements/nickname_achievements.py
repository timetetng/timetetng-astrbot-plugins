# astrbot_plugin_achievement/achievements/nickname_achievements.py
# 定义与昵称系统联动的成就

# --- 检查函数定义 ---
async def check_has_nickname(apis: dict, user_id: str) -> bool:
    """检查用户是否成功设置过昵称"""
    nickname_api = apis.get("nickname_api")
    if not nickname_api:
        return False
    stats = await nickname_api.get_user_stats(user_id)
    # 只要成功设置过一次（无论当前是否拥有昵称），都算达成
    return stats and stats.get("success_count", 0) > 0


async def check_rename_success_3(apis: dict, user_id: str) -> bool:
    """检查用户改名成功次数是否超过3次"""
    nickname_api = apis.get("nickname_api")
    if not nickname_api:
        return False
    stats = await nickname_api.get_user_stats(user_id)
    # 确保用户有统计数据，并且成功次数 > 3
    return stats and stats.get("success_count", 0) > 3


async def check_rename_success_10(apis: dict, user_id: str) -> bool:
    """检查用户改名成功次数是否超过10次"""
    nickname_api = apis.get("nickname_api")
    if not nickname_api:
        return False
    stats = await nickname_api.get_user_stats(user_id)
    return stats and stats.get("success_count", 0) > 10


async def check_rename_fail_10(apis: dict, user_id: str) -> bool:
    """检查用户改名失败次数是否超过10次"""
    nickname_api = apis.get("nickname_api")
    if not nickname_api:
        return False
    stats = await nickname_api.get_user_stats(user_id)
    return stats and stats.get("fail_count", 0) > 10


# --- 成就列表定义 ---

ACHIEVEMENTS = [
    {
        "id": "nickname_first_success",
        "title": "我有名字了",
        "description": "成功地为自己设置了一个昵称。",
        "icon_path": "https://zh.minecraft.wiki/images/Name_Tag_JE2_BE2.png?12b41",
        "rarity": "common",
        "reward_coins": 50,
        "check_func": check_has_nickname,
    },
    {
        "id": "nickname_success_3",
        "title": "菲比我是谁",
        "description": "改名超过3次。",
        "icon_path": "https://zh.minecraft.wiki/images/Name_Tag_JE2_BE2.png?12b41",
        "rarity": "rare",
        "reward_coins": 100,
        "check_func": check_rename_success_3,
    },
    {
        "id": "nickname_success_10",
        "title": "你是谁来着？",
        "description": "改名超过10次。",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/rename.jpg",
        "rarity": "legendary",
        "reward_coins": 500,
        "check_func": check_rename_success_10,
        "hidden": True,
    },
    {
        "id": "nickname_fail_10",
        "title": "别试了...",
        "description": "改名失败超过10次。",
        "icon_path": "https://img.icons8.com/fluency/96/cancel.png",
        "rarity": "legendary",
        "reward_coins": 500,
        "check_func": check_rename_fail_10,
        "hidden": True,
    },
]
