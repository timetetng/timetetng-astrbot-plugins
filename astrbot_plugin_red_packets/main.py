# main.py
import random
import asyncio
import json
import uuid
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

# 导入
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from ..common.services import shared_services
from .database import RedPacketDatabase

# -- 插件元数据 --
__PLUGIN_METADATA__ = {
    "name": "RedPacket_Plugin",
    "author": "Gemini",
    "description": "功能完善的红包插件，支持持久化、限时和历史记录。",
    "version": "2.8.1", # 版本更新：优化历史记录逻辑
}

@register(
    __PLUGIN_METADATA__["name"],
    __PLUGIN_METADATA__["author"],
    __PLUGIN_METADATA__["description"],
    __PLUGIN_METADATA__["version"]
)
class RedPacketPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        
        plugin_dir = os.path.dirname(__file__)
        self.db = RedPacketDatabase(plugin_dir)
        
        self.expiration_check_task = asyncio.create_task(self._check_expired_packets_loop())
        self.lock = asyncio.Lock()
        logger.info("高级红包插件已加载 (整数/历史优化版)，并启动过期红包检查任务。")

    async def terminate(self):
        self.expiration_check_task.cancel()
        await self.db.close()
        logger.info("高级红包插件已卸载，后台任务已取消，数据库连接已关闭。")

    # --- 后台任务 ---
    async def _check_expired_packets_loop(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self.db._ensure_connected()
                expired_packets = await self.db.get_expired_packets()
                if not expired_packets:
                    continue

                economy_api = await self._get_economy_api()
                if not economy_api:
                    continue
                
                for packet in expired_packets:
                    claimed_by = json.loads(packet['claimed_by_json'])
                    claimed_amount = sum(data['amount'] for data in claimed_by.values())
                    refund_amount = packet['total_amount'] - claimed_amount
                    
                    if refund_amount > 0:
                        success = await economy_api.add_coins(
                            packet['sender_id'],
                            refund_amount,
                            f"红包(ID: {packet['packet_id'][:8]})过期退款"
                        )
                        if success and packet['unified_msg_origin']:
                            new_balance = await economy_api.get_coins(packet['sender_id'])
                            msg_chain_obj = MessageChain()
                            msg_chain_obj.chain = [
                                Comp.At(qq=int(packet['sender_id'])),
                                Comp.Plain(f"⏰ 您发送的红包已到期。\n退还剩余金额: {refund_amount} 金币\n💰 当前余额: {int(new_balance)} 金币")
                            ]
                            await self.context.send_message(packet['unified_msg_origin'], msg_chain_obj)

                    await self.db.remove_active_packet(packet['packet_id'])
            except Exception as e:
                logger.error(f"检查过期红包时发生错误: {e}", exc_info=True)

    # --- 辅助函数 ---
    async def _get_economy_api(self):
        return shared_services.get("economy_api")

    async def _get_display_name(self, user_id: str, fallback_name: str) -> str:
        nickname_api = shared_services.get("nickname_api")
        if nickname_api and (custom_name := await nickname_api.get_nickname(user_id)):
            return custom_name
        return fallback_name

    def _generate_lucky_amounts(self, total_amount: int, num_packets: int) -> List[int]:
        if num_packets <= 0 or total_amount <= 0: return []
        if num_packets == 1: return [total_amount]
        if total_amount < num_packets:
            amounts = [1] * total_amount + [0] * (num_packets - total_amount)
            random.shuffle(amounts)
            return amounts

        amounts = []
        remaining_amount = total_amount
        for i in range(num_packets - 1):
            max_alloc = remaining_amount - (num_packets - 1 - i)
            if max_alloc <= 1:
                amount = 1
            else:
                avg = remaining_amount / (num_packets - i)
                upper_bound = max(1, int(avg * 2 - 1))
                amount = random.randint(1, upper_bound)
                amount = min(amount, max_alloc)
            
            amounts.append(amount)
            remaining_amount -= amount
        
        amounts.append(remaining_amount)
        random.shuffle(amounts)
        return amounts

    async def _build_summary_message(self, packet_dict: Dict[str, Any], final_claimed_by: Dict[str, Any]) -> str:
        lines = [f"🧧「{packet_dict['sender_name']}」的红包已被领完！"]
        lines.append(f"总金额 {packet_dict['total_amount']} 金币, 共 {len(final_claimed_by)} 个。")
        lines.append("---领取详情---")

        for user_id, data in final_claimed_by.items():
            lines.append(f"· {data['name']}: {data['amount']} 金币")
        
        if packet_dict['packet_type'] in ['lucky', 'password'] and final_claimed_by:
            lucky_king_id = max(final_claimed_by, key=lambda uid: final_claimed_by[uid]['amount'])
            lucky_king_data = final_claimed_by[lucky_king_id]
            lines.append("----------------")
            lines.append(f"👑 {lucky_king_data['name']} 是手气王，领到了 {lucky_king_data['amount']} 金币！")
            
        return "\n".join(lines)

    # --- 事件监听器 ---
    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def on_claim_message(self, event: AstrMessageEvent):
        async with self.lock:
            msg_text = event.message_str.strip()
            
            packet_dict = await self.db.get_packet_by_password(msg_text)
            if not packet_dict:
                packet_dict = await self.db.get_packet_by_password(f"菲比{msg_text}")

            if packet_dict:
                await self._process_claim(event, packet_dict)
                return
            
            if "领红包" in msg_text:
                for component in event.message_obj.message:
                    if isinstance(component, Comp.At):
                        group_id = event.get_group_id() or f"private_{event.get_sender_id()}"
                        at_user_id = str(component.qq)
                        if packet_dict := await self.db.get_user_active_packet(at_user_id, group_id):
                            await self._process_claim(event, packet_dict)
                            return
                            
    # --- 指令 ---
    @filter.command("未领取红包")
    async def check_unclaimed_packets(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("该指令只能在群聊中使用。")
            return
        packets = await self.db.get_active_packets_in_group(group_id)
        if not packets:
            yield event.plain_result("🤷‍♂️ 当前群聊中没有可以领取的红包。")
            return
        response_lines = ["🔎 当前可领取的红包："]
        for p in packets:
            expires_dt = datetime.fromisoformat(p['expires_at'])
            remaining_time = expires_dt - datetime.now()
            if remaining_time.total_seconds() < 0: continue
            minutes = int(remaining_time.total_seconds() // 60)
            seconds = int(remaining_time.total_seconds() % 60)
            claim_method = f"口令: {p['password']}" if p['packet_type'] == 'password' else f"指令: @{p['sender_name']} 领红包"
            response_lines.append(f"🧧 来自「{p['sender_name']}」, 剩 {p['remaining_packets']} 个, {minutes}分{seconds}秒后失效。({claim_method})")
        if len(response_lines) == 1:
            yield event.plain_result("🤷‍♂️ 当前群聊中没有可以领取的红包。")
        else:
            yield event.plain_result("\n".join(response_lines))

    @filter.command("红包记录")
    async def show_records(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        history = await self.db.get_user_history(user_id)
        sent_total = sum(r['amount'] for r in history['sent'])
        received_total = sum(r['amount'] for r in history['received'])
        lines = [f"🧧 {await self._get_display_name(user_id, event.get_sender_name())} 的红包记录："]
        lines.append(f"📤 总计发出: {len(history['sent'])}个, 共 {sent_total} 金币")
        lines.append(f"📥 总计收到: {len(history['received'])}个, 共 {received_total} 金币")
        lines.append("📜---最近5条接收记录---")
        if not history['received']:
            lines.append("无")
        else:
            for r in history['received'][:5]:
                lines.append(f"[{r['timestamp'][:10]}] 收到 {r['sender_name']} 的红包: +{r['amount']}")
        yield event.plain_result("\n".join(lines))

    @filter.command("红包帮助")
    async def show_help(self, event: AstrMessageEvent):
        help_text = """🧧 红包插件使用指南 (v2.8.1) 🧧
------------------------
发送红包需额外支付约20%的手续费。
所有金额必须为整数。
------------------------
▸ 指令: `/红包 <总金额> <个数> <口令> [祝福语]`

▸ 示例: `/红包 114514 10 菲比啾比`

领取方式：发送口令"菲比啾比"即可领取。

💡 功能指令:
▸ `/未领取红包` - 查看本群未领完的红包。
▸ `/红包记录` - 查看自己的收发历史。"""
        yield event.plain_result(help_text)

    @filter.command("拼手气红包")
    async def send_lucky(self, event: AstrMessageEvent, total: int, count: int, *, greeting: str = "恭喜发财，大吉大利！"):
        sender_id = event.get_sender_id()
        group_id = event.get_group_id() or f"private_{sender_id}"
        if await self.db.get_user_active_packet(sender_id, group_id):
            yield event.plain_result("✋ 你在这个群聊中有一个未领完的红包，请等待它被领完或过期后再发。")
            return
        if total <= 0 or count <= 0:
            yield event.plain_result("⚠️ 红包金额和数量必须是正整数！")
            return
        if total < count:
            yield event.plain_result(f"⚠️ 总金额 ({total}) 不能小于红包个数 ({count})！")
            return
        economy_api = await self._get_economy_api()
        if not economy_api:
            yield event.plain_result("🚨 错误：经济系统未启用。")
            return
        
        fee = (total * 20 + 99) // 100
        total_cost = total + fee
        sender_balance = await economy_api.get_coins(sender_id)
        if sender_balance < total_cost:
            yield event.plain_result(f"😥 金币不足！需要 {total_cost} (含手续费)，你只有 {int(sender_balance)}。")
            return
        if not await economy_api.add_coins(sender_id, -total_cost, f"发送拼手气红包"):
            yield event.plain_result("😥 扣款失败，请稍后再试。")
            return
        
        new_balance = await economy_api.get_coins(sender_id)
        packet_id = str(uuid.uuid4())
        now = datetime.now()
        expires = now + timedelta(minutes=5)
        packet_data = {"packet_id": packet_id, "packet_type": "lucky", "sender_id": sender_id, "sender_name": await self._get_display_name(sender_id, event.get_sender_name()), "group_id": group_id, "created_at": now.isoformat(), "expires_at": expires.isoformat(), "total_amount": total, "remaining_packets": count, "greeting": greeting, "amounts_list": self._generate_lucky_amounts(total, count), "claimed_by": {}, "unified_msg_origin": event.unified_msg_origin}
        await self.db.add_active_packet(packet_data)
        await self.db.log_transaction(sender_id, packet_id, 'SEND', total, fee=fee)
        yield event.plain_result(f"🧧 「拼手气红包」发送成功！\n\n“{greeting}”\n\n请@发送者 并说“领红包”来领取 (5分钟内有效)。\n💰 您的余额: {int(new_balance)} 金币")

    @filter.command("普通红包", alias={"定额红包"})
    async def send_fixed(self, event: AstrMessageEvent, total: int, count: int, *, greeting: str = "恭喜发财，大吉大利！"):
        sender_id = event.get_sender_id()
        group_id = event.get_group_id() or f"private_{sender_id}"
        if await self.db.get_user_active_packet(sender_id, group_id):
            yield event.plain_result("✋ 你在这个群聊中有一个未领完的红包，请等待它被领完或过期后再发。")
            return
        if total <= 0 or count <= 0:
            yield event.plain_result("⚠️ 红包总金额和数量必须是正整数！")
            return
        if total < count:
            yield event.plain_result(f"⚠️ 总金额 ({total}) 不能小于红包个数 ({count})，否则没人能领到钱！")
            return

        amount_per = total // count
        distributable_total = amount_per * count
        economy_api = await self._get_economy_api()
        if not economy_api:
            yield event.plain_result("🚨 错误：经济系统未启用。")
            return
        
        fee = (total * 20 + 99) // 100
        total_cost = total + fee
        sender_balance = await economy_api.get_coins(sender_id)
        if sender_balance < total_cost:
            yield event.plain_result(f"😥 金币不足！需要 {total_cost} (含手续费)，你只有 {int(sender_balance)}。")
            return
        if not await economy_api.add_coins(sender_id, -total_cost, "发送定额红包"):
            yield event.plain_result("😥 扣款失败，请稍后再试。")
            return
        
        new_balance = await economy_api.get_coins(sender_id)
        packet_id = str(uuid.uuid4())
        now = datetime.now()
        expires = now + timedelta(minutes=5)
        packet_data = {"packet_id": packet_id, "packet_type": "fixed", "sender_name": await self._get_display_name(sender_id, event.get_sender_name()), "group_id": group_id, "created_at": now.isoformat(), "expires_at": expires.isoformat(), "total_amount": distributable_total, "remaining_packets": count, "greeting": greeting, "amount_per_packet": amount_per, "claimed_by": {}, "sender_id": sender_id, "unified_msg_origin": event.unified_msg_origin}
        await self.db.add_active_packet(packet_data)
        await self.db.log_transaction(sender_id, packet_id, 'SEND', total, fee=fee)
        yield event.plain_result(f"🧧 「普通红包」发送成功！\n\n“{greeting}”\n\n请@发送者 并说“领红包”来领取 (5分钟内有效)。\n💰 您的余额: {int(new_balance)} 金币")

    @filter.command("红包", alias={"口令红包"})
    async def send_password(self, event: AstrMessageEvent, total: int, count: int, password: str, *, greeting: str = "恭喜发财，大吉大利！"):
        clean_password = password.strip()
        if not clean_password:
            yield event.plain_result("⚠️ 口令不能为空或仅包含空格！")
            return
        sender_id = event.get_sender_id()
        group_id = event.get_group_id() or f"private_{sender_id}"
        if total <= 0 or count <= 0:
            yield event.plain_result("⚠️ 金额、数量必须为正整数！")
            return
        if total < count:
            yield event.plain_result(f"⚠️ 总金额 ({total}) 不能小于红包个数 ({count})！")
            return
        economy_api = await self._get_economy_api()
        if not economy_api:
            yield event.plain_result("🚨 错误：经济系统未启用。")
            return
        
        fee = (total * 20 + 99) // 100
        total_cost = total + fee
        sender_balance = await economy_api.get_coins(sender_id)
        if sender_balance < total_cost:
            yield event.plain_result(f"😥 金币不足！需要 {total_cost} (含手续费)，你只有 {int(sender_balance)}。")
            return
        if not await economy_api.add_coins(sender_id, -total_cost, "发送口令红包"):
            yield event.plain_result("😥 扣款失败，请稍后再试。")
            return
        
        new_balance = await economy_api.get_coins(sender_id)
        packet_id = str(uuid.uuid4())
        now = datetime.now()
        expires = now + timedelta(minutes=5)
        packet_data = {"packet_id": packet_id, "packet_type": "password", "sender_name": await self._get_display_name(sender_id, event.get_sender_name()), "group_id": group_id, "created_at": now.isoformat(), "expires_at": expires.isoformat(), "total_amount": total, "remaining_packets": count, "greeting": greeting, "password": clean_password, "amounts_list": self._generate_lucky_amounts(total, count), "claimed_by": {}, "sender_id": sender_id, "unified_msg_origin": event.unified_msg_origin}
        await self.db.add_active_packet(packet_data)
        await self.db.log_transaction(sender_id, packet_id, 'SEND', total, fee=fee)
        yield event.plain_result(f"🧧 「口令红包」发送成功！(拼手气)\n\n“{greeting}”\n\n发送口令 “{clean_password}” 即可领取 (5分钟内有效)。\n💰 您的余额: {int(new_balance)} 金币")
        
    async def _process_claim(self, event: AstrMessageEvent, packet_dict: Dict[str, Any]):
        if datetime.now() > datetime.fromisoformat(packet_dict['expires_at']):
            await event.send(event.plain_result("⏰ 这个红包已经过期了。"))
            asyncio.create_task(self.db.remove_active_packet(packet_dict['packet_id']))
            return
        
        claimer_id = event.get_sender_id()
        claimed_by = json.loads(packet_dict['claimed_by_json'])
        
        if claimer_id in claimed_by:
            await event.send(event.plain_result("🤭 你已经领过这个红包了哦。"))
            return

        amounts_list = json.loads(packet_dict.get('amounts_json') or '[]')
        amount = 0
        if packet_dict['packet_type'] in ['lucky', 'password']:
            if not amounts_list:
                await event.send(event.plain_result("😭 手慢了，红包已经被领完了！"))
                return
            amount = amounts_list.pop(0)
        else: # fixed
            amount = packet_dict['amount_per_packet']
        
        if amount <= 0:
            await event.send(event.plain_result("💨 这个小红包是空的，下次手速快点哦！"))
        
        economy_api = await self._get_economy_api()
        if amount > 0:
            if not await economy_api.add_coins(claimer_id, amount, f"领取{packet_dict['sender_name']}的红包"):
                await event.send(event.plain_result("🚨 错误：金币发放失败。"))
                return
        
        new_balance = await economy_api.get_coins(claimer_id)
        claimer_name = await self._get_display_name(claimer_id, event.get_sender_name())
        
        claimed_by[claimer_id] = {"amount": amount, "name": claimer_name}
        remaining_packets = packet_dict['remaining_packets'] - 1
        
        await self.db.update_packet_claim(
            packet_dict['packet_id'], remaining_packets, json.dumps(amounts_list), json.dumps(claimed_by)
        )
        
        # --- 核心修改：记录领取日志时，传入发送者名字 ---
        await self.db.log_transaction(
            claimer_id, 
            packet_dict['packet_id'], 
            'RECEIVE', 
            amount, 
            related_user_id=packet_dict['sender_id'],
            sender_name=packet_dict['sender_name']
        )

        if amount > 0:
            await event.send(event.plain_result(f"🎉 恭喜 {claimer_name} 领取了 {packet_dict['sender_name']} 的红包，获得 {amount} 金币！\n💰 您的余额: {int(new_balance)} 金币"))
        
        if remaining_packets <= 0:
            summary = await self._build_summary_message(packet_dict, claimed_by)
            await asyncio.sleep(0.5)
            await event.send(event.plain_result(summary))
            await self.db.remove_active_packet(packet_dict['packet_id'])