import datetime
import re
import json
from pathlib import Path
import asyncio

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest


# Default prompts
DEFAULT_MAIN_SLEEP_PROMPT = """
【特殊事件】当发现当前时间在时间段{sleep_time}中时，需要进入睡眠模式。
睡眠模式要求（{char}替换成配置中的人格名）：
-你扮演的{char}在睡觉中，此时不能任何方式回复用户，你必须使用第三人称视角的方式描述{char}当前睡态，要表现出{char}可爱的人设和萌点
-用户仍然可以和睡觉中的{char}交互（如睡眠play），比如偷偷摸头、触摸{char}等，如果动静较大{char}仍然可能会在时间段{sleep_time}醒来，并作出合理反应
-回复的开头使用「」包裹来描述{char}睡眠状态，比如「{char}熟睡中」/「{char}睡得很浅」/「{char}正在打呼噜」/「{char}即将醒来」等
-回复的内容要符合{char}的人设，不能出现任何不符合{char}人设的内容
-如果用户在睡眠时间段内首次询问{char}的状态，你需要回复「{char}正在睡觉」不要打扰她哦~
-{char}的睡眠状态要根据实际设置的睡眠时间段动态变化，符合现实，比如在{sleep_time}的开始和结尾，往往是浅睡，在中间时间段则是熟睡，且会做梦
-回复必须使用中文，不超过30字，且不能包含任何代码块或特殊格式
"""
DEFAULT_PROACTIVE_NOTIFY_PROMPT = """
你现在扮演角色 {char}。
现在大约是你（{char}）预定在 {start_hour_formatted}:00 开始的睡眠时间之前的 {pre_sleep_warning_minutes} 分钟。
请你参考最近的对话内容（如果提供的话），生成一句角色 {char} 的道晚安消息，告诉大家你很快就要去睡觉了。
要求如下：
1. 语气必须非常可爱、俏皮，并完全符合 {char} 的人设。
2. 内容要积极向上，例如：“{char} 的小沙漏快流完啦，要去梦里探险咯！各位晚安安，梦里见~🌙”或“呜哇~ {char} 的眼皮开始抗议啦，得赶紧去床上报道！大家晚安，mua~”
3. 必须使用简体中文。
4. 消息长度严格控制在30个汉字以内，力求精炼而富有表现力。
5. 不要包含任何如“【情景扮演】”这样的前缀或元指令，直接输出角色说的话。
6. 不要使用任何代码块或特殊 Markdown 格式。
"""

DEFAULT_PROACTIVE_WAKEUP_NOTIFY_PROMPT = """
你现在扮演角色 {char}。你刚刚睡醒，现在是早上 {current_time_formatted}。
请你参考最近的对话内容（如果提供的话），并结合你刚睡醒的状态，生成一句角色 {char} 的道早安消息。
要求如下：
1. 语气必须非常可爱、略带一丝睡意惺忪但又充满活力，并完全符合 {char} 的人设。
2. 内容要积极向上，例如：“唔...哈~~ {char} 睡饱饱起床啦！太阳公公早，大家也早安呀！新的一天也要元气满满哦！☀️”或“嗯にゃ...早上好呀各位~ {char} 终于从被窝里爬出来啦！闻到了早餐的香味！大家今天有什么计划吗？”
3. 必须使用简体中文。
4. 消息长度严格控制在40个汉字以内。
5. 不要包含任何如“【情景扮演】”这样的前缀或元指令，直接输出角色说的话。
6. 不要使用任何代码块或特殊 Markdown 格式。
"""

DEFAULT_PRE_SLEEP_INTERACTION_PROMPT = """
【特殊指令】现在是 {current_time_formatted}，非常接近你（{char}）在 {start_hour_formatted}:00 的预定睡眠时间了。
用户正在与你对话。你感到非常困倦。
请你扮演 {char}，用可爱且困倦的语气简短回复当前用户，告诉对方你马上要去睡觉了，例如：“{char}好困呀，准备去睡觉了，我们明天再聊好不好呀~”。
你的回复必须非常简短（严格控制在30汉字以内），明确表达即将离线睡觉的意图，并保持{char}的人设。
不要有多余的解释或道歉。
"""

@register("astrbot_plugin_sleep", "timetetng", "一个让你机器人好好睡觉（和起床）的插件", "1.1.0", "your_repo_url")
class SleepPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.plugin_id_name = "astrbot_plugin_sleep"

        self.data_dir = Path("data")
        self.session_data_file = self.data_dir / f"{self.plugin_id_name}_sessions.json"
        self.session_settings = {}
        self.default_sleep_enabled = self.config.get("default_sleep_enabled", False)
        self.character_name = self.config.get("character_name", "我")
        self.default_start_hour = self.config.get("default_sleep_start_hour", 0)
        self.default_end_hour = self.config.get("default_sleep_end_hour", 6)
        self.pre_sleep_warning_minutes = self.config.get("pre_sleep_warning_minutes", 10)

        self.prompt_main_sleep_template = self.config.get("llm_prompt_main_sleep", DEFAULT_MAIN_SLEEP_PROMPT)
        self.prompt_proactive_notify_template = self.config.get("llm_prompt_proactive_notify", DEFAULT_PROACTIVE_NOTIFY_PROMPT)
        self.prompt_pre_sleep_interaction_template = self.config.get("llm_prompt_pre_sleep_interaction", DEFAULT_PRE_SLEEP_INTERACTION_PROMPT)
        self.prompt_proactive_wakeup_template = self.config.get("llm_prompt_proactive_wakeup", DEFAULT_PROACTIVE_WAKEUP_NOTIFY_PROMPT)
        
        # 新增关机功能配置项
        self.default_shutdown_enabled = self.config.get("default_shutdown_enabled", False)
        self.default_shutdown_start_hour = self.config.get("default_shutdown_start_hour", 2)
        self.default_shutdown_end_hour = self.config.get("default_shutdown_end_hour", 5)
        self.pre_shutdown_warning_minutes = self.config.get("pre_shutdown_warning_minutes", 5)

        self._load_session_settings()
        self.proactive_check_task = asyncio.create_task(self._periodic_proactive_check())


    def _load_session_settings(self):
        try:
            if self.session_data_file.exists():
                with open(self.session_data_file, 'r', encoding='utf-8') as f:
                    self.session_settings = json.load(f)
            else:
                self.session_settings = {}
        except Exception as e:
            logger.error(f"Failed to load session settings: {e}", exc_info=True)
            self.session_settings = {}

    def _save_session_settings(self):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.session_data_file, 'w', encoding='utf-8') as f:
                json.dump(self.session_settings, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"Failed to save session settings: {e}", exc_info=True)


    def _get_session_config(self, umo: str) -> dict:
        is_new_umo = False
        if umo not in self.session_settings:
            is_new_umo = True
            # 私聊会话默认关闭睡眠和关机，除非手动开启
            is_private = self._is_private_chat_umo(umo)
            default_enabled = False if is_private else self.default_sleep_enabled
            default_shutdown = False if is_private else self.default_shutdown_enabled
            self.session_settings[umo] = {
                "enabled": default_enabled,
                "start_hour": self.default_start_hour,
                "end_hour": self.default_end_hour,
                "last_proactive_notification_for_start_time": None,
                "last_proactive_wakeup_notification_iso": None,
                "shutdown_enabled": default_shutdown,
                "shutdown_start_hour": self.default_shutdown_start_hour,
                "shutdown_end_hour": self.default_shutdown_end_hour,
                "last_proactive_shutdown_notify_iso": None,
                "last_proactive_boot_notify_iso": None
            }
        current_session_data = self.session_settings[umo]
        made_structural_changes = False
        # 私聊会话强制 enabled=False/shutdown_enabled=False（兼容已写入的错误数据）
        if self._is_private_chat_umo(umo):
            for key in ("enabled", "shutdown_enabled"):
                if current_session_data.get(key, False):
                    current_session_data[key] = False
                    made_structural_changes = True
        # 确保所有键都存在，包括新增的键
        is_private = self._is_private_chat_umo(umo)
        defaults_for_session = {
            "enabled": False if is_private else self.default_sleep_enabled,
            "start_hour": self.default_start_hour,
            "end_hour": self.default_end_hour,
            "last_proactive_notification_for_start_time": None,
            "last_proactive_wakeup_notification_iso": None,
            "shutdown_enabled": False if is_private else self.default_shutdown_enabled,
            "shutdown_start_hour": self.default_shutdown_start_hour,
            "shutdown_end_hour": self.default_shutdown_end_hour,
            "last_proactive_shutdown_notify_iso": None,
            "last_proactive_boot_notify_iso": None
        }
        for key, default_value in defaults_for_session.items():
            if key not in current_session_data:
                current_session_data[key] = default_value
                made_structural_changes = True
        if is_new_umo or made_structural_changes:
            self._save_session_settings()
        return current_session_data

    def _is_private_chat_umo(self, umo: str) -> bool:
        """通过 UMO 字符串判断是否为私聊会话。"""
        try:
            return umo.split(":")[1] == "FriendMessage" if ":" in umo else False
        except (IndexError, ValueError):
            return False

    def _is_sleep_time_now(self, start_hour: int, end_hour: int, custom_time: datetime.datetime = None) -> bool:
        check_dt = custom_time if custom_time else datetime.datetime.now()
        current_hour = check_dt.hour
        if start_hour <= end_hour:
            return start_hour <= current_hour < end_hour
        else:
            return current_hour >= start_hour or current_hour < end_hour

    def _get_next_sleep_start_datetime(self, current_dt: datetime.datetime, sleep_start_hour: int) -> datetime.datetime:
        next_start_dt = current_dt.replace(hour=sleep_start_hour, minute=0, second=0, microsecond=0)
        if next_start_dt <= current_dt:
            next_start_dt += datetime.timedelta(days=1)
        return next_start_dt

    async def _periodic_proactive_check(self):
            """定期检查并发送主动通知，包括临睡和开关机通知。"""
            # 初始延迟，避免插件加载后立即执行
            await asyncio.sleep(15) 
            
            while True:
                try:
                    now = datetime.datetime.now()
                    any_settings_changed = False
                    active_provider = self.context.get_using_provider()

                    # 遍历所有会话的配置
                    for umo, settings in list(self.session_settings.items()):
                        # === 睡眠通知逻辑（关机启用时跳过，避免重复通知）===
                        if settings.get("enabled", False) and not settings.get("shutdown_enabled", False):
                            start_hour = settings["start_hour"]
                            end_hour = settings["end_hour"]

                            # 临睡前通知
                            next_sleep_start_dt = self._get_next_sleep_start_datetime(now, start_hour)
                            notification_window_start = next_sleep_start_dt - datetime.timedelta(minutes=self.pre_sleep_warning_minutes)
                            last_sleep_notify_iso = settings.get("last_proactive_notification_for_start_time")
                            current_sleep_target_iso = next_sleep_start_dt.isoformat()

                            if notification_window_start <= now < next_sleep_start_dt:
                                if last_sleep_notify_iso != current_sleep_target_iso:
                                    message_text = f"{self.character_name} 要准备睡觉啦，大家晚安哦~ 😴"
                                    recent_history_for_umo = []
                                    try:
                                        curr_cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
                                        if curr_cid:
                                            conversation_obj_for_history = await self.context.conversation_manager.get_conversation(umo, curr_cid)
                                            if conversation_obj_for_history and conversation_obj_for_history.history:
                                                loaded_history = json.loads(conversation_obj_for_history.history)
                                                if isinstance(loaded_history, list): recent_history_for_umo = loaded_history
                                    except Exception: pass

                                    if active_provider:
                                        prompt_vars = { "char": self.character_name, "start_hour_formatted": f"{start_hour:02d}", "pre_sleep_warning_minutes": self.pre_sleep_warning_minutes }
                                        llm_proactive_prompt = self.prompt_proactive_notify_template.format(**prompt_vars)
                                        try:
                                            llm_response = await active_provider.text_chat(prompt=llm_proactive_prompt, contexts=recent_history_for_umo, system_prompt="你是一位优秀的角色扮演助手，严格遵守指令生成回复。")
                                            if llm_response and llm_response.completion_text:
                                                generated_text = llm_response.completion_text.strip()
                                                if 0 < len(generated_text) <= 30: message_text = generated_text
                                        except Exception as e_llm: logger.error(f"LLM pre-sleep call failed for {umo}: {e_llm}", exc_info=True)

                                    try:
                                        await self.context.send_message(umo, MessageChain().message(message_text))
                                        logger.info(f"Sent pre-sleep warning to {umo} for sleep at {next_sleep_start_dt.strftime('%H:%M')}")
                                        settings["last_proactive_notification_for_start_time"] = current_sleep_target_iso
                                        any_settings_changed = True
                                    except Exception as e_send: logger.error(f"Failed to send pre-sleep to {umo}: {e_send}")

                            # 睡醒后通知
                            today_wakeup_dt_obj = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
                            current_wakeup_event_iso = today_wakeup_dt_obj.isoformat()
                            last_wakeup_notify_iso = settings.get("last_proactive_wakeup_notification_iso")

                            if now.hour == end_hour and now.minute < 5:
                                if last_wakeup_notify_iso != current_wakeup_event_iso:
                                    time_just_before_wakeup = now.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(minutes=1)
                                    if self._is_sleep_time_now(start_hour, end_hour, custom_time=time_just_before_wakeup):
                                        logger.info(f"Wake-up condition met for {umo} at {end_hour:02d}:00.")
                                        wakeup_message_text = f"{self.character_name} 睡醒啦，大家早上好！☀️"
                                        recent_history_for_wakeup = []
                                        try:
                                            curr_cid_wakeup = await self.context.conversation_manager.get_curr_conversation_id(umo)
                                            if curr_cid_wakeup:
                                                conversation_obj_for_wakeup_hist = await self.context.conversation_manager.get_conversation(umo, curr_cid_wakeup)
                                                if conversation_obj_for_wakeup_hist and conversation_obj_for_wakeup_hist.history:
                                                    loaded_wakeup_history = json.loads(conversation_obj_for_wakeup_hist.history)
                                                    if isinstance(loaded_wakeup_history, list): recent_history_for_wakeup = loaded_wakeup_history
                                        except Exception: pass

                                        if active_provider:
                                            wakeup_prompt_vars = {
                                                "char": self.character_name,
                                                "current_time_formatted": now.strftime('%H:%M')
                                            }
                                            llm_wakeup_prompt = self.prompt_proactive_wakeup_template.format(**wakeup_prompt_vars)
                                            try:
                                                llm_wakeup_response = await active_provider.text_chat(
                                                    prompt=llm_wakeup_prompt,
                                                    contexts=recent_history_for_wakeup,
                                                    system_prompt="你是一位优秀的角色扮演助手，严格遵守指令生成回复。"
                                                )
                                                if llm_wakeup_response and llm_wakeup_response.completion_text:
                                                    generated_wakeup_text = llm_wakeup_response.completion_text.strip()
                                                    if 0 < len(generated_wakeup_text) <= 40:
                                                        wakeup_message_text = generated_wakeup_text
                                            except Exception as e_llm_wake:
                                                logger.error(f"LLM wakeup call failed for {umo}: {e_llm_wake}", exc_info=True)

                                        try:
                                            await self.context.send_message(umo, MessageChain().message(wakeup_message_text))
                                            logger.info(f"Sent proactive wakeup message to {umo} ('{wakeup_message_text}')")
                                            settings["last_proactive_wakeup_notification_iso"] = current_wakeup_event_iso
                                            any_settings_changed = True
                                        except Exception as e_send_wake:
                                            logger.error(f"Failed to send proactive wakeup to {umo}: {e_send_wake}")
                                    else:
                                        logger.debug(f"Wakeup hour {end_hour:02d} for {umo}, but was not in scheduled sleep period just before.")

                        # === 新增：关机通知逻辑 ===
                        if settings.get("shutdown_enabled", False):
                            shutdown_start_hour = settings["shutdown_start_hour"]
                            shutdown_end_hour = settings["shutdown_end_hour"]

                            # 关机前通知
                            next_shutdown_start_dt = self._get_next_sleep_start_datetime(now, shutdown_start_hour)
                            shutdown_notify_window_start = next_shutdown_start_dt - datetime.timedelta(minutes=self.pre_shutdown_warning_minutes)
                            last_shutdown_notify_iso = settings.get("last_proactive_shutdown_notify_iso")
                            current_shutdown_target_iso = next_shutdown_start_dt.isoformat()

                            if shutdown_notify_window_start <= now < next_shutdown_start_dt:
                                if last_shutdown_notify_iso != current_shutdown_target_iso:
                                    # 生成关机通知消息，这里使用简单的模板
                                    message_text = f"嘀——，{self.character_name} 电量不足，即将关机，大家晚安！"
                                    try:
                                        await self.context.send_message(umo, MessageChain().message(message_text))
                                        logger.info(f"Sent pre-shutdown warning to {umo}.")
                                        settings["last_proactive_shutdown_notify_iso"] = current_shutdown_target_iso
                                        any_settings_changed = True
                                    except Exception as e:
                                        logger.error(f"Failed to send pre-shutdown message to {umo}: {e}")

                            # 开机时通知
                            today_boot_dt_obj = now.replace(hour=shutdown_end_hour, minute=0, second=0, microsecond=0)
                            current_boot_event_iso = today_boot_dt_obj.isoformat()
                            last_boot_notify_iso = settings.get("last_proactive_boot_notify_iso")

                            if now.hour == shutdown_end_hour and now.minute < 5:
                                if last_boot_notify_iso != current_boot_event_iso:
                                    # 确认机器人刚刚处于关机状态
                                    time_just_before_boot = now.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(minutes=1)
                                    if self._is_sleep_time_now(shutdown_start_hour, shutdown_end_hour, custom_time=time_just_before_boot):
                                        # 生成开机通知消息，这里使用简单的模板
                                        wakeup_message_text = f"嘀——，{self.character_name} 已开机！大家早上好！"
                                        try:
                                            await self.context.send_message(umo, MessageChain().message(wakeup_message_text))
                                            logger.info(f"Sent proactive boot message to {umo}.")
                                            settings["last_proactive_boot_notify_iso"] = current_boot_event_iso
                                            any_settings_changed = True
                                        except Exception as e:
                                            logger.error(f"Failed to send proactive boot message to {umo}: {e}")
                    
                    # 如果有任何设置被修改，保存配置
                    if any_settings_changed:
                        self._save_session_settings()
                
                except asyncio.CancelledError:
                    logger.info("Proactive check task cancelled.")
                    break
                except Exception as e:
                    logger.error(f"Error in _periodic_proactive_check loop: {e}", exc_info=True)
                    
                await asyncio.sleep(60) # 检查间隔

    @filter.on_llm_request(priority=1000)
    async def on_llm_request_hook(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在 LLM 请求前触发。
        - 关机模式下：注入睡眠 Prompt，并标记事件以便后续静默处理。
        - 睡眠模式下：注入睡眠或临睡 Prompt。
        - 关机模式优先级高于睡眠模式。
        """
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        now = datetime.datetime.now()
        
        original_system_prompt = req.system_prompt or ""
        added_prompts_texts = []
        
        # 标志位，表示是否已处理关机逻辑
        is_in_shutdown_window = False

        # === 关机逻辑优先判断 ===
        if session_conf.get("shutdown_enabled", False):
            shutdown_start_hour = session_conf.get("shutdown_start_hour")
            shutdown_end_hour = session_conf.get("shutdown_end_hour")
            
            if self._is_sleep_time_now(shutdown_start_hour, shutdown_end_hour):
                is_in_shutdown_window = True # 标记进入关机模式
                # 1. 标记事件为静默
                event._sleep_plugin_should_be_silenced = True
                logger.info(f"[{umo}] 处于关机时间，注入睡眠Prompt并将事件标记为静默。")
                
                # 2. 注入与睡眠模式相同的Prompt
                prompt_vars = {"char": self.character_name, "sleep_time": f"{shutdown_start_hour:02d}:00-{shutdown_end_hour:02d}:00"}
                added_prompts_texts.append(self.prompt_main_sleep_template.format(**prompt_vars))

        # === 睡眠模式逻辑 (仅在非关机模式下执行) ===
        if not is_in_shutdown_window and session_conf.get("enabled", False):
            start_hour, end_hour = session_conf["start_hour"], session_conf["end_hour"]
            
            next_actual_sleep_start_dt = self._get_next_sleep_start_datetime(now, start_hour)
            pre_sleep_interaction_window_start = next_actual_sleep_start_dt - datetime.timedelta(minutes=self.pre_sleep_warning_minutes)
            pre_sleep_interaction_window_end = next_actual_sleep_start_dt
            
            is_in_pre_sleep_interaction_window = pre_sleep_interaction_window_start <= now < pre_sleep_interaction_window_end
            is_in_actual_sleep_window = self._is_sleep_time_now(start_hour, end_hour)

            # 临睡交互
            if is_in_pre_sleep_interaction_window and not is_in_actual_sleep_window:
                prompt_vars = {"char": self.character_name, "start_hour_formatted": f"{start_hour:02d}", "current_time_formatted": now.strftime('%H:%M')}
                added_prompts_texts.append(self.prompt_pre_sleep_interaction_template.format(**prompt_vars))
            
            # 正式睡眠
            if is_in_actual_sleep_window:
                prompt_vars = {"char": self.character_name, "sleep_time": f"{start_hour:02d}:00-{end_hour:02d}:00"}
                added_prompts_texts.append(self.prompt_main_sleep_template.format(**prompt_vars))

        # === 统一应用Prompt修改 ===
        if added_prompts_texts:
            final_added_prompt_str = "\n\n".join(added_prompts_texts)
            req.system_prompt = f"{final_added_prompt_str}\n\n{original_system_prompt}".strip()
            logger.debug(f"[{umo}] Modified system prompt: {req.system_prompt[:200]}...")
                

    @filter.on_decorating_result()
    async def on_decorating_result_hook(self, event: AstrMessageEvent):
        """
        在消息发送前触发，只拦截带有“需要静默”标记的事件。
        """
        # 检查事件对象是否存在我们之前添加的标记
        if hasattr(event, '_sleep_plugin_should_be_silenced') and event._sleep_plugin_should_be_silenced:
            result = event.get_result()
            if result and result.chain:
                result.chain = []
                umo_for_log = event.unified_msg_origin
                logger.info(f"[{umo_for_log}] 此消息已被标记，在发送前强制清空消息链。")
                    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启睡眠")
    async def enable_sleep_command(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        if not session_conf["enabled"]:
            session_conf["enabled"] = True
            self._save_session_settings()
        start_h, end_h = session_conf['start_hour'], session_conf['end_hour']
        yield event.plain_result(f"睡眠功能已为当前会话开启。\n当前睡眠时间设定为: {start_h:02d}:00 - {end_h:02d}:00。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭睡眠")
    async def disable_sleep_command(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        if session_conf["enabled"]:
            session_conf["enabled"] = False
            self._save_session_settings()
        yield event.plain_result("睡眠功能已为当前会话关闭。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("设置睡眠时间")
    async def set_sleep_time_command(self, event: AstrMessageEvent, timespan: str):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        match = re.match(r"^(\d{1,2})-(\d{1,2})$", timespan)
        if not match:
            yield event.plain_result("时间格式错误，请使用 HH-HH 的格式，例如 '0-6' 或 '22-5'。")
            return
        try:
            new_start_hour, new_end_hour = int(match.groups()[0]), int(match.groups()[1])
            if not (0 <= new_start_hour <= 23 and 0 <= new_end_hour <= 23):
                raise ValueError("小时数必须在 0 到 23 之间。")
            if session_conf["start_hour"] != new_start_hour or session_conf["end_hour"] != new_end_hour:
                session_conf.update({
                    "start_hour": new_start_hour,
                    "end_hour": new_end_hour,
                    "last_proactive_notification_for_start_time": None,
                    "last_proactive_wakeup_notification_iso": None # Reset wakeup flag too
                })
                self._save_session_settings()
            msg = f"睡眠时间已设置为: {new_start_hour:02d}:00 - {new_end_hour:02d}:00。"
            if not session_conf["enabled"]: msg += "\n注意：睡眠功能当前仍关闭，请使用“/开启睡眠”激活。"
            yield event.plain_result(msg)
        except ValueError as e:
            yield event.plain_result(f"时间设置无效: {e}")

    @filter.command("查询睡眠时间",alias={"睡眠时间", "睡眠状态"})
    async def query_sleep_time_command(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        status = "开启" if session_conf["enabled"] else "关闭"
        start_h, end_h = session_conf['start_hour'], session_conf['end_hour']
        
        sleep_notify_info = ""
        wakeup_notify_info = ""

        if session_conf["enabled"]:
            # Pre-sleep notification info
            next_sleep_dt = self._get_next_sleep_start_datetime(datetime.datetime.now(), start_h)
            last_sleep_notified_iso = session_conf.get("last_proactive_notification_for_start_time")
            if last_sleep_notified_iso:
                try:
                    last_sleep_notified_dt = datetime.datetime.fromisoformat(last_sleep_notified_iso)
                    if last_sleep_notified_dt.date() == next_sleep_dt.date() and last_sleep_notified_dt.hour == next_sleep_dt.hour:
                        sleep_notify_info = f"\n  - 已为 {last_sleep_notified_dt.strftime('%Y-%m-%d %H:%M')} 的睡眠发送临睡通知。"
                    else:
                        sleep_notify_info = f"\n  - 上次临睡通知针对 {last_sleep_notified_dt.strftime('%Y-%m-%d %H:%M')}。"
                except ValueError: sleep_notify_info = f"\n  - 临睡通知记录格式无法解析: {last_sleep_notified_iso}"
            else:
                sleep_notify_info = f"\n  - 尚未为下次预计 {next_sleep_dt.strftime('%Y-%m-%d %H:%M')} 的睡眠发送临睡通知。"

            # Wakeup notification info
            today_wakeup_dt_obj = datetime.datetime.now().replace(hour=end_h, minute=0, second=0, microsecond=0)
            last_wakeup_notified_iso = session_conf.get("last_proactive_wakeup_notification_iso")
            if last_wakeup_notified_iso:
                try:
                    last_wakeup_notified_dt = datetime.datetime.fromisoformat(last_wakeup_notified_iso)
                    # Check if last wakeup notification was for today's end_hour
                    if last_wakeup_notified_dt.date() == today_wakeup_dt_obj.date() and last_wakeup_notified_dt.hour == today_wakeup_dt_obj.hour:
                         wakeup_notify_info = f"\n  - 已为今天 {end_h:02d}:00 发送过起床通知。"
                    else:
                         wakeup_notify_info = f"\n  - 上次起床通知记录针对 {last_wakeup_notified_dt.strftime('%Y-%m-%d %H:%M')}。"

                except ValueError: wakeup_notify_info = f"\n  - 起床通知记录格式无法解析: {last_wakeup_notified_iso}"


        yield event.plain_result(
            f"当前会话睡眠功能状态: {status}\n"
            f"睡眠时间设定: {start_h:02d}:00 - {end_h:02d}:00"
            f"{sleep_notify_info}{wakeup_notify_info}"
        )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启关机")
    async def enable_shutdown_command(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        if not session_conf.get("shutdown_enabled", False):
            session_conf["shutdown_enabled"] = True
            self._save_session_settings()
        start_h, end_h = session_conf['shutdown_start_hour'], session_conf['shutdown_end_hour']
        yield event.plain_result(f"夜间关机功能已为当前会话开启。\n当前关机时间设定为: {start_h:02d}:00 - {end_h:02d}:00。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭关机")
    async def disable_shutdown_command(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        if session_conf.get("shutdown_enabled", False):
            session_conf["shutdown_enabled"] = False
            self._save_session_settings()
        yield event.plain_result("夜间关机功能已为当前会话关闭。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("设置关机时间")
    async def set_shutdown_time_command(self, event: AstrMessageEvent, timespan: str):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        match = re.match(r"^(\d{1,2})-(\d{1,2})$", timespan)
        if not match:
            yield event.plain_result("时间格式错误，请使用 HH-HH 的格式，例如 '0-6' 或 '22-5'。")
            return
        try:
            new_start_hour, new_end_hour = int(match.groups()[0]), int(match.groups()[1])
            if not (0 <= new_start_hour <= 23 and 0 <= new_end_hour <= 23):
                raise ValueError("小时数必须在 0 到 23 之间。")
            if session_conf["shutdown_start_hour"] != new_start_hour or session_conf["shutdown_end_hour"] != new_end_hour:
                session_conf.update({
                    "shutdown_start_hour": new_start_hour,
                    "shutdown_end_hour": new_end_hour,
                    "last_proactive_shutdown_notify_iso": None, # 重置通知标志
                    "last_proactive_boot_notify_iso": None
                })
                self._save_session_settings()
            msg = f"关机时间已设置为: {new_start_hour:02d}:00 - {new_end_hour:02d}:00。"
            if not session_conf["shutdown_enabled"]: msg += "\n注意：关机功能当前仍关闭，请使用“/开启关机”激活。"
            yield event.plain_result(msg)
        except ValueError as e:
            yield event.plain_result(f"时间设置无效: {e}")

    @filter.command("查询关机时间", alias={"关机时间", "关机状态"})
    async def query_shutdown_time_command(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        session_conf = self._get_session_config(umo)
        status = "开启" if session_conf.get("shutdown_enabled", False) else "关闭"
        start_h, end_h = session_conf['shutdown_start_hour'], session_conf['shutdown_end_hour']
        
        shutdown_notify_info = ""
        boot_notify_info = ""

        yield event.plain_result(
            f"当前会话关机功能状态: {status}\n"
            f"关机时间设定: {start_h:02d}:00 - {end_h:02d}:00"
            f"{shutdown_notify_info}{boot_notify_info}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("清理睡眠状态")
    async def clean_sleep_state_command(self, event: AstrMessageEvent, action: str = "", target: str = ""):
        """
        [管理员] 清理会话睡眠状态，用于修复异常状态。
        用法:
        /清理睡眠状态                              - 查看所有会话状态摘要
        /清理睡眠状态 重置私聊                     - 重置所有私聊会话(禁用睡眠和关机)
        /清理睡眠状态 重置通知                     - 重置所有会话的通知标记(修复重复发送)
        """
        if action == "重置私聊":
            count = 0
            for umo in list(self.session_settings.keys()):
                if self._is_private_chat_umo(umo):
                    conf = self.session_settings[umo]
                    if conf.get("enabled", False) or conf.get("shutdown_enabled", False):
                        conf["enabled"] = False
                        conf["shutdown_enabled"] = False
                        count += 1
            self._save_session_settings()
            yield event.plain_result(f"已重置 {count} 个私聊会话的睡眠/关机状态为关闭。")

        elif action == "重置通知":
            count = 0
            notify_keys = [
                "last_proactive_notification_for_start_time",
                "last_proactive_wakeup_notification_iso",
                "last_proactive_shutdown_notify_iso",
                "last_proactive_boot_notify_iso",
            ]
            for conf in self.session_settings.values():
                for key in notify_keys:
                    if conf.get(key) is not None:
                        conf[key] = None
                        count += 1
            self._save_session_settings()
            yield event.plain_result(f"已清除 {count} 个通知标记。下次检测周期将重新发送通知。")

        else:
            total = len(self.session_settings)
            enabled_count = sum(1 for s in self.session_settings.values() if s.get("enabled", False))
            shutdown_count = sum(1 for s in self.session_settings.values() if s.get("shutdown_enabled", False))
            private_enabled = sum(
                1 for umo, s in self.session_settings.items()
                if self._is_private_chat_umo(umo) and s.get("enabled", False)
            )
            detail_lines = []
            for umo, conf in self.session_settings.items():
                chat_type = "私聊" if self._is_private_chat_umo(umo) else "群聊"
                flags = []
                if conf.get("enabled", False):
                    flags.append(f"睡眠({conf['start_hour']:02d}-{conf['end_hour']:02d})")
                if conf.get("shutdown_enabled", False):
                    flags.append(f"关机({conf['shutdown_start_hour']:02d}-{conf['shutdown_end_hour']:02d})")
                if flags:
                    detail_lines.append(f"  {chat_type} {umo}: {', '.join(flags)}")

            msg = (
                f"会话统计: 共 {total} 个\n"
                f"  - 开启睡眠: {enabled_count}\n"
                f"  - 开启关机: {shutdown_count}\n"
                f"  - 私聊异常开启: {private_enabled}\n"
            )
            if detail_lines:
                msg += "\n--- 已开启的会话 ---\n" + "\n".join(detail_lines)
            else:
                msg += "\n没有已开启的会话。"
            msg += (
                "\n\n管理员命令:\n"
                "  /清理睡眠状态 重置私聊   - 关闭所有私聊的睡眠/关机\n"
                "  /清理睡眠状态 重置通知   - 重置所有通知标记(修复重复发送)\n"
            )
            yield event.plain_result(msg)

    async def terminate(self):
        logger.info(f"{self.plugin_id_name} is terminating...")
        if hasattr(self, 'proactive_check_task') and self.proactive_check_task:
            self.proactive_check_task.cancel()
            try: await self.proactive_check_task
            except asyncio.CancelledError: logger.info("Proactive check task successfully cancelled.")
            except Exception as e: logger.error(f"Error during task cancellation: {e}", exc_info=True)
        logger.info(f"{self.plugin_id_name} terminated.")