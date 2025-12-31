import time
import json
import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.event import MessageChain, filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

from .database import Database
from .utils import Visualizer
from .logic import LLMModerator

try:
    from ..common.services import shared_services
except ImportError:
    shared_services = None

@register(
    "astrbot_plugin_nsfw_guard",
    "TimeXingjian",
    "基于好感度的动态NSFW审核插件",
    "2.0.0",
    ""
)
class NSFWGuardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = Path("data") / "plugins" / "nsfw_guard"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化各模块
        self.db = Database(self.data_dir)
        self.vis = Visualizer(self.data_dir, config)
        self.moderator = LLMModerator(context, config)
        
        self.recent_bot_replies = {}
        logger.info("NSFW Guard 已加载。")

    async def terminate(self):
        self.db.close()
        logger.info("NSFW Guard 插件已停用。")

    # --- LLM 拦截器 ---
    @filter.on_llm_request()
    async def block_check_on_llm(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.config.get("enabled", True): return
        
        user_data = self.db.get_user_data(event.get_sender_id())
        if time.time() < user_data["block_until"]:
            reply = self.config.get("blocked_reply_message", "您目前处于封禁状态。")
            result = event.make_result()
            result.chain = [Comp.Plain(reply)]
            await event.send(result)
            event.stop_event()

    @filter.on_llm_response(priority=10000)
    async def store_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        if resp and resp.completion_text:
            self.recent_bot_replies[event.unified_msg_origin] = resp.completion_text

    # --- 核心审核逻辑 ---
    @filter.after_message_sent()
    async def nsfw_check(self, event: AstrMessageEvent):
        """消息发送后异步执行审核"""
        if not self.config.get("enabled", True): return
        
        sender_id = event.get_sender_id()
        if not sender_id: return
        
        # 白名单检查
        if sender_id in self.config.get("whitelist_users", []): return
        if event.get_group_id() in self.config.get("whitelist_groups", []): return

        # 异步启动审核
        asyncio.create_task(self._audit_task(event))

    async def _audit_task(self, event: AstrMessageEvent):
        """后台审核任务"""
        user_msg = event.message_str
        
        # 1. 关键词检测
        keywords = self.config.get("nsfw_keywords", [])
        if any(k.lower() in user_msg.lower() for k in keywords):
            await self._handle_violation(event, "关键词", "触发预设敏感词")
            return

        # 2. LLM 检测 (如果配置开启)
        if not self.config.get("llm_detection", {}).get("enabled", False):
            return

        # 获取上下文和好感度信息
        favour_info = await self._get_favour_info(event)
        history = await self._get_history(event)
        bot_reply = self.recent_bot_replies.get(event.unified_msg_origin, "")

        # 执行 LLM 审核
        is_violation, reason, stage = await self.moderator.check_content(
            user_msg, bot_reply, favour_info, history
        )

        if is_violation:
            await self._handle_violation(event, stage, reason)

    async def _get_favour_info(self, event):
        """获取好感度文本"""
        info = "# 【当前好感度状态】\n- 状态: 普通关系 (无记录)"
        if shared_services and (api := shared_services.get("favour_pro_api")):
            try:
                s = await api.get_user_state(event.get_sender_id(), event.unified_msg_origin)
                if s: info = f"# 【当前好感度状态】\n- 好感度: {s.get('favour')}\n- 印象: {s.get('attitude')}"
            except: pass
        return info

    async def _get_history(self, event):
        """获取对话历史"""
        turns = self.config.get("llm_detection", {}).get("context_turns", 0)
        if turns <= 0: return []
        try:
            cid = await self.context.conversation_manager.get_curr_conversation_id(event.unified_msg_origin)
            if cid:
                conv = await self.context.conversation_manager.get_conversation(event.unified_msg_origin, cid)
                if conv and conv.history:
                    return json.loads(conv.history)[-(turns*2):]
        except: pass
        return []

    async def _handle_violation(self, event: AstrMessageEvent, trigger_method: str, reason: str):
        """处理违规逻辑"""
        uid = event.get_sender_id()
        user_name = event.get_sender_name()
        
        user_data = self.db.get_user_data(uid)
        count = user_data["count"]
        
        # 冷却判定
        cooldown = self.config.get("offense_cooldown_minutes", 30) * 60
        if count > 0 and (time.time() - user_data["last_offense"] > cooldown):
            count -= 1
        
        count += 1
        threshold = self.config.get("warning_threshold", 3)
        offense_type = "warning"
        block_until = user_data["block_until"]
        
        if count >= threshold:
            offense_type = "block"
            duration = self.config.get("block_duration_minutes", 60)
            block_until = time.time() + duration * 60
            msg_tmpl = self.config.get("block_message", "{user_name} 已被封禁 {duration} 分钟。")
            msg_to_send = msg_tmpl.format(user_name=user_name, duration=duration)
            count = 0 # 封禁后重置计数
        else:
            msg_tmpl = self.config.get("warning_message", "警告！违规次数: {count}/{threshold}")
            msg_to_send = msg_tmpl.format(user_name=user_name, count=count, threshold=threshold)

        # 记录并更新
        self.db.log_offense(uid, user_name, event.get_group_id(), offense_type, trigger_method, reason, event.message_str)
        self.db.update_user_data(uid, count, block_until, time.time())

        # 发送通知 (构建转发节点)
        detail = f"{msg_to_send}\n\n--- 详情 ---\n方式: {trigger_method}\n原因: {reason}\n内容: {event.message_str}"
        bot_qq = self.config.get("bot_qq") or getattr(event, 'self_id', "10000")
        
        node = Comp.Node(uin=bot_qq, name="系统审查", content=[Comp.Plain(detail)])
        
        if event.get_group_id():
            await self.context.send_message(event.unified_msg_origin, MessageChain([Comp.At(qq=uid), Comp.Plain(" 系统检测到违规内容，请查看详情。")]))
            await self.context.send_message(event.unified_msg_origin, MessageChain([node]))
        else:
            await self.context.send_message(event.unified_msg_origin, MessageChain.from_str(detail))

    # --- 管理员指令 ---
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("解封", alias={"unban"})
    async def unban_user(self, event: AstrMessageEvent):
        target = self._extract_target(event)
        if not target:
            yield event.plain_result("请指定用户：/解封 @用户 或 /解封 <QQ号>")
            return
        
        self.db.update_user_data(target, 0, 0, 0)
        yield event.plain_result(f"用户 {target} 已解封并重置违规次数。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("审核统计")
    async def get_stats(self, event: AstrMessageEvent):
        overall, top_users, top_groups = self.db.get_stats()
        
        text = "📊 NSFW 统计报告\n====================\n"
        text += f"总警告: {overall.get('warning', 0)} | 总封禁: {overall.get('block', 0)}\n\n"
        
        text += "#### 🚫 违规用户 Top 10\n"
        for i, (name, uid, c) in enumerate(top_users):
            text += f"{i+1}. {name}({uid}): {c}次\n"
            
        text += "\n#### 🏠 违规群聊 Top 10\n"
        for i, (gid, c) in enumerate(top_groups):
            text += f"{i+1}. {gid}: {c}次\n"
            
        img_path = await asyncio.to_thread(self.vis.text_to_image, text)
        yield event.image_result(img_path)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查询用户违规")
    async def query_user(self, event: AstrMessageEvent):
        target = self._extract_target(event)
        if not target:
            yield event.plain_result("请指定用户。")
            return
            
        logs = self.db.get_user_logs(target)
        user_data = self.db.get_user_data(target)
        
        text = f"📜 用户 {target} 记录\n"
        text += f"当前违规: {user_data['count']} | 封禁至: {time.strftime('%Y-%m-%d %H:%M', time.localtime(user_data['block_until'])) if user_data['block_until'] > time.time() else '无'}\n"
        text += "====================\n"
        for ts, otype, reason, msg in logs:
            text += f"[{time.strftime('%m-%d %H:%M', time.localtime(ts))}] {otype}: {reason}\n消息: {msg[:20]}...\n---\n"
            
        img_path = await asyncio.to_thread(self.vis.text_to_image, text)
        yield event.image_result(img_path)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("审核词云")
    async def wordcloud(self, event: AstrMessageEvent):
        yield event.plain_result("正在生成违规内容词云...")
        try:
            # 1. 从数据库获取所有违规的原消息
            messages = self.db.get_all_offending_messages()
            # 2. 生成词云 (在线程中运行以免阻塞)
            img_path = await asyncio.to_thread(self.vis.generate_wordcloud, messages)
            yield event.image_result(img_path)
            
            # 延迟删除
            await asyncio.sleep(10)
            Path(img_path).unlink(missing_ok=True)
            
        except ValueError as e:
            yield event.plain_result(f"生成失败: {e}")
        except Exception as e:
            logger.error(f"词云生成错误: {e}")
            yield event.plain_result("生成出错，请检查日志或确认已安装 jieba/wordcloud。")

    def _extract_target(self, event: AstrMessageEvent):
        for comp in event.message_obj.message:
            if isinstance(comp, Comp.At): return comp.qq
        args = event.message_str.split()
        if len(args) > 1 and args[1].isdigit(): return args[1]
        return None
