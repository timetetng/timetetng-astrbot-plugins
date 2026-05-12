import asyncio
import json
import os
import random
import time
import uuid

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType, Platform
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

# 尝试导入全局共享服务，用于插件间API通信
try:
    from ..common.services import shared_services
except (ImportError, ModuleNotFoundError):
    logger.warning("无法导入 'shared_services'。API依赖功能将不可用。请检查插件结构。")
    shared_services = None


@register(
    "astrbot_poke",
    "timetetng",
    "将戳一戳转换为LLM消息，并集成计数、好感度与成就系统",
    "1.3.1",
    "repo url",
)
class PokeToLLMPlugin(Star):
    # 定义一个唯一的键，用于在消息中做标记，防止无限循环
    RAW_MESSAGE_POKE_TAG_KEY = "__is_astrbot_poke_event__"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.achievement_api = None
        self.favour_pro_api = None

        # 初始化统计数据（数据库）
        self.stats_dir = "data/poke_stats"
        os.makedirs(self.stats_dir, exist_ok=True)
        self.stats_file = os.path.join(self.stats_dir, "stats.json")
        self.poke_stats: dict[str, dict[str, int]] = self._load_stats()

        # 启动异步任务，用于安全地获取其他插件的API
        asyncio.create_task(self._async_init())

        logger.info("PokeToLLMPlugin 已初始化并准备就绪。")

        self.llm_prefix: str = self.config.get("llm_trigger_prefix", "/")
        self.poke_message_templates: list[str] = self.config.get(
            "poke_message_templates",
            [
                "{username} 戳了一下你",
                "{username} 对你做了个鬼脸",
                "{username} 敲了敲你的脑袋",
            ],
        )
        logger.info(
            f"PokeToLLMPlugin 配置加载：LLM前缀='{self.llm_prefix}', 模板数量={len(self.poke_message_templates)}"
        )

    # --- 数据库操作方法 ---
    def _load_stats(self) -> dict[str, dict[str, int]]:
        """从文件加载统计数据"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"[PokeToLLMPlugin] 加载戳一戳统计数据失败: {e}")
        return {}

    def _save_stats(self):
        """保存统计数据到文件"""
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self.poke_stats, f, ensure_ascii=False, indent=4)
        except OSError as e:
            logger.error(f"[PokeToLLMPlugin] 保存戳一戳统计数据失败: {e}")

    # --- 异步初始化与API获取 ---
    async def _async_init(self):
        """异步初始化方法，用于加载依赖的API。"""
        logger.info("PokeToLLMPlugin: 正在等待依赖API加载...")
        self.achievement_api, self.favour_pro_api = await asyncio.gather(
            self.wait_for_api("achievement_api"), self.wait_for_api("favour_pro_api")
        )

        if self.achievement_api:
            logger.info("PokeToLLMPlugin: 已成功连接到成就系统API。")
        else:
            logger.warning(
                "PokeToLLMPlugin: 未能获取成就系统API，成就解锁功能将不可用。"
            )

        if self.favour_pro_api:
            logger.info("PokeToLLMPlugin: 已成功连接到好感度系统API。")
        else:
            logger.warning("PokeToLLMPlugin: 未能获取好感度系统API，相关功能将受限。")

    async def wait_for_api(self, api_name: str, timeout: int = 30):
        """通用API等待函数"""
        start_time = asyncio.get_event_loop().time()
        while True:
            if shared_services and (api := shared_services.get(api_name)):
                return api
            if asyncio.get_event_loop().time() - start_time > timeout:
                logger.warning(f"等待API '{api_name}' 超时。")
                return None
            await asyncio.sleep(1)

    # --- 辅助方法 ---
    async def _get_username(self, event: AstrMessageEvent, user_id: str) -> str:
        """获取用户的昵称，优先显示群名片。"""
        if isinstance(event, AiocqhttpMessageEvent):
            try:
                if event.message_obj.group_id:
                    user_info = await event.bot.call_action(
                        action="get_group_member_info",
                        group_id=int(event.message_obj.group_id),
                        user_id=int(user_id),
                        no_cache=True,
                    )
                    return user_info.get("card", "") or user_info.get(
                        "nickname", user_id
                    )
                user_info = await event.bot.call_action(
                    action="get_stranger_info", user_id=int(user_id), no_cache=True
                )
                if user_info:
                    return user_info.get("nickname", user_id)
            except Exception as e:
                logger.error(f"[PokeToLLMPlugin] 获取用户 {user_id} 昵称失败: {e}")
        return user_id

    # --- 核心事件处理 ---
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_poke_event(self, event: AstrMessageEvent):
        """监听所有消息事件，专门捕获并处理戳一戳通知。"""
        raw_event_data = event.message_obj.raw_message

        # 防止处理自己注入的消息
        if isinstance(raw_event_data, dict) and raw_event_data.get(
            self.RAW_MESSAGE_POKE_TAG_KEY
        ):
            return

        # 判断是否为戳一戳事件
        is_poke_notice = (
            isinstance(raw_event_data, dict)
            and raw_event_data.get("post_type") == "notice"
            and raw_event_data.get("notice_type") == "notify"
            and raw_event_data.get("sub_type") == "poke"
            and str(raw_event_data.get("target_id")) == str(event.message_obj.self_id)
        )

        if not is_poke_notice:
            return

        user_id = str(raw_event_data.get("user_id"))

        # --- 计数与多维度成就触发 (已修正缩进) ---

        # 1. 查询好感度并触发“别碰我”成就
        if self.favour_pro_api and self.achievement_api:
            user_state = await self.favour_pro_api.get_user_state(user_id)
            if user_state and user_state.get("favour", 0) < -150:
                was_unlocked = await self.achievement_api.unlock_achievement(
                    user_id=user_id, achievement_id="poke_low_favour"
                )
                if was_unlocked:
                    logger.info(
                        f"用户 {user_id} 在好感度低于-150时戳一戳，成功解锁成就 [poke_low_favour]。"
                    )

        # 2. 随机选择一个将要使用的模板
        template = random.choice(
            self.poke_message_templates or ["{username} 戳了一下你"]
        )

        # 3. 获取该用户的统计数据字典
        user_data = self.poke_stats.setdefault(user_id, {})

        # 4. [分类计数] 检查是否为“帽子”相关事件
        if "帽子" in template:
            hat_poke_count = user_data.get("hat_poke_count", 0) + 1
            user_data["hat_poke_count"] = hat_poke_count
            if self.achievement_api and hat_poke_count == 100:
                await self.achievement_api.unlock_achievement(
                    user_id=user_id, achievement_id="hat_poked_100"
                )

        # 5. [总数计数] 处理总戳一戳次数的计数
        total_poke_count = user_data.get("count", 0) + 1
        user_data["count"] = total_poke_count
        if self.achievement_api:
            achievement_to_check = {1: "poke_1", 99: "poke_99", 999: "poke_999"}
            if total_poke_count in achievement_to_check:
                ach_id = achievement_to_check[total_poke_count]
                await self.achievement_api.unlock_achievement(
                    user_id=user_id, achievement_id=ach_id
                )

        # 6. 统一保存数据
        self._save_stats()

        # --- 消息转换与注入逻辑 ---
        username = await self._get_username(event, user_id)
        formatted_message = template.format(username=username)
        llm_message_str = self.llm_prefix + formatted_message
        group_id = raw_event_data.get("group_id")

        new_abm = AstrBotMessage()
        new_abm.self_id = str(event.message_obj.self_id)
        new_abm.session_id = event.session_id
        new_abm.message_id = uuid.uuid4().hex
        new_abm.group_id = str(group_id) if group_id else ""
        new_abm.sender = MessageMember(user_id=user_id, nickname=username)
        new_abm.message = [Comp.Plain(llm_message_str)]
        new_abm.message_str = llm_message_str
        new_abm.timestamp = int(time.time())
        new_abm.type = (
            MessageType.GROUP_MESSAGE if group_id else MessageType.FRIEND_MESSAGE
        )

        temp_raw_message = raw_event_data.copy()
        temp_raw_message[self.RAW_MESSAGE_POKE_TAG_KEY] = True
        new_abm.raw_message = temp_raw_message

        platform_adapter: Platform = self.context.get_platform(
            event.get_platform_name()
        )
        asyncio.create_task(platform_adapter.handle_msg(new_abm))
        logger.info(f"[PokeToLLMPlugin] 已成功注入戳一戳消息: '{llm_message_str}'")

    @filter.on_llm_response(priority=100)
    async def on_poke_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """
        高优先级拦截戳一戳的LLM回复，主要目的：
        1. 检查休眠插件状态，如果休眠则阻止回复。
        2. 检查LLM回复是否为空，为空则阻止回复。
        如果检查通过，则不进行任何操作，让事件继续传播给其他插件（如表情包插件）。
        """
        # 检查是否为本插件注入的戳一戳消息的回复
        if not (
            isinstance(event.message_obj.raw_message, dict)
            and event.message_obj.raw_message.get(self.RAW_MESSAGE_POKE_TAG_KEY)
        ):
            return  # 不是戳一戳的回复，直接放行，不作处理

        # 关键检查1：检查休眠插件状态
        if (
            hasattr(event, "_sleep_plugin_should_be_silenced")
            and event._sleep_plugin_should_be_silenced
        ):
            logger.info("[PokeToLLMPlugin] 检测到休眠状态，已阻止戳一戳的回复。")
            event.stop_event()  # 阻止事件继续传播，消息不会被发送
            return

        # 关键检查2：检查LLM回复是否为空
        if not resp.completion_text:
            logger.warning("[PokeToLLMPlugin] LLM对戳一戳返回了空内容，不发送。")
            event.stop_event()  # 阻止事件继续传播，消息不会被发送
            return

    async def terminate(self):
        """插件卸载时调用的方法。"""
        logger.info("PokeToLLMPlugin 已卸载。")
