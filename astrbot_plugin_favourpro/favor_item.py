# favor_item.py

from typing import List, Dict, Any
from astrbot.api import logger

FAVOR_ITEMS: List[Dict[str, Any]] = [
    {
        "item_id": "favour_pizza",
        "name": "热海皇梨披萨",
        "price": 1000,
        "description": "玛格烈特小姐亲手烤制，菲比的最爱。",
        "daily_limit": 2,
        "effect": {"type": "add_favour", "value": 5}
    },
    {
        "item_id": "favour_cake",
        "name": "小蛋糕",
        "price": 500,
        "description": "一份精致的甜点，能让任何人的心情都变好一点点。",
        "daily_limit": 2,
        "effect": {"type": "add_favour", "value": 2}
    },
    {
        "item_id": "favour_ticket",
        "name": "声骸乐园门票",
        "price": 3000,
        "description": "菲比休假之余喜欢去的地方，那里有很多可爱的声骸。",
        "daily_limit": 1,
        "effect": {"type": "add_favour", "value": 7}
    },
    {
        "item_id": "favour_reset_card",
        "name": "好感度重置卡",
        "price": 5000,
        "description": "一张神奇的卡片，使用后能将你与菲比的关系重置到最初的状态。",
        "daily_limit": 1,
        "effect": {"type": "reset_favour", "value": None}
    },
    # --- 新增道具 ---
    {
        "item_id": "favour_lock_card_day",
        "name": "关系锁定卡(一日)",
        "price": 5200,
        "description": "可以将你与菲比的关系锁定24小时，不会随对话更新。",
        "daily_limit": 0,  # 无限制
        "effect": {"type": "lock_relationship", "duration_seconds": 86400} # 24 * 60 * 60
    },
    {
        "item_id": "favour_lock_card_week",
        "name": "关系锁定卡(月卡)",
        "price": 131400,
        "description": "可以将你与菲比的关系锁定一月，不会随对话更新。",
        "daily_limit": 0,  # 无限制
        "effect": {"type": "lock_relationship", "duration_seconds": 2592000}
    }
]

class FavorItemManager:
    def __init__(self):
        # 新增：保留原始列表用于有序显示
        self.items_list = FAVOR_ITEMS
        self.items_map = {item['item_id']: item for item in FAVOR_ITEMS}
        logger.info(f"成功加载 {len(self.items_map)} 种好感度道具。")

    def get_item(self, item_id: str) -> Dict[str, Any] | None:
        """根据 item_id 获取道具信息"""
        return self.items_map.get(item_id)

    async def register_all_items(self, shop_api):
        """将所有定义好的道具注册到商店API中"""
        if not shop_api:
            logger.error("商店API未找到，无法注册好感度道具。")
            return 0
        
        count = 0
        for item in FAVOR_ITEMS:
            try:
                await shop_api.register_item(
                    owner_plugin="FavourPro",
                    item_id=item['item_id'],
                    name=item['name'],
                    description=item['description'],
                    price=item['price'],
                    daily_limit=item['daily_limit']
                )
                logger.info(f"成功注册道具：{item['name']}")
                count += 1
            except Exception as e:
                logger.error(f"注册道具 {item['name']} 失败: {e}")
        return count