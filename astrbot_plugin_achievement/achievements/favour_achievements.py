# astrbot_plugin_achievement/achievements/favour_achievements.py
# 定义好感度系统相关成就

# --- 检查函数定义 ---

async def check_favour_520(apis: dict, user_id: str) -> bool:
    """检查好感度是否达到520"""
    favour_api = apis.get("favour_pro_api")
    if not favour_api:
        return False
    state = await favour_api.get_user_state(user_id)
    return state and state.get("favour", 0) >= 520

async def check_favour_1314(apis: dict, user_id: str) -> bool:
    """检查好感度是否达到1314"""
    favour_api = apis.get("favour_pro_api")
    if not favour_api:
        return False
    state = await favour_api.get_user_state(user_id)
    return state and state.get("favour", 0) >= 1314

async def check_favour_9999(apis: dict, user_id: str) -> bool:
    """检查好感度是否达到9999"""
    favour_api = apis.get("favour_pro_api")
    if not favour_api:
        return False
    state = await favour_api.get_user_state(user_id)
    return state and state.get("favour", 0) >= 9999

async def check_favour_negative(apis: dict, user_id: str) -> bool:
    """检查好感度是否为负数"""
    favour_api = apis.get("favour_pro_api")
    if not favour_api:
        return False
    state = await favour_api.get_user_state(user_id)
    return state and state.get("favour", 0) < 0

async def check_favour_hated(apis: dict, user_id: str) -> bool:
    """检查好感度是否低于-200"""
    favour_api = apis.get("favour_pro_api")
    if not favour_api:
        return False
    state = await favour_api.get_user_state(user_id)
    return state and state.get("favour", 0) <= -200

async def check_favour_rank_first(apis: dict, user_id: str) -> bool:
    """检查是否为好感度排行榜第一名"""
    favour_api = apis.get("favour_pro_api")
    if not favour_api:
        return False
    ranking = await favour_api.get_favour_ranking(limit=1)
    # 确保排行榜不为空，且榜首是当前用户
    return ranking and ranking[0].get("user_id") == user_id

async def check_relationship_beloved(apis: dict, user_id: str) -> bool:
    """检查与用户的关系是否为「挚爱」"""
    favour_api = apis.get("favour_pro_api")
    if not favour_api:
        return False
    state = await favour_api.get_user_state(user_id)
    # 确保状态存在，并且关系字段的值是 "挚爱"
    return state and state.get("relationship") == "挚爱之人"
# --- 成就列表定义 ---

ACHIEVEMENTS = [
    {
        "id": "elationship_beloved",
        "title": "挚爱之人",
        "description": "与菲比成为挚爱之人",
        "icon_path": "https://img.icons8.com/fluency/96/like.png",
        "rarity": "legendary",
        "reward_coins": 520,
        "check_func": check_relationship_beloved,
        "hidden": True,
    },
    {
        "id": "favour_520",
        "title": "最重要的人",
        "description": "好感度超过了520",
        "icon_path": "https://img.icons8.com/fluency/96/pixel-heart.png",
        "rarity": "epic",
        "reward_coins": 520,
        "check_func": check_favour_520,
    },
    {
        "id": "favour_1314",
        "title": "一生一世",
        "description": "好感度超过了1314",
        "icon_path": "https://img.icons8.com/fluency/96/pixel-heart.png",
        "rarity": "legendary",
        "reward_coins": 1314,
        "check_func": check_favour_1314,
    },
    {
    "id": "favour_9999",
    "title": "天长地久",  # 或"永恒誓约"
    "description": "好感度达到了9999，这份情谊将永恒长存",
    "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/infity.png",  # 可用无限符号图标
    "rarity": "flawless",  # 使用最高稀有度
    "reward_coins": 9999,
    "check_func": check_favour_9999,
    "hidden": True,
},
    {
        "id": "favour_negative",
        "title": "希望只是误会",
        "description": "好感度跌破了0点。",
        "icon_path": "https://img.icons8.com/?size=96&id=13770&format=png",
        "rarity": "rare",
        "reward_coins": 0,
        "check_func": check_favour_negative,
        "hidden": True,
    },
    {
        "id": "favour_hated",
        "title": "你究竟对我做了什么...",
        "description": "好感度低于-200",
        "icon_path": "https://img.icons8.com/?size=100&id=5K8b6OPStFN8&format=png",
        "rarity": "epic",
        "reward_coins": -1000,
        "check_func": check_favour_hated,
        "hidden": True,
    },
    {
        "id": "favour_rank_first",
        "title": "万千宠爱",
        "description": "首个登上好感度排行榜榜首的人。",
        "icon_path": "https://img.icons8.com/fluency/96/crown.png",
        "rarity": "miracle",
        "reward_coins": 520,
        "check_func": check_favour_rank_first,
        "unique": True,
        "hidden": True,
    },
]
