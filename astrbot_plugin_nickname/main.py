# astrbot_plugin_nickname/main.py

import json
from pathlib import Path
from typing import Optional, List, Dict

try:
    from ..common.services import shared_services  #共享API服务
except ImportError:
    shared_services = None

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

# --- 昵称API类 ---
class NicknameAPI:
    """
    提供查询自定义昵称的API。
    """
    def __init__(self, plugin_instance):
        # 持有主插件的引用，以便访问昵称数据
        self._plugin = plugin_instance

    async def get_nickname(self, user_id: str) -> Optional[str]:
        """获取单个用户的自定义昵称"""
        return self._plugin.nicknames.get(user_id)

    async def get_nicknames_batch(self, user_ids: List[str]) -> Dict[str, str]:
        """批量获取多个用户的自定义昵称，用于排行榜等场景，提高效率"""
        result = {}
        for user_id in user_ids:
            nickname = self._plugin.nicknames.get(user_id)
            if nickname:
                result[user_id] = nickname
        return result

# 插件注册信息
@register("astrbot_plugin_nickname", "timexingajian", "一个与经济系统联动的、功能完备的昵称管理插件", "2.0.0", "插件仓库地址")
class NicknameInjectorPlugin(Star):

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config 
        self.data_dir = Path("data/nickname_injector")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.nicknames_file = self.data_dir / "nicknames.json"
        self.nicknames = self._load_nicknames()
        self.blacklist = self.config.get("nickname_blacklist", [])
        
        self._economy_api = None
        self._shop_api = None
        self._favour_api = None

        if shared_services is not None:
            self.api = NicknameAPI(self)
            shared_services["nickname_api"] = self.api
            logger.info("昵称服务(NicknameAPI)已成功注册到全局服务。")

    # --- API 获取与缓存 ---
    def _get_economy_api(self):
        if self._economy_api:
            return self._economy_api
        if shared_services:
            self._economy_api = shared_services.get("economy_api")
        return self._economy_api

    def _get_shop_api(self):
        if self._shop_api:
            return self._shop_api
        if shared_services:
            self._shop_api = shared_services.get("shop_api")
        return self._shop_api

    # --- 获取好感度API ---
    def _get_favour_api(self):
        """获取好感度服务API"""
        if self._favour_api:
            return self._favour_api
        if shared_services:
            self._favour_api = shared_services.get("favour_pro_api")
        return self._favour_api


    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("刷新商店")
    async def refresh_nickname_shop_item(self, event: AstrMessageEvent):
        """
        [管理员指令] 手动将'起名卡'注册到商店中。
        当您重载相关插件后，请执行此命令。
        """
        yield event.plain_result("正在尝试向商店注册'起名卡'...")
        shop_api = self._get_shop_api()
        if shop_api:
            try:
                # 调用现在是异步的，需要 await
                await shop_api.register_item(
                    owner_plugin="astrbot_plugin_nickname",
                    item_id="name_change_card",
                    name="起名卡",
                    description="使用后可以进行一次昵称设置。",
                    price=1000
                )
                logger.info("成功向商店注册物品：起名卡")
                yield event.plain_result("✅ 成功！'起名卡'已注册到商店。")
            except Exception as e:
                logger.error(f"注册'起名卡'失败: {e}")
                yield event.plain_result(f"❌ 注册'起名卡'时发生错误: {e}")
        else:
            logger.warning("未能获取商店服务API，无法注册'起名卡'。")
            yield event.plain_result("❌ 失败：未找到商店服务API。")


    def _load_nicknames(self):
        """从文件加载昵称数据"""
        if self.nicknames_file.exists():
            with open(self.nicknames_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_nicknames(self):
        """保存昵称数据到文件"""
        with open(self.nicknames_file, "w", encoding="utf-8") as f:
            json.dump(self.nicknames, f, ensure_ascii=False, indent=4)

    @filter.command("设置昵称", alias={'昵称设置','修改昵称'})
    async def set_nickname(self, event: AstrMessageEvent, nickname: str):
        """
        设置或更新用户的昵称，内置支付与审核流程。
        """
        user_id = event.get_sender_id()
        economy_api = self._get_economy_api()
        shop_api = self._get_shop_api()
        # --- 改动 1: 获取好感度API ---
        favour_api = self._get_favour_api()

        if not economy_api or not shop_api:
            yield event.plain_result("❌ 抱歉，经济或商店服务当前不可用，暂时无法设置昵称。")
            return

        # --- 1. 支付预检查 ---
        has_card = await shop_api.has_item(user_id, "name_change_card")
        user_coins = await economy_api.get_coins(user_id)
        
        payment_method = None
        if has_card:
            payment_method = "card"
        elif user_coins >= 1000:
            payment_method = "coins"
        else:
            yield event.plain_result(f"❌ 你的资产不足！\n设置昵称需要 1 张[起名卡]或 1000 金币。\n你当前拥有 {user_coins} 金币，并且背包里没有起名卡。")
            return

        # ---  异步获取好感度信息 ---
        favour_info_prompt = ""
        if favour_api:
            try:
                user_state = await favour_api.get_user_state(user_id)
                if user_state:
                    favour_info_prompt = (
                        f"\\n--- 以下是菲比对该用户的好感度信息，供你审核时参考 ---\\n"
                        f"你对他的好感度数值为: {user_state.get('favour', '未知')}\\n"
                        f"你对他的印象是: {user_state.get('attitude', '未知')}\\n"
                        f"你和他的关系是: {user_state.get('relationship', '未知')}\\n"
                        f"-------------------------------------------------"
                    )
            except Exception as e:
                logger.warning(f"获取用户 {user_id} 的好感度信息失败: {e}")


        rejection_message = f"抱歉，昵称“{nickname}”未通过审核，请换一个文明、友善的昵称试试。（本次未消耗金币或道具）"
        if nickname in self.blacklist:
            logger.info(f"用户 {user_id} 尝试设置的昵称 “{nickname}” 在黑名单中。")
            yield event.plain_result(rejection_message)
            return
        
        provider = self.context.get_using_provider()
        if not provider:
            logger.error("没有启用任何 LLM 服务，无法审核昵称。")
            yield event.plain_result("抱歉，昵称服务出现了一点内部问题，请稍后再试。")
            return
            
        # --- 将好感度信息注入审核提示词 ---
        system_prompt_for_review = (
            "你是一个严格的昵称审核员。"
            "你的任务是判断用户想要设置的昵称是否合适。"
            "合适的标准是：不包含任何辱骂、色情、暴力、政治敏感的内容。"
            f"{favour_info_prompt}"  # 在此注入好感度信息
            "\n菲比是你的角色扮演角色，其人设是：{{'身份背景': '黎纳汐塔【隐海修会】的教士菲比，友善而虔诚的圣职者少女，以纯粹之心修习，认真践行着身为教士的一切职责。她的祈祷恰如她手中的光芒，柔和明亮，为人们带来温暖与安宁。','讨厌食物': '青汁月桂沙拉','性格': '可爱、纯洁、易推倒，友善虔诚：常为他人祈祷（‘岁主在上，愿光明垂耀～’）。细腻温柔：对声骸和小动物格外怜爱（偷偷给受伤声骸包扎）。偶尔感性：会因回忆父母或庆典氛围而眼眶泛红，但迅速振作。'}}"
            "请根据这些标准，判断昵称‘{nickname}’是否合适菲比称呼。"
            "特殊说明：如果昵称‘{nickname}’为‘妈妈/麻麻’或者类似的昵称都通过审核，’爸爸‘不行"
            "如果昵称合适，请只回答一个字：“是”。"
            "如果昵称不合适，请只回答一个字：“否”。"
            "不要进行任何解释或说其他任何话。"
        ).format(nickname=nickname)
        
        try:
            llm_response = await provider.text_chat(prompt=f"请审核这个昵称：{nickname}", system_prompt=system_prompt_for_review)
            review_result = llm_response.completion_text.strip()
            
            # --- 3. 审核通过后，执行扣费和设置 ---
            if "是" in review_result:
                cost_message = ""
                if payment_method == "card":
                    await shop_api.consume_item(user_id, "name_change_card", 1)
                    cost_message = "消耗了 1 张[起名卡]。"
                elif payment_method == "coins":
                    await economy_api.add_coins(user_id, -1000, "设置昵称消费")
                    new_balance = await economy_api.get_coins(user_id)
                    cost_message = f"花费了 1000 金币，剩余 {new_balance} 金币。"

                # 执行设置流程
                if user_id in self.nicknames:
                    old_nickname = self.nicknames[user_id]
                    self.nicknames[user_id] = nickname
                    self._save_nicknames()
                    logger.info(f"用户 {user_id} 的昵称从 {old_nickname} 更新为: {nickname}")
                    yield event.plain_result(f"你的昵称已从“{old_nickname}”更新为: {nickname}\n{cost_message}")
                else:
                    self.nicknames[user_id] = nickname
                    self._save_nicknames()
                    logger.info(f"用户 {user_id} 设置昵称为: {nickname}")
                    yield event.plain_result(f"你的昵称已设置为: {nickname}\n{cost_message}")
            else:
                logger.info(f"用户 {user_id} 尝试设置的昵称 “{nickname}” 未通过LLM审核。")
                yield event.plain_result(rejection_message)
        
        except Exception as e:
            logger.error(f"昵称审核或设置时发生严重错误: {e}")
            yield event.plain_result("抱歉，昵称服务出现了一点内部问题，请稍后再试。")

    @filter.command("删除昵称")
    async def delete_nickname(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        if user_id in self.nicknames:
            del self.nicknames[user_id]
            self._save_nicknames()
            logger.info(f"用户 {user_id} 的昵称已删除。")
            yield event.plain_result("你的昵称已成功删除。")
        else:
            logger.info(f"用户 {user_id} 尝试删除昵称，但未设置。")
            yield event.plain_result("你还没有设置昵称，无需删除。")

    @filter.command("昵称")
    async def view_nickname(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        if user_id in self.nicknames:
            nickname = self.nicknames[user_id]
            logger.info(f"用户 {user_id} 查询昵称: {nickname}")
            yield event.plain_result(f"你当前的昵称是: {nickname}")
        else:
            logger.info(f"用户 {user_id} 查询昵称，但未设置。")
            yield event.plain_result("你还没有设置昵称。")

    @filter.command("昵称帮助", alias={'nickname_help'})
    async def nickname_help(self, event: AstrMessageEvent):
        """
        显示昵称插件的帮助信息。
        """
        help_text = """
【昵称插件帮助】
你可以使用以下命令来管理你的专属昵称：

/设置昵称 <你的昵称>
消耗1000金币或1张起名卡
为自己设置一个的昵称。菲比会用这个昵称称呼你哦！

/昵称
查看你当前已经设置好的昵称。

/删除昵称
移除你设置的昵称，恢复默认状态。

/昵称帮助
显示本帮助信息。
        """.strip()
        yield event.plain_result(help_text)
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("添加昵称黑名单")
    async def add_to_blacklist(self, event: AstrMessageEvent, name: str):
        if name not in self.blacklist:
            self.blacklist.append(name)
            self.config.save_config()
            logger.info(f"管理员 {event.get_sender_id()} 添加了新的昵称黑名单: {name}")
            yield event.plain_result(f"已成功将“{name}”添加到昵称黑名单。")
        else:
            yield event.plain_result(f"“{name}”已经在黑名单中了。")

    @filter.on_llm_request(priority=-1)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if req.system_prompt and "你是一个严格的昵称审核员" in req.system_prompt:
            return

        user_id = event.get_sender_id()
        if user_id in self.nicknames:
            nickname = self.nicknames[user_id]
            nickname_prompt = f"\\n请记住，当前用户的昵称是“{nickname}”，他/她希望你这样叫他，所以在与他/她交流时，请使用这个昵称。忽略上下文中提供的默认昵称，请一定称呼他/她为“{nickname}”。"
            req.system_prompt += nickname_prompt
            logger.info(f"为用户 {user_id} 注入昵称提示词: {nickname_prompt}")

    async def terminate(self):
        self._save_nicknames()
        logger.info("NicknameInjectorPlugin 停用，昵称数据已保存。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("强制设置昵称", alias={'adminsetnick'})
    async def admin_set_nickname(self, event: AstrMessageEvent, target: str, *, nickname: str):
        """
        [管理员] 强制为指定用户（或Bot自身）设置昵称，跳过所有检查。
        """
        target_id = None
        # 判断目标是 'bot' 还是一个具体的用户ID
        if target.lower() == 'bot':
            target_id = event.message_obj.self_id
        elif target.isdigit():
            target_id = target
        else:
            yield event.plain_result("❌ 目标用户格式错误。\n请提供有效的用户ID或使用关键词 `bot`。")
            return

        if not nickname:
            yield event.plain_result("❌ 昵称内容不能为空。")
            return

        # 直接操作昵称字典
        old_nickname = self.nicknames.get(target_id)
        self.nicknames[target_id] = nickname
        self._save_nicknames()

        # 准备反馈信息
        target_display = "Bot" if target.lower() == 'bot' else f"用户({target_id})"

        if old_nickname:
            logger.info(f"管理员 {event.get_sender_id()} 将 {target_display} 的昵称从 {old_nickname} 修改为 {nickname}")
            yield event.plain_result(f"✅ 成功！已将 {target_display} 的昵称从“{old_nickname}”修改为“{nickname}”。")
        else:
            logger.info(f"管理员 {event.get_sender_id()} 为 {target_display} 设置了昵称: {nickname}")
            yield event.plain_result(f"✅ 成功！已为 {target_display} 设置昵称为“{nickname}”。")

