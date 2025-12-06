# astrbot_plugin_achievement/achievements/bank_achievements.py

async def check_balance_100k(apis: dict, user_id: str) -> bool:
    """检查用户银行存款是否达到10万。"""
    bank_api = apis.get("bank_api")
    if not bank_api:
        return False
    balance = await bank_api.get_bank_asset_value(user_id)
    return balance >= 100000

async def check_balance_1m(apis: dict, user_id: str) -> bool:
    """检查用户银行存款是否达到100万。"""
    bank_api = apis.get("bank_api")
    if not bank_api:
        return False
    balance = await bank_api.get_bank_asset_value(user_id)
    return balance >= 1000000

async def check_balance_10m(apis: dict, user_id: str) -> bool:
    """检查用户银行存款是否达到1000万。"""
    bank_api = apis.get("bank_api")
    if not bank_api:
        return False
    balance = await bank_api.get_bank_asset_value(user_id)
    return balance >= 10000000

ACHIEVEMENTS = [
    {
        "id": "bank_first_deposit",
        "title": "理财第一步",
        "description": "完成了第一笔银行存款",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/bank.png", # 请替换为你的图标
        "rarity": "common",
        "reward_coins": 100,
        "hidden": False,
        "check_func": None,
    },
    {
        "id": "bank_balance_100k",
        "title": "十万富翁",
        "description": "银行存款达到了100,000",
        "icon_path": "https://img.icons8.com/?size=128&id=uza7MgwSIbLC&format=png", # 请替换为你的图标
        "rarity": "rare",
        "reward_coins": 1000,
        "check_func": check_balance_100k,
    },
    {
        "id": "bank_balance_1m",
        "title": "理财糕手",
        "description": "银行存款达到了1,000,000",
        "icon_path": "data/plugins/achievements/assets/bank_1m.png", # 请替换为你的图标
        "rarity": "epic",
        "reward_coins": 10000,
        "check_func": check_balance_1m,
    },
    {
        "id": "bank_balance_10m",
        "title": "银行大客户！",
        "description": "银行存款达到了10,000,000",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/财富.png", # 请替换为你的图标
        "rarity": "legendary",
        "reward_coins": 50000,
        "hidden": True,
        "check_func": check_balance_10m,
    },
    {
        "id": "bank_loan_overdue_3_days",
        "title": "你还不还？？",
        "description": "贷款超过3天未还",
        "icon_path": "data/plugins/astrbot_plugin_achievement/assets/icons/欠款.png", # 请替换为你的图标
        "rarity": "rare",
        "reward_coins": 0,
        "hidden": False,
    },
    {
        "id": "bank_fixed_deposit_success",
        "title": "时间的价值",
        "description": "成功完成至少一周的定期存款",
        "icon_path": "data/plugins/achievements/assets/icon", # 请替换为你的图标
        "rarity": "epic",
        "reward_coins": 2000,
        "hidden": False,
    },
]
