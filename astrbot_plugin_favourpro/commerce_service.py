from datetime import datetime
from .const import DAILY_GIFT_LIMIT, DEFAULT_STATE
from .database import DatabaseManager
from .api import FavourProAPI

class CommerceService:
    def __init__(self, db_manager: DatabaseManager, api: FavourProAPI, shared_services: dict, item_manager):
        self.db = db_manager
        self.api = api
        self.shared = shared_services
        self.item_manager = item_manager

    async def _get_services(self):
        """获取商店和经济API"""
        return self.shared.get("shop_api"), self.shared.get("economy_api")

    async def process_gift(self, event, item_name: str, quantity: int) -> str:
        """处理送礼逻辑"""
        shop_api, eco_api = await self._get_services()
        if not shop_api or not eco_api:
            return "错误：商店或经济系统未启用。"

        sender_id = event.get_sender_id()
        
        # 1. 查找物品
        item_info = next((i for i in self.item_manager.items_map.values() if i["name"] == item_name), None)
        if not item_info:
            return f"菲比不认识名为“{item_name}”的礼物呢…"
        
        item_id = item_info["item_id"]
        
        # 2. 交易处理 (优先背包，其次购买)
        success, msg, from_inventory, total_price = await self._handle_transaction(
            sender_id, item_id, quantity, item_name, shop_api, eco_api, is_gift=True
        )
        if not success:
            return msg

        # 3. 应用效果
        return await self._apply_gift_effect(
            sender_id, item_info, quantity, from_inventory, total_price
        )

    async def process_use_item(self, event, item_name: str, quantity: int) -> str:
        """处理道具使用逻辑"""
        shop_api, eco_api = await self._get_services()
        if not shop_api or not eco_api:
            return "错误：商店或经济系统未启用。"

        sender_id = event.get_sender_id()
        item_info = next((i for i in self.item_manager.items_map.values() if i["name"] == item_name), None)
        if not item_info:
            return f"找不到名为“{item_name}”的道具。"

        # 检查是否为功能性道具
        if item_info["effect"]["type"] not in ["lock_relationship", "reset_favour"]:
            return f"“{item_name}”不能直接使用，试试 /赠送礼物 ？"

        # 交易
        success, msg, from_inventory, total_price = await self._handle_transaction(
            sender_id, item_info["item_id"], quantity, item_name, shop_api, eco_api, is_gift=False
        )
        if not success:
            return msg

        # 应用效果
        return await self._apply_item_effect(
            sender_id, item_info, quantity, from_inventory, total_price
        )

    async def _handle_transaction(self, user_id, item_id, quantity, item_name, shop_api, eco_api, is_gift):
        """统一处理库存扣除或金币购买"""
        # 检查背包
        inventory = await shop_api.get_user_inventory(user_id)
        inv_item = next((i for i in inventory if i["item_id"] == item_id), None)

        if inv_item and inv_item.get("quantity", 0) >= quantity:
            if await shop_api.consume_item(user_id, item_id, quantity):
                return True, "", True, 0
            return False, "背包扣除失败", False, 0

        # 购买流程
        details = await shop_api.get_item_details(item_id)
        if not details:
            return False, "该物品未上架", False, 0

        # 每日限购检查
        daily_limit = details.get("daily_limit", 0)
        if daily_limit > 0:
            today_count = await shop_api.get_today_purchase_count(user_id, item_id)
            if today_count + quantity > daily_limit:
                return False, f"超过每日限购！剩余额度：{daily_limit - today_count}", False, 0

        total_price = details["price"] * quantity
        balance = await eco_api.get_coins(user_id)
        if balance < total_price:
            return False, f"金币不足！需要 {total_price}，拥有 {balance}", False, 0

        reason = f"{'赠送' if is_gift else '使用'}: {item_name} x{quantity}"
        if await eco_api.add_coins(user_id, -total_price, reason):
            if daily_limit > 0:
                await shop_api.log_purchase(user_id, item_id, quantity)
            return True, "", False, total_price
        
        return False, "支付失败", False, 0

    async def _apply_gift_effect(self, user_id, item_info, quantity, from_inv, price):
        effect = item_info["effect"]
        effect_type = effect["type"]
        
        if effect_type == "add_favour":
            state = await self.db.get_user_state(user_id)
            today_str = datetime.now().strftime("%Y-%m-%d")
            
            if state.get("last_update_date") != today_str:
                state["daily_gift_gain"] = 0
            
            if state.get("daily_gift_gain", 0) >= DAILY_GIFT_LIMIT:
                return f"你{'消耗' if from_inv else '购买'}了{item_info['name']}，但今日礼物好感已达上限！"
                
            raw_gain = effect["value"] * quantity
            actual_gain = min(raw_gain, DAILY_GIFT_LIMIT - state.get("daily_gift_gain", 0))
            
            state["daily_gift_gain"] = state.get("daily_gift_gain", 0) + actual_gain
            state["last_update_date"] = today_str
            await self.api.add_favour(user_id, actual_gain)
            
            cost_msg = "背包消耗" if from_inv else f"消费 {price} 金币"
            return f"赠送成功！菲比很喜欢！\n好感度 +{actual_gain}。\n💰 {cost_msg}"
            
        elif effect_type == "reset_favour":
            if quantity > 1: return "重置卡只能用一张。"
            await self._reset_user(user_id)
            return "一切都回到了原点..."
            
        return "未知效果"

    async def _apply_item_effect(self, user_id, item_info, quantity, from_inv, price):
        effect = item_info["effect"]
        effect_type = effect["type"]
        
        if effect_type == "lock_relationship":
            duration = effect.get("duration_seconds", 0) * quantity
            state = await self.db.get_user_state(user_id)
            now = datetime.now().timestamp()
            current_expiry = state.get("relationship_lock_until", 0)
            
            new_expiry = max(now, current_expiry) + duration
            state["relationship_lock_until"] = new_expiry
            await self.db.update_user_state(user_id, state)
            
            end_time = datetime.fromtimestamp(new_expiry).strftime("%Y-%m-%d %H:%M:%S")
            return f"锁定成功！关系已锁定至 {end_time}。"
            
        elif effect_type == "reset_favour":
            if quantity > 1: return "重置卡只能用一张。"
            await self._reset_user(user_id)
            return "一切都回到了原点..."
            
        return "道具使用成功。"

    async def _reset_user(self, user_id):
        await self.api.set_favour(user_id, DEFAULT_STATE["favour"])
        await self.api.set_attitude(user_id, DEFAULT_STATE["attitude"])
        await self.api.set_relationship(user_id, DEFAULT_STATE["relationship"])
