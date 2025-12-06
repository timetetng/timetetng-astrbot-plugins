# astrbot_plugin_achievement/achievements/economy_achievements.py
# 定义经济系统成就

# self.RARITY_NAMES = {
#     'common': "普通", 'rare': "稀有", 'epic': "史诗",
#     'legendary': "传说", 'mythic': "神话",
#     'miracle': "奇迹", 'flawless': "无瑕"
# }

async def check_first_coin(apis: dict, user_id: str) -> bool:
    """检查是否拥有大于0的金币"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False
    return await economy_api.get_coins(user_id) > 0

async def check_become_rich(apis: dict, user_id: str) -> bool:
    """检查金币是否达到10000"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False
    coins = await economy_api.get_coins(user_id)
    return coins >= 10000

async def check_first_millionaire(apis: dict, user_id: str) -> bool:
    """检查用户金币是否达到一百万"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False
    # 假设 get_coins 是一个异步方法
    return await economy_api.get_coins(user_id) >= 1_000_000

async def check_first_10M(apis: dict, user_id: str) -> bool:
    """检查用户金币是否达到一千万"""
    economy_api = apis.get("economy_api")
    if not economy_api:
        return False
    # 假设 get_coins 是一个异步方法
    return await economy_api.get_coins(user_id) >= 10_000_000

# 必须提供一个名为 ACHIEVEMENTS 的列表，其中包含所有成就的定义字典
ACHIEVEMENTS = [
    {
        "id": "economy_first_coin",
        "title": "第一桶金",
        "description": "通过签到或其他方式获得你的第一枚金币。",
        "icon_path": "https://i.mcmod.cn/item/icon/128x128/6/63128.png?v=1",
        "rarity": "common",  # 稀有度，需要与你的图片生成器配置对应
        "reward_coins": 1000,
        "check_func": check_first_coin, # 关联检查函数
    },
    {
        "id": "have10K",
        "title": "万元户",
        "description": "你的金币总额超过了10000！",
        "icon_path": "https://i.mcmod.cn/item/icon/128x128/6/63128.png?v=1",
        "rarity": "rare",
        "reward_coins": 500,
        "check_func": check_become_rich,
    },
    {
        "id": "have1M",
        "title": "百万富豪",
        "description": "拥有一百万金币",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/金锭.png",
        "rarity": "legendary",
        "reward_coins": 10000,
        "check_func": check_first_millionaire,
    },
    {
        "id": "have10M",
        "title": "千万大亨",
        "description": "拥有一千万金币",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/绿宝石.png",
        "rarity": "legendary",
        "reward_coins": 100000,
        "check_func": check_first_10M,
    },
    {
        "id": "world_first_millionaire",
        "title": "天选之人",
        "description": "首个拥有一百万金币",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/绿宝石.png",
        "rarity": "legendary",
        "reward_coins": 100000,
        "check_func": check_first_millionaire,
        "unique": True,  # 标记为唯一
        "hidden": True,  # 明确标记为隐藏，用于控制显示
    },
    {
        "id": "world_first_10M",
        "title": "没钱，是什么感觉呢",
        "description": "拥有一千万金币",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/钻石.png",
        "rarity": "miracle",
        "reward_coins": 500000,
        "check_func": check_first_10M,
        "unique": True,  # 标记为唯一
        "hidden": True,  # 明确标记为隐藏，用于控制显示
    },
]


