# plugins/shop_plugin/main.py (异步化改造后)

import os
from typing import Optional, Any, Dict

from ..common.services import shared_services
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .shop_database import ShopDatabase
import astrbot.api.message_components as Comp
from ..common.forwarder import Forwarder
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)


class ShopAPI:
    """异步化的商店API"""

    def __init__(self, db: ShopDatabase):
        self._db = db

    async def register_item(
        self,
        owner_plugin: str,
        item_id: str,
        name: str,
        description: str,
        price: int,
        daily_limit: int = 0,
    ):
        await self._db.add_or_update_item_definition(
            item_id, name, description, price, owner_plugin, daily_limit
        )

    async def get_user_inventory(self, user_id: str) -> list:
        return await self._db.get_user_inventory(user_id)

    async def has_item(self, user_id: str, item_id: str) -> bool:
        inventory = await self.get_user_inventory(user_id)
        return any(item["item_id"] == item_id for item in inventory)

    async def consume_item(self, user_id: str, item_id: str, quantity: int = 1) -> bool:
        logger.info(f"API调用：尝试为用户 {user_id} 消耗物品 {item_id} x{quantity}")
        return await self._db.remove_item_from_user(user_id, item_id, quantity)

    async def get_item_details(self, identifier: str) -> Optional[Dict[str, Any]]:
        """
        根据物品的ID或名称获取其详细信息。
        这是让其他插件了解商品属性的核心API。
        :param identifier: 物品的英文ID或中文名称。
        :return: 包含商品所有属性的字典，如果找不到则返回 None。
        """
        # 优先按名称查找，因为可能更常用
        item = await self._db.get_item_by_name(identifier)
        if not item:
            # 如果按名称找不到，再按ID查找
            item = await self._db.get_item_by_id(identifier)
        return item

    async def get_today_purchase_count(self, user_id: str, item_id: str) -> int:
        """
        [新增] 查询用户今日购买某限购商品的数量。
        这是实现跨插件共享限购额度的核心API。
        """
        return await self._db.get_today_purchase_count(user_id, item_id)

    async def log_purchase(self, user_id: str, item_id: str, quantity: int):
        """
        [新增] 记录用户的购买行为，用于限购统计。
        当其他插件通过金币交易"购买"了限购商品时，应调用此API来消耗额度。
        """
        await self._db.log_purchase(user_id, item_id, quantity)


@register("shop_plugin", "Gemini", "一个提供商品交易服务的核心插件", "1.0.0")
class ShopPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._economy_api: Optional[Any] = None
        self._nickname_api: Optional[Any] = None
        # 注意这里的路径，传递的是插件所在目录
        self.db = ShopDatabase(os.path.dirname(__file__))
        self.api = ShopAPI(self.db)
        self.forwarder = Forwarder()
        shared_services["shop_api"] = self.api
        logger.info("商店服务API已成功注册到全局服务。")

    @filter.on_astrbot_loaded()
    async def _async_init(self):
        """在AstrBot加载完成后，获取经济插件的API实例。"""
        self._economy_api = shared_services.get("economy_api")
        if not self._economy_api:
            logger.warning("商店插件未能连接到经济系统API！部分功能可能无法使用。")
        else:
            logger.info("商店插件已成功连接到经济系统API。")

    async def terminate(self):
        """插件终止时，安全关闭数据库连接。"""
        logger.info("正在终止商店插件并关闭数据库连接...")
        await self.db.close()

    def _get_economy_api(self) -> Optional[Any]:
        """获取经济API的实例。"""
        if not self._economy_api:
            self._economy_api = shared_services.get("economy_api")
        return self._economy_api

    def _get_nickname_api(self) -> Optional[Any]:
        """获取昵称API的实例。"""
        if not self._nickname_api:
            self._nickname_api = shared_services.get("nickname_api")
        return self._nickname_api

    @filter.command("商店", alias={"shop"})
    async def show_shop(self, event: AstrMessageEvent):
        items = await self.db.get_all_items()
        if not items:
            yield event.plain_result("商店里空空如也，还没有任何商品上架哦~")
            return

        reply = "--- 🛍️ 欢迎光临小店 🛍️ ---\n"
        for i, item in enumerate(items, 1):
            reply += f"[{i}] {item['name']} - {item['price']}金币"
            # vvvvv 在商店列表中显示限购信息 vvvvv
            if item.get("daily_limit", 0) > 0:
                reply += f" (每日限购{item['daily_limit']})"
            reply += "\n"
            # ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
            reply += f"  功能: {item['description']}\n\n"
        reply += "--------------------\n"
        reply += "使用 `/购买 <编号/名称> [数量]` 来购买。\n"
        reply += "使用 `/赠送 <编号/名称> [数量] @用户` 来赠送。"  # 新增指令提示
        reply = self.forwarder.create_from_text(reply)
        yield event.chain_result([reply])

    @filter.command("购买")
    async def buy_item(
        self, event: AstrMessageEvent, identifier: str, quantity: int = 1
    ):
        if quantity <= 0:
            yield event.plain_result("购买数量必须是大于0的整数。")
            return

        user_id = event.get_sender_id()

        item_to_buy = None
        if identifier.isdigit():
            all_items = await self.db.get_all_items()
            item_index = int(identifier)
            if 1 <= item_index <= len(all_items):
                item_to_buy = all_items[item_index - 1]

        if not item_to_buy:
            item_to_buy = await self.db.get_item_by_name(identifier)

        if not item_to_buy:
            yield event.plain_result(
                f"抱歉，没有找到编号或名称为“{identifier}”的商品。"
            )
            return

        # vvvvv 核心逻辑：每日限购检查 vvvvv
        daily_limit = item_to_buy.get("daily_limit", 0)
        if daily_limit > 0:
            current_purchase_count = await self.db.get_today_purchase_count(
                user_id, item_to_buy["item_id"]
            )
            if current_purchase_count + quantity > daily_limit:
                reply = (
                    f"❌ 购买失败！\n"
                    f"【{item_to_buy['name']}】每人每日限购 {daily_limit} 次。\n"
                    f"您今天已购买 {current_purchase_count} 次，本次还可购买 {daily_limit - current_purchase_count} 次。"
                )
                yield event.plain_result(reply)
                return
        # ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

        price = item_to_buy["price"]
        total_price = price * quantity

        eco_api = self._get_economy_api()
        if not eco_api:
            yield event.plain_result("抱歉，支付系统出现问题，暂时无法购买。")
            return

        balance = await eco_api.get_coins(user_id)
        if balance < total_price:
            yield event.plain_result(
                f"购买失败，您的金币不足！\n需要 {total_price} 金币 ({price} x {quantity})，您只有 {balance} 金币。"
            )
            return

        reason = f"购买商品: {item_to_buy['name']} x{quantity}"
        success = await eco_api.add_coins(user_id, -total_price, reason)

        if success:
            await self.db.add_item_to_user(user_id, item_to_buy["item_id"], quantity)
            # vvvvv 核心逻辑：记录购买历史 vvvvv
            await self.db.log_purchase(user_id, item_to_buy["item_id"], quantity)
            # ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
            new_balance = await eco_api.get_coins(user_id)
            yield event.plain_result(
                f"🎉 购买成功！\n您获得了【{item_to_buy['name']}】x{quantity}！\n💰 剩余金币: {new_balance}"
            )
        else:
            yield event.plain_result("购买失败，支付网关繁忙，请稍后再试。")

    @filter.command("赠送", alias={"give"})
    async def gift_item(
        self, event: AstrMessageEvent, content: str
    ):  # content 参数将不再被直接使用
        """
        处理为他人付款购买物品的指令。
        通过接收完整的指令内容(`content`)并手动解析，来解决@用户导致参数识别错误的问题。
        """
        # --- 步骤1: 解析接收者 (逻辑不变) ---
        recipient_id = None
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                recipient_id = str(component.qq)
                break

        if not recipient_id:
            yield event.plain_result("赠送失败，请使用 `@` 指定要为谁购买。")
            return

        sender_id = event.get_sender_id()
        if sender_id == recipient_id:
            yield event.plain_result("您可以通过 `/购买` 命令为自己购买。")
            return

        # <--- vvvvvvvvvv 最终BUG修复 vvvvvvvvvv --->
        # --- 步骤2: 从 event 对象手动重构参数，不再依赖 content ---

        # 1. 提取所有纯文本部分并拼接
        plain_text = "".join(
            str(c.text) for c in event.message_obj.message if isinstance(c, Comp.Plain)
        ).strip()

        # 2. 分割文本，并移除命令本身（如 /赠送）
        all_parts = plain_text.split()
        if not all_parts:
            yield event.plain_result("请输入要赠送的物品名称或编号。")
            return

        # 移除命令词，剩下的就是纯参数
        args_parts = all_parts[1:]

        # 3. 使用上一版已修正的解析逻辑来处理重构后的完整参数
        numbers = []
        text_parts = []
        for part in args_parts:
            # 在这里不再需要过滤@用户，因为我们只拼接了 Plain 文本
            if part.isdigit():
                numbers.append(int(part))
            else:
                text_parts.append(part)

        item_name_str = " ".join(text_parts)
        identifier = None
        quantity = 1

        if item_name_str:
            identifier = item_name_str
            if len(numbers) >= 1:
                quantity = numbers[0]
        elif numbers:
            if len(numbers) == 1:
                identifier = str(numbers[0])
                quantity = 1
            elif len(numbers) == 2:
                identifier = str(numbers[0])
                quantity = numbers[1]
            else:
                yield event.plain_result(
                    "指令格式不正确。当只使用数字时，请按 `商品编号 数量` 或 `商品编号` 的格式提供。"
                )
                return

        if not identifier:
            yield event.plain_result("请输入要赠送的物品名称或编号。")
            return

        if quantity <= 0:
            yield event.plain_result("赠送数量必须是大于0的整数。")
            return

        # --- 步骤3: 物品查找逻辑 (逻辑不变) ---
        item_to_gift = None
        if identifier.isdigit():
            all_items = await self.db.get_all_items()
            item_index = int(identifier)
            if 1 <= item_index <= len(all_items):
                item_to_gift = all_items[item_index - 1]

        if not item_to_gift:
            item_to_gift = await self.db.get_item_by_name(identifier)
        # <--- ^^^^^^^^^^^ 最终BUG修复 ^^^^^^^^^^^ --->

        if not item_to_gift:
            yield event.plain_result(
                f"抱歉，没有找到编号或名称为“{identifier}”的商品。"
            )
            return

        # --- 后续所有逻辑，包括支付、发货、发送消息等，都保持不变 ---
        item_id = item_to_gift["item_id"]
        item_name = item_to_gift["name"]

        daily_limit = item_to_gift.get("daily_limit", 0)
        if daily_limit > 0:
            purchase_count = await self.db.get_today_purchase_count(sender_id, item_id)
            if purchase_count + quantity > daily_limit:
                reply = (
                    f"❌ 赠送失败！\n"
                    f"【{item_name}】属于限购商品，赠送行为将消耗您自己的购买额度。\n"
                    f"每人每日限购 {daily_limit} 次，您今天已用额度 {purchase_count} 次，"
                    f"剩余额度不足以赠送 {quantity} 次。"
                )
                yield event.plain_result(reply)
                return

        price = item_to_gift["price"]
        total_price = price * quantity
        eco_api = self._get_economy_api()
        if not eco_api:
            yield event.plain_result("抱歉，支付系统出现问题，暂时无法赠送。")
            return

        sender_balance = await eco_api.get_coins(sender_id)
        if sender_balance < total_price:
            yield event.plain_result(
                f"赠送失败，您的金币不足！\n需要支付 {total_price} 金币，您只有 {sender_balance} 金币。"
            )
            return

        reason = f"为用户 {recipient_id} 购买商品: {item_name} x{quantity}"  # quantity现在是正确的了
        success = await eco_api.add_coins(sender_id, -total_price, reason)

        if success:
            await self.db.add_item_to_user(recipient_id, item_id, quantity)
            if daily_limit > 0:
                await self.db.log_purchase(sender_id, item_id, quantity)

            recipient_display_name = recipient_id
            nickname_api = self._get_nickname_api()
            if nickname_api:
                custom_nickname = await nickname_api.get_nickname(recipient_id)
                if custom_nickname:
                    recipient_display_name = custom_nickname

            if recipient_display_name == recipient_id:
                recipient_profile = await eco_api.get_user_profile(recipient_id)
                if recipient_profile and recipient_profile.get("nickname"):
                    recipient_display_name = recipient_profile["nickname"]

            new_balance = await eco_api.get_coins(sender_id)
            # 这里的 quantity 也将正确显示
            yield event.plain_result(
                f"✅ 赠送成功！\n您已为用户【{recipient_display_name}】购买了【{item_name}】x{quantity}！\n💰 您支付了 {total_price} 金币，剩余 {new_balance} 金币。"
            )

        else:
            yield event.plain_result("赠送失败，支付网关繁忙，请稍后再试。")

    @filter.command("我的背包", alias={"我的物品", "背包"})
    async def show_inventory(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        inventory = await self.db.get_user_inventory(user_id)
        if not inventory:
            yield event.plain_result("您的背包是空的。")
            return

        reply = "--- 🎒 您的背包 🎒 ---\n"
        for item in inventory:
            reply += f"【{item['name']}】 x{item['quantity']}\n"
            reply += f"  功能: {item['description']}\n"
        reply += "--------------------"
        yield event.plain_result(reply)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("上架")
    async def add_item_interactive(self, event: AstrMessageEvent):
        """[管理员]通过引导式交互上架或更新一个商品。"""
        # 用于在会话中存储商品信息的字典
        item_data = {}
        try:
            # 启动会话
            yield event.plain_result(
                "好的，我们开始上架新商品。\n第一步，请输入商品的【英文ID】(例如 a_cool_item)，输入 `退出` 可随时取消。"
            )

            @session_waiter(timeout=120)  # 2分钟无操作则自动超时
            async def item_creation_waiter(
                controller: SessionController, event: AstrMessageEvent
            ):
                user_input = event.message_str.strip()

                # 随时可以退出
                if user_input in ["退出", "取消"]:
                    await event.send(event.plain_result("操作已取消。"))
                    controller.stop()
                    return

                # 根据 item_data 中已有的键来判断当前进行到哪一步
                if "item_id" not in item_data:
                    # 步骤1：接收英文ID
                    if " " in user_input:  # 简单验证
                        await event.send(
                            event.plain_result("❌ 英文ID不能包含空格，请重新输入。")
                        )
                        return  # 继续等待，不重置超时
                    item_data["item_id"] = user_input
                    await event.send(
                        event.plain_result(
                            f"ID已设为: {user_input}\n第二步，请输入商品的【中文名称】。"
                        )
                    )

                elif "name" not in item_data:
                    # 步骤2：接收中文名称
                    item_data["name"] = user_input
                    await event.send(
                        event.plain_result(
                            f"名称已设为: {user_input}\n第三步，请输入商品的【价格】(纯数字)。"
                        )
                    )

                elif "price" not in item_data:
                    # 步骤3：接收价格
                    try:
                        price = int(user_input)
                        if price < 0:
                            raise ValueError
                        item_data["price"] = price
                        await event.send(
                            event.plain_result(
                                f"价格已设为: {price}\n第四步，请输入【每日限购次数】(输入 0 代表不限购)。"
                            )
                        )
                    except ValueError:
                        await event.send(
                            event.plain_result(
                                "❌ 价格必须是一个非负整数，请重新输入。"
                            )
                        )

                elif "daily_limit" not in item_data:
                    # 步骤4：接收每日限购
                    try:
                        limit = int(user_input)
                        if limit < 0:
                            raise ValueError
                        item_data["daily_limit"] = limit
                        await event.send(
                            event.plain_result(
                                f"每日限购已设为: {limit}\n最后一步，请输入商品的【功能描述】。"
                            )
                        )
                    except ValueError:
                        await event.send(
                            event.plain_result(
                                "❌ 限购次数必须是一个非负整数，请重新输入。"
                            )
                        )

                elif "description" not in item_data:
                    # 步骤5：接收描述并最终确认
                    item_data["description"] = user_input

                    # 构建确认信息
                    confirm_text = (
                        "---------- 请确认商品信息 ----------\n"
                        f"英文ID: {item_data['item_id']}\n"
                        f"商品名称: {item_data['name']}\n"
                        f"价格: {item_data['price']} 金币\n"
                        f"每日限购: {'不限购' if item_data['daily_limit'] == 0 else item_data['daily_limit']}\n"
                        f"功能描述: {item_data['description']}\n"
                        "------------------------------------\n"
                        "请回复【确认】以完成上架，回复其他任何内容则取消。"
                    )
                    await event.send(event.plain_result(confirm_text))

                else:
                    # 步骤6：处理最终确认
                    if user_input == "确认":
                        await self.db.add_or_update_item_definition(
                            owner_plugin="shop_plugin",  # 表示由商店管理员直接添加
                            item_id=item_data["item_id"],
                            name=item_data["name"],
                            description=item_data["description"],
                            price=item_data["price"],
                            daily_limit=item_data["daily_limit"],
                        )
                        await event.send(
                            event.plain_result(
                                f"✅ 操作成功！商品【{item_data['name']}】已成功上架/更新。"
                            )
                        )
                    else:
                        await event.send(event.plain_result("操作已取消。"))

                    controller.stop()  # 无论成功与否，结束会话
                    return

                # 如果会话没有在上面结束，就保持会话并重置超时时间
                controller.keep(timeout=120, reset_timeout=True)

            # 启动会话等待器
            await item_creation_waiter(event)

        except TimeoutError:
            yield event.plain_result("操作超时，已自动取消上架流程。")
        except Exception as e:
            logger.error(f"交互式上架商品时发生错误: {e}")
            yield event.plain_result("发生内部错误，请联系机器人管理员。")
        finally:
            event.stop_event()  # 阻止事件继续传播

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("下架")
    async def remove_item(self, event: AstrMessageEvent, identifier: str):
        """[管理员]下架一个商品。"""
        item_to_remove = None
        if identifier.isdigit():
            all_items = await self.db.get_all_items()
            item_index = int(identifier)
            if 1 <= item_index <= len(all_items):
                item_to_remove = all_items[item_index - 1]

        if not item_to_remove:
            item_to_remove = await self.db.get_item_by_name(identifier)
            if not item_to_remove and hasattr(self.db, "get_item_by_id"):
                item_to_remove = await self.db.get_item_by_id(identifier)

        if not item_to_remove:
            yield event.plain_result(f"❌ 找不到要下架的商品：“{identifier}”。")
            return

        item_id = item_to_remove["item_id"]
        item_name = item_to_remove["name"]

        # 调用新的数据库方法并处理返回状态
        status = await self.db.remove_item_definition(item_id)

        if status == "success":
            yield event.plain_result(f"✅ 商品【{item_name}】已成功从商店下架。")
        elif status == "in_use":
            yield event.plain_result(
                f"❌ 下架失败！\n原因：仍有玩家的背包中持有【{item_name}】。请等待玩家消耗完毕后再尝试。"
            )
        elif status == "not_found":
            # 这种情况理论上不应该发生，因为我们已经提前找到了商品
            logger.warning(f"下架逻辑异常：找到了商品 {item_name}，但删除时却未找到。")
            yield event.plain_result("❌ 下架时发生同步错误，请稍后再试。")

    @filter.command("物品信息", alias={"查看物品"})
    async def show_item_info(self, event: AstrMessageEvent, identifier: str):
        """查询指定商品的详细信息。"""
        # 使用我们刚刚添加到API的新方法来获取信息
        item_details = await self.api.get_item_details(identifier)

        if not item_details:
            yield event.plain_result(
                f"❌ 未在商店中找到编号或名称为“{identifier}”的物品。"
            )
            return

        # 格式化输出
        limit_text = (
            "不限购"
            if item_details["daily_limit"] == 0
            else str(item_details["daily_limit"])
        )
        reply = (
            f"---------- 物品详情 ----------\n"
            f"🔹 **名称**: {item_details['name']}\n"
            f"🔸 **ID**: {item_details['item_id']}\n"
            f"💰 **价格**: {item_details['price']} 金币\n"
            f"📅 **每日限购**: {limit_text}\n"
            f"📜 **描述**: {item_details['description']}\n"
            f"🔌 **来源**: {item_details['owner_plugin']}\n"
            f"--------------------------------"
        )
        yield event.plain_result(reply)
