import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Any

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp


@register(
    "astrbot_plugin_todo",
    "timetetng",
    "一个由LLM驱动、支持持久化的智能代办提醒插件",
    "1.4",
    "https://github.com/timetetng/astrbot_plugin_todo",
)
class TodoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_todo")
        os.makedirs(self.data_dir, exist_ok=True)
        self.tasks_file = os.path.join(self.data_dir, "tasks.json")
        self.lock = asyncio.Lock()
        
        asyncio.create_task(self._load_and_reschedule_tasks())

    async def _load_tasks(self) -> List[Dict[str, Any]]:
        """从JSON文件安全地加载任务列表"""
        async with self.lock:
            if not os.path.exists(self.tasks_file):
                return []
            try:
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return []

    async def _save_tasks(self, tasks: List[Dict[str, Any]]):
        """将任务列表安全地保存到JSON文件"""
        async with self.lock:
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                json.dump(tasks, f, indent=4, ensure_ascii=False)

    async def _remove_task(self, task_id: str):
        """根据任务ID移除一个持久化的任务"""
        tasks = await self._load_tasks()
        tasks = [t for t in tasks if t.get("id") != task_id]
        await self._save_tasks(tasks)
        logger.info(f"任务 {task_id} 已完成并从持久化存储中移除。")

    async def _load_and_reschedule_tasks(self):
        """加载并重新调度所有未过期的任务"""
        logger.info("正在加载并重新调度持久化的提醒任务...")
        tasks = await self._load_tasks()
        current_time = datetime.now()
        pending_tasks = []

        for task in tasks:
            try:
                reminder_time = datetime.fromisoformat(task["reminder_time"])
                if reminder_time > current_time:
                    delay = (reminder_time - current_time).total_seconds()
                    asyncio.create_task(
                        self._send_reminder_task(
                            task["id"],
                            delay,
                            task["umo"],
                            task["sender_id"],
                            # 读取持久化的提醒文案
                            task["reminder_content"],
                        )
                    )
                    pending_tasks.append(task)
                    logger.info(f"已重新调度任务 '{task['reminder_content']}' (ID: {task['id']})")
                else:
                    logger.info(f"任务 '{task['reminder_content']}' (ID: {task['id']}) 已过期，将被清理。")
            except (KeyError, TypeError) as e:
                logger.warning(f"加载任务失败，任务数据格式不正确: {task}，错误: {e}")

        if len(pending_tasks) != len(tasks):
             await self._save_tasks(pending_tasks)
        logger.info(f"持久化任务加载完成，重新调度了 {len(pending_tasks)} 个任务。")

    async def _send_reminder_task(
        self, task_id: str, delay: float, umo: str, sender_id: str, reminder_content: str
    ):
        """后台异步任务，用于发送由LLM预设的提醒文案"""
        try:
            await asyncio.sleep(delay)

            component_list = [
                Comp.At(qq=sender_id),
                Comp.Plain(f" {reminder_content}"), # 加一个空格，让@和消息内容分开
            ]
            
            message_to_send = MessageChain(component_list)

            await self.context.send_message(umo, message_to_send)
            logger.info(f"已成功发送提醒给 {sender_id}，内容：{reminder_content}")

            await self._remove_task(task_id)

        except asyncio.CancelledError:
            logger.info(f"任务 '{reminder_content}' (ID: {task_id}) 已被取消。")
        except Exception as e:
            logger.error(f"发送提醒 (ID: {task_id}) 时发生错误: {e}")

    @filter.llm_tool(name="add_reminder")
    async def add_reminder(
        self, event: AstrMessageEvent, time_str: str, reminder_message: str
    ) -> str:
        """
        添加一个未来的提醒事项，并由LLM预设提醒时的回复文案。
        Args:
            time_str(string): 提醒的具体时间，必须是 'YYYY-MM-DD HH:MM:SS' 格式。请根据用户意图和当前时间计算出这个绝对时间。
            reminder_message(string): 由LLM生成的、届时要发送给用户的完整、自然的提醒消息。例如："时间到啦，该起床了哦！"
        """
        logger.info(f"LLM调用代办工具: time='{time_str}', message='{reminder_message}'")

        try:
            reminder_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()

            if reminder_time <= current_time:
                return f"任务设置失败：你提供的时间 {time_str} 已经过去了，请提供一个未来的时间。"

            delay_seconds = (reminder_time - current_time).total_seconds()

            new_task = {
                "id": str(uuid.uuid4()),
                "reminder_time": reminder_time.isoformat(),
                "umo": event.unified_msg_origin,
                "sender_id": event.get_sender_id(),
                "reminder_content": reminder_message, # 存储LLM生成的完整文案
                "created_at": current_time.isoformat(),
            }

            tasks = await self._load_tasks()
            tasks.append(new_task)
            await self._save_tasks(tasks)

            asyncio.create_task(
                self._send_reminder_task(
                    task_id=new_task["id"],
                    delay=delay_seconds,
                    umo=new_task["umo"],
                    sender_id=new_task["sender_id"],
                    reminder_content=new_task["reminder_content"],
                )
            )

            logger.info(f"已成功创建并持久化任务: {new_task['id']}")
            # 返回给LLM的确认信息，让它知道自己设置的提醒文案是什么
            return f"任务已成功设置！我会在 {time_str} 提醒你。"

        except ValueError:
            logger.error(f"时间格式解析错误: '{time_str}'")
            return f"任务设置失败：时间格式不正确，我无法理解 '{time_str}'。请确保时间格式为 'YYYY-MM-DD HH:MM:SS'。"
        except Exception as e:
            logger.error(f"设置提醒时发生未知错误: {e}")
            return f"任务设置失败：发生了一个内部错误，请稍后再试或联系管理员。"