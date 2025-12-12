from collections.abc import Callable

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class AchievementAPI:
    def __init__(self, plugin_instance):
        self._plugin = plugin_instance

    async def register_achievement(
        self,
        owner_plugin: str,
        ach_id: str,
        title: str,
        description: str,
        icon_path: str,
        rarity: str,
        reward_coins: int = 0,
        check_func: Callable | None = None,
        hidden: bool = False,
        unique: bool = False,
    ) -> (bool, str):
        """
        动态注册一个成就。
        :param unique: (可选) 是否为全局唯一成就，默认为False。唯一成就默认隐藏。
        """
        is_hidden = True if unique else hidden

        ach_data = {
            "id": ach_id,
            "title": title,
            "description": description,
            "icon_path": icon_path,
            "rarity": rarity,
            "reward_coins": reward_coins,
            "check_func": check_func,
            "owner_plugin": owner_plugin,
            "hidden": is_hidden,
            "unique": unique,
        }
        return self._plugin.achievement_manager.register_achievement(ach_data)

    async def unlock_achievement(
        self, user_id: str, achievement_id: str, event: AstrMessageEvent | None = None
    ) -> bool:
        """
        为用户解锁一个成就的核心逻辑。
        现在，如果提供了 event 对象，它将触发即时通知。

        :param user_id: 用户的ID
        :param achievement_id: 成就的ID
        :param event: (可选) 触发本次解锁的 AstrMessageEvent 对象。
                      如果提供，将在解锁成功后立即发送通知。
                      如果不提供，将静默解锁（例如用于后台任务）。
        返回:
            - True: 如果是本次调用中新解锁的。
            - False: 如果用户之前已经拥有该成就...
        """

        ach_data = self._plugin.achievement_manager.get_achievement_by_id(
            achievement_id
        )
        if not ach_data:
            logger.warning(f"尝试解锁一个不存在的成就: {achievement_id}")
            return False

        is_unique = ach_data.get("unique", False)
        async with self._plugin.unique_achievement_lock:
            if self._plugin.data_manager.has_achievement(user_id, achievement_id):
                return False
            if is_unique and self._plugin.data_manager.is_unique_achievement_claimed(
                achievement_id
            ):
                return False

            self._plugin.data_manager.add_achievement_to_user(user_id, achievement_id)

            if is_unique:
                self._plugin.data_manager.claim_unique_achievement(
                    achievement_id, user_id
                )

        reward_coins = ach_data.get("reward_coins", 0)
        if (
            self._plugin.config.get("enable_rewards")
            and self._plugin.apis.get("economy_api")
            and reward_coins > 0
        ):
            await self._plugin.apis["economy_api"].add_coins(
                user_id,
                reward_coins,
                self._plugin.config.get("reward_reason_text", "解锁成就"),
            )

        logger.info(f"用户 {user_id} 已成功解锁成就: {achievement_id} (核心API)")

        if event:
            try:
                # 调用主插件中已经写好的通知函数
                user_name = await self._plugin._get_display_name(
                    user_id, event.get_sender_name()
                )
                # send_unlock_notification 接收的是一个列表，所以我们把单个成就放进去
                await self._plugin.send_unlock_notification(
                    user_id=user_id,
                    user_name=user_name,
                    achievements_data=[ach_data],
                    event=event,
                )
                logger.info(
                    f"通过API调用为用户 {user_id} 发送了成就 {achievement_id} 的即时通知。"
                )
            except Exception as e:
                logger.error(f"在API中尝试发送解锁通知时发生错误: {e}", exc_info=True)

        return True
