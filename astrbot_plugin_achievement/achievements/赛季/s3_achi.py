# s3 限定成就
ACHIEVEMENTS = [
    # --- S3 赛季成就 ---
    {
        "id": "s3_rank1",
        "title": "钞能力MAX",
        "description": "S3赛季总资产第一",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/下界合金锭.png",
        "rarity": "flawless",  # 使用最高稀有度
        "reward_coins": 0,  # 可以设定奖励，也可以不设
        "check_func": None,
        "unique": True,
        "hidden": True,  # 关键点：设置为None，系统将永远不会自动检查它
    },
    {
        "id": "s3_rank2",
        "title": "差亿点点",
        "description": "S3赛季总资产第二",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/钻石.png",
        "rarity": "miracle",
        "reward_coins": 0,
        "check_func": None,
        "unique": True,
        "hidden": True,
    },
    {
        "id": "s3_rank3",
        "title": "铜牌收藏家",
        "description": "S3赛季总资产第三",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/绿宝石.png",
        "rarity": "miracle",
        "reward_coins": 0,
        "check_func": None,
        "unique": True,
        "hidden": True,
    },
    {
        "id": "s3_top10",
        "title": "排行榜常客",
        "description": "在S3赛季的总资产排行榜上名列前十",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/金锭.png",
        "rarity": "legendary",
        "reward_coins": 0,
        "check_func": None,
        "hidden": True,
    },
    {
        "id": "s3_oldplayer",
        "title": "又看一集",
        "description": "S3赛季老玩家",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/ykyj.png",
        "rarity": "epic",
        "reward_coins": 233,
        "check_func": None,
        "hidden": True,
    },
]
