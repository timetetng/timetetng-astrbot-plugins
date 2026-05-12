import asyncio
import glob
import hashlib
import os
import time
from typing import Any

import aiohttp  # <-- 新增
import aiosqlite
from jinja2 import Template

from astrbot.api import AstrBotConfig, logger

# astrbot imports
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Node, Plain
from astrbot.api.star import Context, Star, register

# 导入新的API类
from ..common.services import shared_services
from .achievement_api import AchievementAPI

# Local imports
from .achievement_manager import AchievementManager
from .data_manager import DataManager
from .icon_cache import IconCacheManager  # <-- 新增
from .image_generator import ImageGenerator

# 用于缓存用户上次检查的时间，实现冷却
user_last_check_time: dict[str, float] = {}


@register("achievement", "YourName", "一个模块化的成就系统", "1.0.0")
class AchievementPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.apis = {}
        self.aiohttp_session = aiohttp.ClientSession()
        # 定义缓存目录和备用图标路径
        icon_cache_dir = "data/temp/achievement_icons"
        fallback_icon_path = "data/plugins/astrbot_plugin_achievement/lock_icon.png"

        self.icon_cache_manager = IconCacheManager(
            cache_dir=icon_cache_dir,
            aiohttp_session=self.aiohttp_session,
            fallback_icon_path=fallback_icon_path,
        )
        self.data_manager = DataManager()
        self.achievement_manager = AchievementManager()
        self.image_generator = ImageGenerator(
            font_path=self.config.get("font_path"),
            icon_cache_manager=self.icon_cache_manager,
        )

        self.unique_achievement_lock = asyncio.Lock()
        self.api = AchievementAPI(self)
        # 创建一个从中文稀有度名称到英文ID的映射，方便搜索
        self.RARITY_NAMES_MAP = {
            "common": "普通",
            "rare": "稀有",
            "epic": "史诗",
            "legendary": "传说",
            "mythic": "神话",
            "miracle": "奇迹",
            "flawless": "无瑕",
        }
        self.rarity_zh_to_en = {v: k for k, v in self.RARITY_NAMES_MAP.items()}
        asyncio.create_task(self.initialize_plugin())

    async def terminate(self):
        """插件卸载时清理资源，关闭网络会话。"""
        if self.aiohttp_session and not self.aiohttp_session.closed:
            await self.aiohttp_session.close()
            logger.info("成就插件的 aiohttp session 已成功关闭。")

    async def initialize_plugin(self):
        """安全地获取API并加载成就"""
        try:
            # 1. 获取 API
            self.apis["economy_api"] = await self.wait_for_api("economy_api")
            self.apis["nickname_api"] = await self.wait_for_api("nickname_api")
            self.apis["favour_pro_api"] = await self.wait_for_api("favour_pro_api")
            self.apis["wordle_api"] = await self.wait_for_api("wordle_api")
            self.apis["bank_api"] = await self.wait_for_api("bank_api")

            # 注册API到全局服务
            shared_services["achievement_api"] = self.api
            logger.info("AchievementAPI 已成功注册到 shared_services。")

            # 2. 加载与报告逻辑
            logger.info("开始加载成就定义文件...")
            # 注意：请确保此处的路径与你的实际结构匹配
            successful_files, failed_files = self.achievement_manager.load_achievements(
                directory="data/plugins/astrbot_plugin_achievement/achievements"
            )

            if failed_files > 0:
                logger.warning(
                    f"成就文件加载完毕。成功: {successful_files}个, 失败: {failed_files}个。请检查日志。"
                )
            else:
                logger.info(f"所有成就文件加载成功 ({successful_files}个)。")

            total_achievements = len(self.achievement_manager.achievements)
            logger.info(f"插件初始化完成，共加载 {total_achievements} 个有效成就。")

        except Exception:
            logger.error("在成就插件的初始化流程中发生未知致命错误！", exc_info=True)

    async def wait_for_api(self, api_name: str, timeout: int = 30):
        """通用API等待函数"""
        logger.info(f"正在等待 {api_name} 加载...")
        start_time = asyncio.get_event_loop().time()
        while True:
            api_instance = shared_services.get(api_name)
            if api_instance:
                logger.info(f"{api_name} 已成功加载。")
                return api_instance
            if asyncio.get_event_loop().time() - start_time > timeout:
                logger.warning(f"等待 {api_name} 超时，相关功能将受限！")
                return None
            await asyncio.sleep(1)

    async def send_unlock_notification(
        self,
        user_id: str,
        user_name: str,
        achievements_data: list,
        event: AstrMessageEvent,
    ):
        """发送成就解锁通知的通用方法。"""
        final_node_content: list = []
        output_dir = "data/temp/achievements"
        os.makedirs(output_dir, exist_ok=True)

        for i, ach_data in enumerate(achievements_data):
            try:
                template_string = self.config.get("announcement_template")
                template = Template(
                    template_string, trim_blocks=True, lstrip_blocks=True
                )
                reward_text = template.render(
                    user_name=user_name,
                    achievement_title=ach_data.get("title", "未知成就"),
                    reward_coins=ach_data.get("reward_coins", 0),
                    rarity=self.achievement_manager.RARITY_NAMES.get(
                        ach_data.get("rarity", "common")
                    ),
                    uniqueness="【唯一】" if ach_data.get("unique", False) else "",
                )
                reward_text = reward_text.replace("\\n", "\n")
            except Exception as e:
                logger.error(f"渲染成就播报模板时出错: {e}")
                reward_text = f"恭喜 {user_name} 解锁了成就【{ach_data.get('title', '未知成就')}】！"

            image_filename = f"ach_{ach_data['id'].replace(':', '_')}.png"
            output_path = os.path.join(output_dir, image_filename)

            if not os.path.exists(output_path):
                logger.info(f"缓存成就图片不存在，正在生成: {output_path}")
                await self.image_generator.create_achievement_image(  # <-- 修改点
                    title=ach_data["title"],
                    description=ach_data["description"],
                    icon_path=ach_data["icon_path"],
                    rarity=ach_data["rarity"],
                    output_path=output_path,
                )

            final_node_content.append(Plain(text=reward_text))
            final_node_content.append(Image.fromFileSystem(path=output_path))
            if i < len(achievements_data) - 1:
                final_node_content.append(Plain(text="\n- - - - - - - - - - -\n"))

        if final_node_content:
            bot_uin = event.message_obj.self_id
            single_node = Node(
                uin=bot_uin, name="成就解锁通知", content=final_node_content
            )
            await event.send(event.chain_result([single_node]))

    async def _get_display_name(self, user_id: str, default_name: str) -> str:
        """获取用户的优先显示名称（自定义昵称 > 默认名称）。"""
        nickname_api = self.apis.get("nickname_api")
        if nickname_api:
            custom_nickname = await nickname_api.get_nickname(user_id)
            if custom_nickname:
                return custom_nickname
        return default_name

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        whitelist = self.config.get("session_whitelist", [])
        if whitelist and event.unified_msg_origin not in whitelist:
            return

        user_id = event.get_sender_id()

        economy_api = self.apis.get("economy_api")
        if economy_api:
            user_profile = await economy_api.get_user_profile(user_id)
            if user_profile is None or user_profile.get("total_days", 0) == 0:
                return

        cooldown = self.config.get("check_cooldown", 60)
        current_time = time.time()
        if current_time - user_last_check_time.get(user_id, 0) < cooldown:
            return
        user_last_check_time[user_id] = current_time

        user_unlocked_ids = self.data_manager.get_unlocked_achievements(user_id)
        all_achievements = self.achievement_manager.get_all_achievements()
        newly_unlocked_data = []

        for ach in all_achievements:
            if ach["id"] in user_unlocked_ids:
                continue

            check_func = ach.get("check_func")
            if not callable(check_func):
                continue

            try:
                if await check_func(self.apis, user_id):
                    was_unlocked = await self.api.unlock_achievement(
                        user_id=user_id, achievement_id=ach["id"]
                    )
                    if was_unlocked:
                        newly_unlocked_data.append(ach)
            except Exception as e:
                logger.error(f"被动检查成就 {ach['id']} 时失败: {e}")

        pending_ids = self.data_manager.get_and_clear_pending_notifications(user_id)

        all_to_notify = list(newly_unlocked_data)
        if pending_ids:
            for ach_id in pending_ids:
                ach_data = self.achievement_manager.get_achievement_by_id(ach_id)
                if ach_data:
                    all_to_notify.append(ach_data)

        if all_to_notify:
            user_name = await self._get_display_name(user_id, event.get_sender_name())
            logger.info(
                f"用户 {user_id} 本次共解锁和收到 {len(all_to_notify)} 个成就，将合并推送。"
            )
            await self.send_unlock_notification(
                user_id, user_name, all_to_notify, event
            )
            event.stop_event()

    async def _find_achievements_by_keyword(
        self, keyword: str, user_id: str
    ) -> list[dict[str, Any]]:
        """
        根据关键词模糊搜索成就。
        - 支持按标题/描述搜索。
        - 支持按中文稀有度名称（如“神话”）搜索。
        """
        if not keyword:
            return []

        keyword_lower = keyword.lower()
        matches = []

        # 检查关键词是否是预设的稀有度中文名
        target_rarity = self.rarity_zh_to_en.get(keyword)

        user_unlocked_ids = self.data_manager.get_unlocked_achievements(user_id)
        all_achievements = self.achievement_manager.get_all_achievements()

        for ach in all_achievements:
            # 条件1: 关键词是稀有度，并且与成就的稀有度匹配
            rarity_match = (
                target_rarity is not None and ach.get("rarity") == target_rarity
            )

            # 条件2: 关键词在标题或描述中（不区分大小写）
            title_match = keyword_lower in ach.get("title", "").lower()
            desc_match = keyword_lower in ach.get("description", "").lower()

            # 如果满足上述任一搜索条件，则进入可见性检查
            if rarity_match or title_match or desc_match:
                is_hidden = ach.get("hidden", False)

                # 可见性检查：如果成就是非隐藏的，或者虽然是隐藏但已被该用户解锁，则视为可见
                if not is_hidden or (is_hidden and ach["id"] in user_unlocked_ids):
                    matches.append(ach)

        return matches

    @filter.command("成就帮助", alias={"achievement_help"})
    async def achievement_help(self, event: AstrMessageEvent):
        help_text = (
            "--- 成就系统帮助 ---\n"
            "/成就 - 显示你或他人的成就看板\n"
            "/查看成就 <关键词> - 查找并显示特定成就的卡片\n"
            "\n"
            "/成就帮助 - 显示本帮助信息"
        )
        yield event.plain_result(help_text)

    @filter.command("查看成就", alias={"检视成就", "检视"})
    async def view_achievement(self, event: AstrMessageEvent, keyword: str = ""):
        """根据关键词查找并显示具体的成就卡片（仅限已解锁）"""
        if not keyword:
            yield event.plain_result(
                "请输入要在你已解锁的成就中查找的关键词。\n用法: /查看成就 <关键词>"
            )
            return

        user_id = event.get_sender_id()

        # 1. 首先，获取该用户所有已解锁的成就ID列表
        user_unlocked_ids = self.data_manager.get_unlocked_achievements(user_id)

        # 如果用户一个成就都还没解锁，直接告知并返回
        if not user_unlocked_ids:
            yield event.plain_result("你还没有解锁任何成就，无法进行查看。")
            return

        # 2. 然后，像之前一样，根据关键词从所有可见成就中进行模糊搜索
        # _find_achievements_by_keyword 的逻辑保持不变，它能搜出所有对用户可见的成就
        all_possible_matches = await self._find_achievements_by_keyword(
            keyword, user_id
        )

        # 3. 在这里进行筛选，只保留那些既匹配关键词、又在用户已解锁列表中的成就
        matched_achievements = [
            ach for ach in all_possible_matches if ach["id"] in user_unlocked_ids
        ]

        # 4. 判断筛选后的结果
        if not matched_achievements:
            # 修改提示语，让用户明白是在他自己的成就库里没找到
            yield event.plain_result(
                f"在你已解锁的成就中，没有找到与“{keyword}”相关的条目。"
            )
            return

        # 后续的显示逻辑完全不变，使用的都是筛选后的 matched_achievements 列表
        if len(matched_achievements) > 5:
            await event.send(
                event.plain_result(
                    "找到了超过5个相关成就，将仅显示前5个。请尝试使用更精确的关键词。"
                )
            )
            matched_achievements = matched_achievements[:5]

        output_dir = "data/temp/achievements"
        os.makedirs(output_dir, exist_ok=True)

        for ach_data in matched_achievements:
            try:
                image_filename = f"ach_{ach_data['id'].replace(':', '_')}.png"
                output_path = os.path.join(output_dir, image_filename)

                if not os.path.exists(output_path):
                    logger.info(f"为 '查看成就' 命令生成缓存图片: {output_path}")
                    await self.image_generator.create_achievement_image(
                        title=ach_data["title"],
                        description=ach_data["description"],
                        icon_path=ach_data["icon_path"],
                        rarity=ach_data["rarity"],
                        output_path=output_path,
                    )

                await event.send(event.image_result(output_path))
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(
                    f"为成就 {ach_data['id']} 生成或发送图片时出错: {e}", exc_info=True
                )
                await event.send(
                    event.plain_result(
                        f"处理成就【{ach_data.get('title', '未知')}】时发生错误。"
                    )
                )

        event.stop_event()

    @filter.command("成就", alias={"成就看板"})
    async def show_board(self, event: AstrMessageEvent):
        target_user_id = event.get_sender_id()
        default_user_name = event.get_sender_name()

        for component in event.message_obj.message:
            if isinstance(component, At):
                target_user_id = str(component.qq)
                mentioned_name = getattr(component, "display_name", None)
                if (
                    not mentioned_name
                    and event.get_group_id()
                    and event.get_platform_name() == "aiocqhttp"
                ):
                    logger.info(f"正在尝试通过 API 获取用户 {target_user_id} 的昵称...")
                    try:
                        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                            AiocqhttpMessageEvent,
                        )

                        if isinstance(event, AiocqhttpMessageEvent):
                            client = event.bot
                            payloads = {
                                "group_id": int(event.get_group_id()),
                                "user_id": int(target_user_id),
                            }
                            user_info = await client.api.call_action(
                                "get_group_member_info", **payloads
                            )
                            if user_info:
                                mentioned_name = user_info.get("card") or user_info.get(
                                    "nickname"
                                )
                                logger.info(f"成功获取到昵称: {mentioned_name}")
                    except Exception as e:
                        logger.warning(
                            f"通过API获取用户 {target_user_id} 的昵称失败: {e}"
                        )
                        mentioned_name = None
                default_user_name = mentioned_name or f"用户 {target_user_id}"
                break

        target_user_name = await self._get_display_name(
            target_user_id, default_user_name
        )

        unlocked_ids = self.data_manager.get_unlocked_achievements(target_user_id)
        all_achievements_data = self.achievement_manager.get_all_achievements()

        visible_achievements = []
        for ach in all_achievements_data:
            is_unlocked = ach["id"] in unlocked_ids
            is_hidden = ach.get("hidden", False)
            if is_unlocked or not is_hidden:
                visible_achievements.append(ach)

        unlocked_visible_count = sum(
            1 for ach in visible_achievements if ach["id"] in unlocked_ids
        )
        total_visible_count = len(visible_achievements)

        sorted_unlocked_ids = sorted(list(unlocked_ids))
        state_string = ",".join(sorted_unlocked_ids)
        state_hash = hashlib.sha1(state_string.encode("utf-8")).hexdigest()[:10]

        output_dir = "data/temp/achievements"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(
            output_dir, f"board_{target_user_id}_{state_hash}.png"
        )

        if os.path.exists(output_path):
            logger.info(f"命中成就看板缓存，直接发送图片: {output_path}")
            yield event.image_result(output_path)
            return

        logger.info(
            f"未命中缓存，将为用户 {target_user_id} 生成新的成就看板。状态哈希: {state_hash}"
        )

        try:
            old_cache_pattern = os.path.join(
                output_dir, f"board_{target_user_id}_*.png"
            )
            for old_file in glob.glob(old_cache_pattern):
                os.remove(old_file)
                logger.info(f"删除了过期的看板缓存: {old_file}")
        except Exception as e:
            logger.warning(f"清理旧的成就看板缓存时出错: {e}")

        try:
            await self.image_generator.create_achievement_board(  # <-- 修改点
                user_name=target_user_name,
                all_achievements_data=visible_achievements,
                unlocked_ids=list(unlocked_ids),
                unlocked_count=unlocked_visible_count,
                total_count=total_visible_count,
                output_path=output_path,
            )
            yield event.image_result(output_path)
        except Exception as e:
            logger.error(f"生成成就看板失败: {e}", exc_info=True)
            yield event.plain_result("生成成就看板时遇到问题，请联系管理员。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置成就", alias={"reset_achievements"})
    async def reset_achievements(self, event: AstrMessageEvent):
        target_user_id = None
        for component in event.message_obj.message:
            if isinstance(component, At):
                target_user_id = str(component.qq)
                break

        if not target_user_id:
            yield event.plain_result("请@一个要重置成就的用户。")
            return

        success = self.data_manager.reset_user_achievements(target_user_id)
        if success:
            yield event.plain_result(
                f"已成功重置用户 {target_user_id} 的所有成就数据。"
            )
        else:
            yield event.plain_result(
                f"用户 {target_user_id} 没有任何成就数据，无需重置。"
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置所有成就", alias={"重置全部成就"})
    async def reset_all_achievements(self, event: AstrMessageEvent, confirm: str = ""):
        num_affected = self.data_manager.reset_all_data()
        yield event.plain_result(
            f"✅ 操作成功！已清空所有成就数据，共影响 {num_affected} 名用户的记录。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("颁发成就", alias={"grant_achievement", "授权成就"})
    async def grant_achievement(
        self, event: AstrMessageEvent, achievement_id: str, user_id_arg: str = None
    ):
        target_user_id = None
        target_at_component = None

        for component in event.message_obj.message:
            if isinstance(component, At):
                target_user_id = str(component.qq)
                target_at_component = component
                break

        if not target_user_id and user_id_arg is not None:
            if str(user_id_arg).isdigit():
                target_user_id = str(user_id_arg)
            else:
                yield event.plain_result("错误：提供的用户ID格式不正确，应为一串数字。")
                return

        if not target_user_id:
            yield event.plain_result(
                "请@一个用户或直接提供其ID。\n"
                "用法1: /颁发成就 <成就ID> @用户\n"
                "用法2: /颁发成就 <成就ID> <用户ID>"
            )
            return

        ach_data = self.achievement_manager.get_achievement_by_id(achievement_id)
        if not ach_data:
            yield event.plain_result(f"错误：未找到ID为 '{achievement_id}' 的成就。")
            return

        success = await self.api.unlock_achievement(
            user_id=target_user_id, achievement_id=achievement_id
        )

        if success:
            self.data_manager.add_pending_notification(target_user_id, achievement_id)
            if target_at_component:
                yield event.chain_result(
                    [
                        Plain(text="✅ 已为 "),
                        target_at_component,
                        Plain(
                            text=f" 静默授予成就【{ach_data['title']}】。该通知将在其下次获得成就时一并推送。"
                        ),
                    ]
                )
            else:
                yield event.plain_result(
                    f"✅ 已为用户 {target_user_id} 静默授予成就【{ach_data['title']}】。该通知将在其下次获得成就时一并推送。"
                )
        else:
            yield event.plain_result(
                f"操作失败：该用户已经拥有成就【{ach_data['title']}】。"
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("批量颁发成就", alias={"batchgrant"})
    async def batch_grant_achievement(
        self, event: AstrMessageEvent, achievement_id: str, confirm: str = ""
    ):
        if confirm.lower() != "confirm":
            yield event.plain_result(
                f"⚠️ **警告：即将批量颁发成就！**\n"
                f"目标成就: `{achievement_id}`\n"
                f"目标用户来源: FavourPro 数据库中的所有用户\n"
                f"确认执行请输入: `/批量颁发成就 {achievement_id} confirm`"
            )
            return

        yield event.plain_result(
            f"✅ 命令已接收。开始在后台为老玩家批量颁发成就【{achievement_id}】...完成后将在此处发送通知。"
        )

        asyncio.create_task(self._perform_batch_grant(achievement_id, event))

    async def _perform_batch_grant(self, achievement_id: str, event: AstrMessageEvent):
        db_path = "data/plugin_data/favorpro/favour_pro.db"  # 确保路径正确
        logger.info(
            f"开始执行批量颁发任务，成就ID: {achievement_id}，数据库路径: {db_path}"
        )

        success_count = 0
        skipped_count = 0
        error_count = 0
        total_users = 0

        try:
            if not os.path.exists(db_path):
                logger.error(f"批量颁发失败：数据库文件不存在于 {db_path}")
                await event.send(
                    event.plain_result(
                        f"❌ 任务失败：数据库文件不存在！\n路径: `{db_path}`"
                    )
                )
                return

            async with aiosqlite.connect(db_path) as db:
                async with db.execute("SELECT user_id FROM user_states") as cursor:
                    user_rows = await cursor.fetchall()

            user_ids_to_grant = [str(row[0]) for row in user_rows]
            total_users = len(user_ids_to_grant)
            logger.info(f"从数据库中查询到 {total_users} 名用户。")

            for i, user_id in enumerate(user_ids_to_grant):
                try:
                    success = await self.api.unlock_achievement(
                        user_id=user_id, achievement_id=achievement_id
                    )
                    if success:
                        self.data_manager.add_pending_notification(
                            user_id, achievement_id
                        )
                        success_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    logger.warning(
                        f"为用户 {user_id} 颁发成就 {achievement_id} 时出错: {e}"
                    )
                    error_count += 1

                if (i + 1) % 100 == 0:
                    await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"批量颁发成就任务发生严重错误: {e}", exc_info=True)
            await event.send(
                event.plain_result(
                    f"❌ 任务因严重错误而中断！请检查后台日志。\n错误: {e}"
                )
            )
            return

        report_message = (
            f"🎉 **批量颁发成就任务已完成！**\n"
            f"--------------------\n"
            f"目标成就: 【{achievement_id}】\n"
            f"总计查询到: {total_users} 名老玩家\n"
            f"✅ 成功颁发: {success_count} 人\n"
            f"⏭️ 跳过(已拥有): {skipped_count} 人\n"
            f"❌ 出错: {error_count} 人"
        )
        await event.send(event.plain_result(report_message))
        logger.info("批量颁发任务完成。")
