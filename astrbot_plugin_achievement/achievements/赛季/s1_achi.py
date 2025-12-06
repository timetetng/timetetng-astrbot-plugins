# s1 限定成就
ACHIEVEMENTS = [
    # --- S1 赛季成就 ---
    {
        "id": "s1_rank1",
        "title": "登峰造极",
        "description": "S1赛季总资产第一",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/下界合金锭.png",
        "rarity": "flawless",  # 使用最高稀有度
        "reward_coins": 0, # 可以设定奖励，也可以不设
        "check_func": None,
        "unique": True,
        "hidden": True,    # 关键点：设置为None，系统将永远不会自动检查它
    },
    {
        "id": "s1_rank2",
        "title": "既生瑜，何生亮",
        "description": "S1赛季总资产第二",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/钻石.png",
        "rarity": "miracle",
        "reward_coins": 0,
        "check_func": None,
        "unique": True,
        "hidden": True,
    },
    {
        "id": "s1_rank3",
        "title": "这里风景也不错",
        "description": "S1赛季总资产第三",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/绿宝石.png",
        "rarity": "miracle",
        "reward_coins": 0,
        "check_func": None,
        "unique": True,
        "hidden": True,
    },
    {
        "id": "s1_top10",
        "title": "声名显赫",
        "description": "在S1赛季的总资产排行榜上名列前十",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/minecraft/金锭.png",
        "rarity": "legendary",
        "reward_coins": 0,
        "check_func": None,
        "hidden": True,
    },
    {
        "id": "s1_oldplayer",
        "title": "我...重生了?",
        "description": "S1赛季老玩家",
        "icon_path": "https://zh.minecraft.wiki/images/Invicon_Red_Bed.png?ef6e0",
        "rarity": "epic",
        "reward_coins": 0,
        "check_func": None,
        "hidden": True,
    }
]
