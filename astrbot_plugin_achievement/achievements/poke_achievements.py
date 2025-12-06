# astrbot_plugin_achievement/achievements/poke_achievements.py

ACHIEVEMENTS = [
    # ... (已有的 poke_1, poke_99, poke_999 成就) ...
    {
        "id": "poke_1",
        "title": "哎呀",
        "description": "戳了戳菲比",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/phoebe/pb8.png",
        "rarity": "common",
        "reward_coins": 50,
        "hidden": False,
    },
    {
        "id": "poke_99",
        "title": "再戳就要生气了！",
        "description": "累计戳了菲比99次",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/phoebe/pb4.png",
        "rarity": "epic",
        "reward_coins": 999,
        "hidden": False,
    },
    {
        "id": "poke_999",
        "title": "不要...再戳了",
        "description": "累计戳了菲比999次",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/phoebe/pb1.png",
        "rarity": "legendary",
        "reward_coins": 9999,
        "hidden": True,
    },

    # --- [新增] “帽子”专属成就 ---
    {
        "id": "hat_poked_100",
        "title": "哎呀，我的帽子",
        "description": "累计100次对菲比的帽子动手动脚",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/phoebe/pb7.png", # 建议路径，请替换为你的图标
        "rarity": "mythic",
        "reward_coins": 2500,
        "hidden": True,
    },
    {
        "id": "poke_low_favour",
        "title": "别碰我",
        "description": "在菲比好感度低于-150的时候戳了她。",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/phoebe/pb3.png", # 建议路径，请替换为你的图标
        "rarity": "epic",
        "reward_coins": -1000, # 负数奖励即为惩罚
        "hidden": True,
    },
]
