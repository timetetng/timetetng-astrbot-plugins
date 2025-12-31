import asyncio
from typing import Optional
import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api import AstrBotConfig, logger
from datetime import datetime

# 尝试导入共享服务，避免报错
try:
    from ..common.services import shared_services
except ImportError:
    shared_services = {} 

# 导入同级模块
from .database import DatabaseManager
from .api import FavourProAPI
from .logic_service import LogicService
from .commerce_service import CommerceService
from .favor_item import FavorItemManager

@register(
    "FavourPro",
    "TimeXingjian",
    "一个由AI驱动的、包含好感度、态度和关系的多维度交互系统",
    "3.0.0",
    "https://github.com/TimeXingjian/astrbot_plugin_favour_pro"
)
class FavourProPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 初始化各个组件
        self.db_path = StarTools.get_data_dir() / "favour_pro.db"
        self.db_manager = DatabaseManager(self.db_path)
        
        self.api = FavourProAPI(self.db_manager)
        self.item_manager = FavorItemManager()
        
        # 逻辑服务
        self.logic_service = LogicService(self.db_manager)
        
        # 商业服务 (延迟注入 shared_services)
        self.commerce_service = CommerceService(
            self.db_manager, self.api, shared_services, self.item_manager
        )

        # 异步初始化
        asyncio.create_task(self._initialize())

    async def _initialize(self):
        await self.db_manager.init_db()
        if shared_services is not None:
            shared_services["favour_pro_api"] = self.api
            logger.info("FavourProAPI 已成功注册到共享服务。")

    @property
    def session_based(self) -> bool:
        return bool(self.config.get("session_based", False))

    def _get_session_id(self, event: AstrMessageEvent) -> Optional[str]:
        return event.unified_msg_origin if self.session_based else None
    
    def _is_admin(self, event: AstrMessageEvent) -> bool:
        return event.role == "admin"

    # --- 核心事件监听 ---

    @filter.on_llm_request(priority=100)
    async def add_context_prompt(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.db_manager._db: return
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        
        state = await self.db_manager.get_user_state(user_id, session_id)
        prompt = self.logic_service.get_context_prompt(state)
        req.system_prompt += prompt

    @filter.on_llm_response(priority=101)
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        if not self.db_manager._db: return
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        
        new_text = await self.logic_service.process_llm_response(
            user_id, session_id, resp.completion_text
        )
        resp.completion_text = new_text

    # --- 用户命令 ---

    @filter.command("好感度", alias={"favor", "好感"})
    async def query_status(self, event: AstrMessageEvent):
        if not self.db_manager._db: yield event.plain_result("初始化中..."); return
        
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        state = await self.db_manager.get_user_state(user_id, session_id)
        
        msg = f"我眼中的你：\n好感度：{state['favour']}\n关系：{state['relationship']}\n印象：{state['attitude']}"
        
        lock_ts = state.get("relationship_lock_until", 0)
        if lock_ts > datetime.now().timestamp():
            end_time = datetime.fromtimestamp(lock_ts).strftime("%Y-%m-%d %H:%M:%S")
            msg += f"\n🔒 关系锁定至 {end_time}。"
            
        yield event.plain_result(msg)

    @filter.command("好感度排行", alias={"好感榜","好感排行"})
    async def show_favour_ranking(self, event: AstrMessageEvent):
        """显示好感度排行榜（带昵称修复版）"""
        ranking = await self.api.get_favour_ranking()
        if not ranking:
            yield event.plain_result("还没有人上榜哦~")
            return

        # 1. 收集所有需要查询的 User ID
        user_ids = [u['user_id'] for u in ranking]
        display_names = {}

        # 2. 尝试从共享服务获取昵称 (如果有安装 Nickname 插件)
        nickname_api = shared_services.get("nickname_api")
        if nickname_api:
            try:
                display_names = await nickname_api.get_nicknames_batch(user_ids)
            except Exception as e:
                logger.warning(f"NicknameAPI 调用失败: {e}")

        # 3. 尝试从平台 API 获取昵称 (针对 OneBot/QQ 平台)
        if event.get_platform_name() == "aiocqhttp":
            try:
                # 动态导入，防止非 OneBot 平台报错
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                
                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    for uid in user_ids:
                        # 如果已经在步骤 2 获取到了，就跳过
                        if uid in display_names:
                            continue
                            
                        try:
                            # 调用 OneBot get_stranger_info
                            info = await client.api.call_action("get_stranger_info", user_id=int(uid))
                            if info and "nickname" in info:
                                display_names[uid] = info["nickname"]
                        except Exception:
                            # 获取失败（非好友等），忽略，后续显示 ID
                            pass
            except ImportError:
                pass
        
        # 4. 构建显示文本
        lines = ["🏆 好感度排行榜"]
        for i, u in enumerate(ranking):
            uid = u['user_id']
            # 优先显示昵称，没有则显示 ID
            name = display_names.get(uid, uid) 
            lines.append(f"❤️{i+1}. {name} : {u['favour']} ({u['relationship']})")
            
        yield event.plain_result("\n".join(lines))

    @filter.command("厌恶榜", alias={"厌恶度排行", "黑名单"})
    async def show_dislike_ranking(self, event: AstrMessageEvent):
        """显示厌恶度排行榜（好感度最低的用户）"""
        # 1. 调用厌恶度接口
        ranking = await self.api.get_dislike_ranking()
        if not ranking:
            yield event.plain_result("看来菲比还没有讨厌的人呢~")
            return

        # 2. 收集所有需要查询的 User ID
        user_ids = [u['user_id'] for u in ranking]
        display_names = {}

        # 3. 尝试从共享服务获取昵称
        nickname_api = shared_services.get("nickname_api")
        if nickname_api:
            try:
                display_names = await nickname_api.get_nicknames_batch(user_ids)
            except Exception as e:
                logger.warning(f"NicknameAPI 调用失败: {e}")

        # 4. 尝试从平台 API 获取昵称 (针对 OneBot/QQ)
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    for uid in user_ids:
                        if uid in display_names: continue
                        try:
                            info = await client.api.call_action("get_stranger_info", user_id=int(uid))
                            if info and "nickname" in info:
                                display_names[uid] = info["nickname"]
                        except Exception:
                            pass
            except ImportError:
                pass
        
        # 5. 构建显示文本
        lines = ["💔 厌恶度排行榜"]
        for i, u in enumerate(ranking):
            uid = u['user_id']
            name = display_names.get(uid, uid)
            # 使用不同的 emoji 区分
            lines.append(f"👿{i+1}. {name} : {u['favour']} ({u['relationship']})")
            
        yield event.plain_result("\n".join(lines))

    @filter.command("赠送礼物", alias={"送礼物", "送礼"})
    async def gift_to_bot(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("用法: /送礼物 <礼物名> [数量]")
            return
            
        item_name = args[1]
        quantity = 1
        if len(args) > 2 and args[2].isdigit():
            quantity = int(args[2])
            
        result = await self.commerce_service.process_gift(event, item_name, quantity)
        yield event.plain_result(result)

    @filter.command("使用道具", alias={"使用"})
    async def use_item(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("用法: /使用 <道具名> [数量]")
            return

        item_name = args[1]
        quantity = 1
        if len(args) > 2 and args[2].isdigit():
            quantity = int(args[2])

        result = await self.commerce_service.process_use_item(event, item_name, quantity)
        yield event.plain_result(result)

    @filter.command("好感度商店", alias={"好感商店"})
    async def show_favor_shop(self, event: AstrMessageEvent):
        items = self.item_manager.items_list
        if not items:
            yield event.plain_result("商店空空如也~")
            return
            
        lines = ["💝 菲比的心意小铺"]
        for item in items:
            eff_type = item.get("effect", {}).get("type", "unknown")
            eff_val = item.get("effect", {}).get("value", 0)
            desc = f"好感+{eff_val}" if eff_type == "add_favour" else "特殊道具"
            
            lines.extend([
                "----------------",
                f"🎁 {item['name']}",
                f"💰 {item['price']} 金币 | {desc}",
                f"📅 限购: {item.get('daily_limit', '无')}"
            ])
        yield event.plain_result("\n".join(lines))

    @filter.command("解除关系锁定")
    async def unlock_relationship(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        state = await self.db_manager.get_user_state(user_id, session_id)
        
        if state.get("relationship_lock_until", 0) > datetime.now().timestamp():
            state["relationship_lock_until"] = 0
            await self.db_manager.update_user_state(user_id, state, session_id)
            yield event.plain_result("🔓 锁定已解除。")
        else:
            yield event.plain_result("当前未锁定。")

    # --- 管理员命令 ---

    @filter.command("设置好感")
    async def admin_set_favour(self, event: AstrMessageEvent, content: str):
        if not self._is_admin(event): return
        try:
            target_id = [c.qq for c in event.message_obj.message if isinstance(c, Comp.At)][0]
            val = int(content.split()[-1])
            await self.api.set_favour(str(target_id), val)
            yield event.plain_result(f"已设置 {target_id} 好感为 {val}")
        except:
            yield event.plain_result("用法: /设置好感 @用户 数值")

    @filter.command("刷新商店")
    async def refresh_shop(self, event: AstrMessageEvent):
        if not self._is_admin(event): return
        shop_api = shared_services.get("shop_api")
        if shop_api:
            c = await self.item_manager.register_all_items(shop_api)
            yield event.plain_result(f"已注册 {c} 个商品")
        else:
            yield event.plain_result("商店API不可用")

    async def terminate(self):
        await self.db_manager.close()
