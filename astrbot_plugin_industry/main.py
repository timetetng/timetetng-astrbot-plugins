# astrbot_plugin_industry/main.py
import asyncio
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from ..common.services import shared_services
from . import data_manager, config
from .service import CompanyService
import astrbot.api.message_components as Comp
from typing import List, Dict, Any


class IndustryAPI:
    """
    虚拟产业插件对外暴露的API。
    用于查询用户的公司资产。
    """

    def __init__(self, plugin_instance: "IndustryPlugin"):
        self._plugin = plugin_instance

    async def get_company_asset_value(self, user_id: str) -> int:
        """
        获取单个用户的公司资产净值。
        如果用户没有公司，则返回 0。
        """
        return await self._plugin.get_asset_value_for_api(user_id)

    async def get_top_companies_by_value(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取公司资产价值排行榜 (供API调用)。
        这个方法会实时计算所有公司的价值（包括市值和固定资产）并排序。

        Args:
            limit (int): 返回排行榜的公司数量，默认为10。

        Returns:
            一个字典列表，每个字典包含 'user_id', 'company_name', 'asset_value'。
            例如: [{'user_id': '123', 'company_name': '我的公司', 'asset_value': 500000}, ...]
        """
        return await self._plugin._get_top_companies_for_api(limit)


@register(
    "astrbot_plugin_industry",
    "timetetng",
    "虚拟产业插件，一个公司经营玩法。",
    "1.0.0",
    "https://github.com/YourRepo/astrbot_plugin_industry",
)
class IndustryPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.service = CompanyService(self)

        self.api = IndustryAPI(self)
        shared_services["industry_api"] = self.api
        logger.info("虚拟产业API (industry_api) 已成功注册。")

    async def get_asset_value_for_api(self, user_id: str) -> int:
        """供API调用的内部方法，用于查询公司资产 (V3 - 统一调用市值API)"""
        company = await data_manager.get_company(user_id)
        if not company:
            return 0

        if company.get("is_public"):
            # 如果是上市公司，其价值是市值
            stock_api = shared_services.get("stock_market_api")
            if stock_api and hasattr(
                stock_api, "get_market_cap"
            ):  # 确保API和方法都存在
                try:
                    market_cap = await stock_api.get_market_cap(company["stock_ticker"])
                    if market_cap is not None:
                        return int(market_cap)
                except Exception as e:
                    logger.error(
                        f"调用 stock_api.get_market_cap 时发生错误: {e}", exc_info=True
                    )

            # 如果API或市值获取失败，返回0作为安全默认值
            return 0
        else:
            # 私有公司，价值是固定资产
            level = company.get("level", 1)
            return config.COMPANY_LEVELS.get(level, {}).get("assets", 0)

    async def _get_top_companies_for_api(self, limit: int = 10) -> List[Dict[str, Any]]:
        """[内部方法] 计算所有公司的资产价值并返回前 N 名，供API调用。"""
        # 1. 获取所有公司的基础信息
        all_companies = await data_manager.get_all_companies()
        if not all_companies:
            return []

        # 2. 使用 asyncio.gather 并发计算所有公司的当前资产价值，以提高效率
        tasks = [
            self.get_asset_value_for_api(company["user_id"])
            for company in all_companies
        ]
        asset_values = await asyncio.gather(*tasks)

        # 3. 将公司信息和其对应的资产价值配对
        company_data_with_value = []
        for i, company in enumerate(all_companies):
            value = asset_values[i]
            if value > 0:  # 只包含有实际价值的公司
                company_data_with_value.append(
                    {
                        "user_id": company["user_id"],
                        "company_name": company["name"],
                        "asset_value": value,
                    }
                )

        # 4. 按资产价值从高到低排序
        sorted_companies = sorted(
            company_data_with_value, key=lambda x: x["asset_value"], reverse=True
        )

        # 5. 返回排序后的前 limit 名
        return sorted_companies[:limit]

    async def terminate(self):
        """插件被卸载/停用时调用，清理shared_services中的API实例"""
        if shared_services.get("industry_api") == self.api:
            del shared_services["industry_api"]
            logger.info("虚拟产业API (industry_api) 已成功注销。")

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """
        AstrBot 初始化完成后，执行插件的异步初始化流程。
        """
        # 这个钩子现在由 service.py 内部的 initialize() 隐式处理
        # 为确保数据库初始化，我们仍然保留 init_db 调用
        logger.info("正在初始化虚拟产业插件数据库...")
        await data_manager.init_db()
        logger.info("虚拟产业插件数据库初始化完成。")

    # --- 基础指令 (无改动) ---
    @filter.command("开公司", alias={"创建公司"})
    async def create_company_handler(
        self, event: AstrMessageEvent, company_name: str = ""
    ):
        """创建一个属于你的虚拟公司。需要提供公司名称。"""
        if not company_name:
            yield event.plain_result("指令格式不正确哦，请使用：/开公司 [你的公司名]")
            return
        user_id = event.get_sender_id()
        result_msg = await self.service.create_company(user_id, company_name)
        yield event.plain_result(result_msg)

    @filter.command("我的公司", alias={"公司"})
    async def get_profile_handler(self, event: AstrMessageEvent):
        """查看你的公司信息，并结算挂机收益。"""
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()
        result_msg = await self.service.get_company_profile(user_id, user_name)
        yield event.plain_result(result_msg)

    @filter.command("升级公司", alias={"公司升级", "计划升级"})
    async def upgrade_company_handler(self, event: AstrMessageEvent):
        """
        为你的公司升级。
        私有公司会立即升级；上市公司将启动一个有公示期的升级计划。
        """
        user_id = event.get_sender_id()
        # 调用 service 层的新方法
        result_msg = await self.service.handle_upgrade_request(user_id)
        yield event.plain_result(result_msg)

    @filter.command("公司排行", alias={"公司排名"})
    async def get_ranking_handler(self, event: AstrMessageEvent):
        """查看服务器内所有公司的资产排行榜。"""
        result_msg = await self.service.get_company_ranking()
        yield event.plain_result(result_msg)

    @filter.command("公司改名")
    async def rename_company_handler(self, event: AstrMessageEvent, new_name: str = ""):
        """为你的公司更换一个新的名字，需要花费金币。"""
        if not new_name:
            yield event.plain_result(
                "指令格式不正确哦，请使用：/公司改名 [新的公司名]\n改名将消耗金币。"
            )
            return
        user_id = event.get_sender_id()
        result_msg = await self.service.rename_company(user_id, new_name)
        yield event.plain_result(result_msg)

    @filter.command("出售公司", alias={"解散公司"})
    async def dissolve_company_handler(self, event: AstrMessageEvent):
        """解散你的公司，并按其价值的60%回收资金。这是一个不可逆操作。"""
        user_id = event.get_sender_id()
        result_msg = await self.service.dissolve_company(user_id)
        yield event.plain_result(result_msg)

    @filter.command("我的部门", alias={"部门", "查看部门"})
    async def get_department_profile_handler(self, event: AstrMessageEvent):
        """查看你公司所有部门的详细信息和总加成。"""
        result = await self.service.get_department_profile(
            event.get_sender_id(), event.get_sender_name()
        )
        yield event.plain_result(result)

    @filter.command("升级部门", alias={"部门升级"})
    async def upgrade_department_handler(
        self, event: AstrMessageEvent, dept_name: str = ""
    ):
        """提升你指定部门的等级。"""
        # 从原始消息中提取部门名称，以支持带空格的别名
        full_command = event.message_str.strip()
        parts = full_command.split(maxsplit=2)
        final_dept_name = parts[1] if len(parts) > 1 else ""

        if not final_dept_name:
            yield event.plain_result(
                "请指定要升级的部门：\n- `/升级部门 运营部`\n- `/升级部门 研发部`\n- `/升级部门 公关部`\n(您也可以使用自定义的别名)"
            )
            return
        result = await self.service.upgrade_department(
            event.get_sender_id(), final_dept_name
        )
        yield event.plain_result(result)

    @filter.command("部门改名")
    async def set_department_alias_handler(
        self, event: AstrMessageEvent, old_name: str = "", new_alias: str = ""
    ):
        """为你的部门设置一个自定义的别名。"""
        if not old_name or not new_alias:
            yield event.plain_result(
                "指令格式不正确哦，请使用：\n`/部门改名 [原名或别名] [新别名]`"
            )
            return

        result = await self.service.set_department_alias(
            event.get_sender_id(), old_name, new_alias
        )
        yield event.plain_result(result)

    @filter.command("挖角", alias={"挖掘"})
    async def talent_poach_handler(self, event: AstrMessageEvent):
        """对其他玩家的公司发起人才挖角。"""
        target_id = None
        # 优先处理 @
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                target_id = component.qq
                break

        # 如果没有 @，尝试从文本中解析 ID
        if not target_id:
            parts = event.message_str.strip().split()
            if len(parts) > 1 and parts[1].isdigit():
                target_id = parts[1]

        if not target_id:
            yield event.plain_result(
                "请 @ 一位玩家或提供其ID。例如：\n/挖角 @张三\n/挖角 12345678"
            )
            return

        result = await self.service.talent_poach(event.get_sender_id(), target_id)
        yield event.plain_result(result)

    @filter.command("刺探", alias={"商业间谍"})
    async def industrial_espionage_handler(self, event: AstrMessageEvent):
        """对其他玩家的公司发起商业刺探。"""
        target_id = None
        # 优先处理 @
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                target_id = component.qq
                break

        # 如果没有 @，尝试从文本中解析 ID
        if not target_id:
            parts = event.message_str.strip().split()
            if len(parts) > 1 and parts[1].isdigit():
                target_id = parts[1]

        if not target_id:
            yield event.plain_result(
                "请 @ 一位玩家或提供其ID。例如：\n/刺探 @张三\n/刺探 12345678"
            )
            return

        result = await self.service.industrial_espionage(
            event.get_sender_id(), target_id
        )
        yield event.plain_result(result)

    @filter.command("公司上市")
    async def company_ipo_handler(self, event: AstrMessageEvent, ticker: str = ""):
        """让你的公司进行首次公开募股 (IPO)，必须指定一个股票代码。"""
        # +++ 新增：检查玩家是否输入了代码 +++
        if not ticker:
            yield event.plain_result(
                "指令格式错误！\n请使用：/公司上市 [自定义股票代码]\n代码必须是2到5位纯英文字母。"
            )
            return

        user_id = event.get_sender_id()
        # 将玩家输入的ticker传递给service层
        result_msg = await self.service.company_ipo(user_id, custom_ticker=ticker)
        yield event.plain_result(result_msg)

    @filter.command("公司退市")
    async def company_delist_handler(self, event: AstrMessageEvent):
        """将你的上市公司私有化，从股票市场退市。"""
        user_id = event.get_sender_id()
        result_msg = await self.service.company_delist(user_id)
        yield event.plain_result(result_msg)

    @filter.command("公司财报", alias={"财报"})
    async def get_earnings_report_handler(self, event: AstrMessageEvent):
        """作为上市公司董事长，发布本周期的业绩报告以获取分红。"""
        user_id = event.get_sender_id()
        result_msg = await self.service.get_earnings_report(user_id)
        yield event.plain_result(result_msg)

    @filter.command("公司行动")
    async def corporate_action_handler(
        self, event: AstrMessageEvent, *, action_name: str = ""
    ):
        """(上市公司) 执行一项战略投资以影响下次财报。"""
        user_id = event.get_sender_id()
        action_name = action_name.strip()

        if not action_name:
            # 如果没有输入行动，则显示帮助信息
            # +++ 核心修正：指令示例从 {key} 改为 {act['name']} +++
            actions_list = [
                f"- {act['name']} (`/公司行动 {act['name']}`)"
                for key, act in config.CORPORATE_ACTIONS.items()
            ]
            help_msg = "您可以执行以下公司行动来影响下次财报：\n" + "\n".join(
                actions_list
            )
            yield event.plain_result(help_msg)
            return

        # 将输入的中文名映射回内部关键字
        action_keyword = None
        for key, act in config.CORPORATE_ACTIONS.items():
            if act["name"] == action_name:
                action_keyword = key
                break

        # 如果玩家输入的是关键字，也支持 (向下兼容)
        if not action_keyword and action_name in config.CORPORATE_ACTIONS:
            action_keyword = action_name

        if not action_keyword:
            yield event.plain_result(f"找不到名为「{action_name}」的公司行动。")
            return

        result_msg = await self.service.perform_corporate_action(
            user_id, action_keyword
        )
        yield event.plain_result(result_msg)

    @filter.command("公司帮助", alias={"产业帮助"})
    async def company_help_handler(self, event: AstrMessageEvent):
        """获取虚拟产业插件的详细玩法说明。"""
        help_text = (
            "🏢 虚拟产业插件帮助文档 🏢\n"
            "--------------------\n"
            "基础指令\n"
            "`/开公司 [名]` - 创建公司\n"
            "`/我的公司` - 查看公司详情，结算收益\n"
            "`/升级公司` - 提升公司主等级\n"
            "`/公司改名 [新名]` - 修改公司名\n"
            "`/出售公司` - 解散公司并回收60%资金\n"
            "`/公司排行` - 查看价值排行榜\n\n"
            "部门系统 (公司Lv.2解锁)\n"
            "`/我的部门` - 查看所有部门的总览和加成\n"
            "`/升级部门 [部门名]` - 指定升级运营/研发/公关部\n"
            "`/部门改名 [原名/别名] [新别名]` - 自定义部门名称\n\n"
            "商业行动\n"
            "`/挖角 [@玩家]` - 尝试削弱对手，强化自己\n"
            "`/刺探 [@玩家]` - 尝试增加对方下次升级成本\n\n"
            "高级玩法 (公司Lv.5解锁)\n"
            "`/公司上市 [代码]` - 将公司转为上市公司\n"
            "`/公司行动` - (上市公司) 进行投资以提升财报表现\n"
            "`/公司财报` - (上市公司) 发布财报获取分红\n"
            "`/公司退市` - (上市公司) 将公司私有化\n\n"
            "特色玩法\n"
            "查询公司状态时，有几率触发随机事件！"
        )
        yield event.plain_result(help_text)
