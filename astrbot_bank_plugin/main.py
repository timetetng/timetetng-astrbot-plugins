# main.py
import asyncio
import aiosqlite
import os
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from ..common.services import shared_services


# --- 银行插件对外暴露的API ---
class BankAPI:
    """
    银行插件对外暴露的API。
    其他插件可以通过 shared_services.get("bank_api") 获取。
    """

    def __init__(self, plugin_instance: "BankPlugin"):
        self._plugin = plugin_instance

    async def get_balance(self, user_id: str) -> float:
        """获取用户的银行活期存款余额。"""
        return await self._plugin.db_get_balance(user_id)

    async def get_bank_asset_value(self, user_id: str) -> float:
        """
        获取用户在银行的总资产价值（活期+定期）。
        此方法用于被总资产统计类插件（如股市插件）调用。
        """
        balance = await self.get_balance(user_id)
        fixed_deposits = await self._plugin.db_get_all_fixed_deposits(user_id)
        total_fixed_amount = sum(d["principal"] for d in fixed_deposits)
        return balance + total_fixed_amount

    async def has_loan(self, user_id: str) -> bool:
        """检查用户是否有未偿还的贷款。"""
        loan_info = await self._plugin.db_get_loan(user_id)
        return loan_info is not None

    async def get_loan_info(self, user_id: str) -> dict | None:
        """
        获取用户的贷款详情。
        返回: 包含 'principal', 'amount_due' 等键的字典，或 None。
        """
        return await self._plugin.db_get_loan(user_id)

    async def get_top_accounts(self, limit: int = 10) -> list[dict]:
        """
        获取银行总资产（活期+定期）排行榜。
        """
        # 获取所有在银行有资产的用户
        all_users = await self._plugin.db_get_all_bank_users()

        # 使用 asyncio.gather 并行计算所有用户的总资产
        tasks = [self.get_bank_asset_value(user_id) for user_id in all_users]
        all_assets = await asyncio.gather(*tasks)

        # 将用户ID和他们的总资产配对
        user_assets = [
            {"user_id": user_id, "total_asset": asset}
            for user_id, asset in zip(all_users, all_assets)
            if asset > 0
        ]

        # 按总资产降序排序
        sorted_user_assets = sorted(
            user_assets, key=lambda x: x["total_asset"], reverse=True
        )

        # 返回前 limit 个结果，并修改键名为 "balance" 以兼容旧接口
        top_users = sorted_user_assets[:limit]
        return [
            {"user_id": user["user_id"], "balance": user["total_asset"]}
            for user in top_users
        ]


@register(
    "bank",
    "Gemini & YourName",
    "一个功能丰富的银行插件，支持定期存款和成就系统",
    "2.0.0",
    "https://github.com/AstrBotDevs/AstrBot",
)
class BankPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.db_path = os.path.join(os.path.dirname(__file__), "bank.db")

        self.economy_api = None
        self.industry_api = None
        self.achievement_api = None  # 成就API

        self.bank_api_instance = BankAPI(self)
        self.interest_task = None

        shared_services["bank_api"] = self.bank_api_instance
        logger.info("银行插件API (bank_api) 已立即注册。")

        asyncio.create_task(self.initialize_and_run_task())

    async def initialize_and_run_task(self):
        """异步初始化插件，包含等待依赖API的逻辑。"""
        await self.init_database()
        logger.info("银行插件：正在后台等待依赖API加载...")

        # 并行等待多个API
        self.economy_api = await self.wait_for_api("economy_api")
        if not self.economy_api:
            logger.error("银行插件：等待经济系统API超时！插件核心功能将无法使用！")
            return
        logger.info("银行插件：经济系统API (economy_api) 已成功加载。")

        self.industry_api = await self.wait_for_api(
            "industry_api", timeout=10
        )  # 贷款不是核心，等待时间短点
        if self.industry_api:
            logger.info("银行插件：虚拟产业API (industry_api) 已成功加载。")
        else:
            logger.warning("银行插件：未能获取虚拟产业API，贷款功能将受限。")

        # 等待成就API
        self.achievement_api = await self.wait_for_api("achievement_api", timeout=15)
        if self.achievement_api:
            logger.info("银行插件：成就系统API (achievement_api) 已成功加载。")
        else:
            logger.warning("银行插件：未能获取成就系统API，成就将无法触发。")

        self.interest_task = asyncio.create_task(self.interest_calculation_task())
        logger.info("银行插件初始化完成，后台任务已启动。")

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

    async def terminate(self):
        """插件卸载/停用时调用"""
        if self.interest_task and not self.interest_task.done():
            self.interest_task.cancel()
        shared_services.pop("bank_api", None)
        logger.info("银行插件已卸载，API已注销。")

    # --- 数据库操作 ---
    async def init_database(self):
        async with aiosqlite.connect(self.db_path) as db:
            # 活期账户表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    user_id TEXT PRIMARY KEY,
                    balance REAL NOT NULL DEFAULT 0,
                    total_interest_earned REAL NOT NULL DEFAULT 0
                )
            """)
            # 贷款表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS loans (
                    user_id TEXT PRIMARY KEY,
                    principal REAL NOT NULL,
                    amount_due REAL NOT NULL,
                    interest_rate REAL NOT NULL,
                    loan_date TEXT NOT NULL
                )
            """)
            # 定期存款表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS fixed_deposits (
                    deposit_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    principal REAL NOT NULL,
                    interest_rate REAL NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL
                )
            """)

            try:
                await db.execute(
                    "ALTER TABLE accounts ADD COLUMN total_interest_earned REAL NOT NULL DEFAULT 0"
                )
            except aiosqlite.OperationalError as e:
                if "duplicate column name" in str(e):
                    pass
                else:
                    raise e

            await db.commit()

    async def db_get_all_bank_users(self) -> set[str]:
        """获取所有在银行有资产（活期或定期）的用户ID集合。"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor_accounts = await db.execute(
                "SELECT user_id FROM accounts WHERE balance > 0"
            )
            users_from_accounts = {row[0] for row in await cursor_accounts.fetchall()}

            cursor_fixed = await db.execute("SELECT user_id FROM fixed_deposits")
            users_from_fixed = {row[0] for row in await cursor_fixed.fetchall()}

            return users_from_accounts.union(users_from_fixed)

    async def db_get_balance(self, user_id: str) -> float:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT balance FROM accounts WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return round(row[0], 2) if row else 0.0

    async def db_get_account_info(self, user_id: str) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT balance, total_interest_earned FROM accounts WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "balance": round(row[0], 2),
                    "total_interest_earned": round(row[1], 2),
                }
            return {"balance": 0.0, "total_interest_earned": 0.0}

    async def db_update_balance(self, user_id: str, amount_change: float) -> float:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO accounts (user_id) VALUES (?)", (user_id,)
            )
            await db.execute(
                "UPDATE accounts SET balance = balance + ? WHERE user_id = ?",
                (amount_change, user_id),
            )
            await db.commit()
            return await self.db_get_balance(user_id)

    async def db_get_loan(self, user_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT principal, amount_due, interest_rate, loan_date FROM loans WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "principal": row[0],
                    "amount_due": round(row[1], 2),
                    "interest_rate": row[2],
                    "loan_date": row[3],
                }
            return None

    async def db_add_fixed_deposit(
        self, user_id: str, amount: float, weeks: int
    ) -> str:
        deposit_id = os.urandom(4).hex()
        start_date = datetime.now()
        end_date = start_date + timedelta(weeks=weeks)
        interest_rate = (
            self.config.savings_interest_rate
            * self.config.fixed_deposit_interest_multiplier
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO fixed_deposits (deposit_id, user_id, principal, interest_rate, start_date, end_date) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    deposit_id,
                    user_id,
                    amount,
                    interest_rate,
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            )
            await db.commit()
        return deposit_id

    async def db_get_fixed_deposit(self, deposit_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM fixed_deposits WHERE deposit_id = ?", (deposit_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "deposit_id": row[0],
                "user_id": row[1],
                "principal": row[2],
                "interest_rate": row[3],
                "start_date": row[4],
                "end_date": row[5],
            }

    async def db_get_all_fixed_deposits(self, user_id: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT deposit_id, principal, end_date FROM fixed_deposits WHERE user_id = ? ORDER BY end_date",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [
                {"deposit_id": r[0], "principal": r[1], "end_date": r[2]} for r in rows
            ]

    async def db_delete_fixed_deposit(self, deposit_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM fixed_deposits WHERE deposit_id = ?", (deposit_id,)
            )
            await db.commit()

    # --- 指令处理 ---

    @filter.command("银行帮助", alias={"bankhelp"})
    async def bank_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "🏦 银行指令帮助 🏦\n"
            "--------------------\n"
            "【账户操作】\n"
            "/银行信息 - 查看您的现金、存款及贷款。\n"
            "/存款 [金额] - 将现金存入银行 (活期)。\n"
            "/取款 [金额] - 从银行活期账户取款。\n"
            "/全部取出 - 将银行活期存款全部提现。\n"
            "\n"
            "【定期存款】(利息更高!)\n"
            "/定期存款 [金额] [周数] - 存入一笔定期，到期前无法取出。\n"
            "/查询定期 - 查看你所有的定期存款。\n"
            "/取出定期 [存款ID] - 取出已到期的定期存款本息。\n"
            "\n"
            "【贷款服务】\n"
            "/贷款信息 - 查看当前贷款详情或规则。\n"
            "/贷款 [金额] - 抵押资产申请贷款。\n"
            "/还款 [金额] - 偿还贷款。\n"
            "/全部还款 - 一次性还清所有贷款。\n"
            "--------------------\n"
        )

    @filter.command("银行信息", alias={"银行"})
    async def check_balance(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        coins = await self.economy_api.get_coins(user_id)
        account_info = await self.db_get_account_info(user_id)
        bank_balance = account_info["balance"]
        interest_earned = account_info["total_interest_earned"]

        fixed_deposits = await self.db_get_all_fixed_deposits(user_id)
        total_fixed_amount = sum(d["principal"] for d in fixed_deposits)

        msg = f"👤 {user_name} 的财务报告:\n"
        msg += f"💰 现金: {coins:,.2f} 金币\n"
        msg += f"💳 活期存款: {bank_balance:,.2f} 金币"
        if interest_earned > 0:
            msg += f" (已获利息: {interest_earned:,.2f} 金币)"

        if total_fixed_amount > 0:
            msg += f"\n📦 定期存款总额: {total_fixed_amount:,.2f} 金币 ({len(fixed_deposits)}笔)"

        loan_info = await self.db_get_loan(user_id)
        if loan_info:
            msg += "\n\n🚨 负债信息:\n"
            msg += f"   - 待还贷款: {loan_info['amount_due']:,.2f} 金币"

        msg += "\n\n💡 发送 /银行帮助 查看所有指令。"
        yield event.plain_result(msg)

    @filter.command("存款", alias={"存入"})
    async def deposit(self, event: AstrMessageEvent, amount: int):
        user_id = event.get_sender_id()

        if amount <= 0:
            yield event.plain_result("存款金额必须是正数！")
            return

        current_coins = await self.economy_api.get_coins(user_id)
        if current_coins < amount:
            yield event.plain_result(
                f"您的现金不足！当前现金: {current_coins:,.2f} 金币。"
            )
            return

        is_first_deposit = (await self.db_get_balance(user_id)) == 0 and (
            len(await self.db_get_all_fixed_deposits(user_id)) == 0
        )

        success = await self.economy_api.add_coins(user_id, -amount, "银行存款")
        if success:
            new_balance = await self.db_update_balance(user_id, amount)

            if self.achievement_api and is_first_deposit:
                await self.achievement_api.unlock_achievement(
                    user_id, "bank_first_deposit", event=event
                )
                logger.info(f"用户 {user_id} 完成了第一笔存款，触发成就。")

            yield event.plain_result(
                f"✅ 存款成功！\n存入: {amount:,.2f} 金币\n当前活期余额: {new_balance:,.2f} 金币。"
            )
        else:
            yield event.plain_result("存款失败，请稍后再试。")

    @filter.command("定期存款")
    async def fixed_deposit(self, event: AstrMessageEvent, amount: int, weeks: int):
        user_id = event.get_sender_id()

        if amount <= 0 or weeks <= 0:
            yield event.plain_result("金额和周数都必须是正数！")
            return

        max_weeks = self.config.get("fixed_deposit_max_weeks", 52)
        if weeks > max_weeks:
            yield event.plain_result(f"定期存款最长不能超过 {max_weeks} 周。")
            return

        current_coins = await self.economy_api.get_coins(user_id)
        if current_coins < amount:
            yield event.plain_result(f"您的现金不足以存入 {amount:,.2f} 金币。")
            return

        is_first_deposit = (await self.db_get_balance(user_id)) == 0 and (
            len(await self.db_get_all_fixed_deposits(user_id)) == 0
        )

        success = await self.economy_api.add_coins(user_id, -amount, "银行定期存款")
        if not success:
            yield event.plain_result("定期存款失败，现金扣除时发生错误。")
            return

        deposit_id = await self.db_add_fixed_deposit(user_id, amount, weeks)

        if self.achievement_api and is_first_deposit:
            await self.achievement_api.unlock_achievement(
                user_id, "bank_first_deposit", event=event
            )
            logger.info(f"用户 {user_id} 完成了第一笔定期存款，触发成就。")

        end_date_str = (datetime.now() + timedelta(weeks=weeks)).strftime("%Y-%m-%d")
        yield event.plain_result(
            f"✅ 定期存款成功！\n"
            f" - 金额: {amount:,.2f} 金币\n"
            f" - 期限: {weeks} 周\n"
            f" - 到期日: {end_date_str}\n"
            f" - 存款ID: `{deposit_id}` (取出时需要)"
        )

    @filter.command("查询定期")
    async def check_fixed_deposits(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        deposits = await self.db_get_all_fixed_deposits(user_id)
        if not deposits:
            yield event.plain_result("您当前没有任何定期存款。")
            return

        msg = "🗓️ 您的定期存款列表:\n"
        now = datetime.now()
        for d in deposits:
            end_date = datetime.fromisoformat(d["end_date"])
            status = "已到期" if now >= end_date else "计息中"
            msg += f" - ID: `{d['deposit_id']}` | 金额: {d['principal']:,.2f} | 到期日: {end_date.strftime('%Y-%m-%d')} ({status})\n"
        msg += "\n使用 /取出定期 [存款ID] 来取出到期的存款。"
        yield event.plain_result(msg)

    @filter.command("取出定期")
    async def withdraw_fixed_deposit(self, event: AstrMessageEvent, deposit_id: str):
        user_id = event.get_sender_id()
        deposit_info = await self.db_get_fixed_deposit(deposit_id)

        if not deposit_info or deposit_info["user_id"] != user_id:
            yield event.plain_result("未找到该笔定期存款，或该存款不属于您。")
            return

        start_date = datetime.fromisoformat(deposit_info["start_date"])
        end_date = datetime.fromisoformat(deposit_info["end_date"])

        if datetime.now() < end_date:
            yield event.plain_result(
                f"该笔存款尚未到期（到期日: {end_date.strftime('%Y-%m-%d')}），无法取出。"
            )
            return

        principal = deposit_info["principal"]
        rate = deposit_info["interest_rate"]
        days = (end_date - start_date).days

        final_amount = round(principal * ((1 + rate) ** days), 2)
        interest_earned = round(final_amount - principal, 2)

        await self.db_delete_fixed_deposit(deposit_id)
        await self.economy_api.add_coins(
            user_id, final_amount, f"取出定期存款{deposit_id}"
        )

        if self.achievement_api and (end_date - start_date) >= timedelta(days=6.9):
            await self.achievement_api.unlock_achievement(
                user_id, "bank_fixed_deposit_success", event=event
            )
            logger.info(f"用户 {user_id} 完成了一笔长于一周的定期存款，触发成就。")

        yield event.plain_result(
            f"✅ 定期存款取出成功！\n"
            f" - 本金: {principal:,.2f} 金币\n"
            f" - 利息: {interest_earned:,.2f} 金币\n"
            f" - 总计到账: {final_amount:,.2f} 金币"
        )

    @filter.command("取款", alias={"取出"})
    async def withdraw(self, event: AstrMessageEvent, amount: int):
        """从银行取出为现金"""
        if not self.economy_api:
            yield event.plain_result("错误：经济系统未加载，无法取款。")
            return

        if amount <= 0:
            yield event.plain_result("取款金额必须是正数！")
            return

        user_id = event.get_sender_id()
        current_balance = await self.db_get_balance(user_id)

        # 新增：计算手续费
        fee = round(amount * self.config.withdrawal_fee_rate, 2)
        total_deduction = amount + fee

        if current_balance < total_deduction:
            yield event.plain_result(
                f"您的银行存款不足！\n"
                f"取款 {amount} 金币需支付手续费 {fee} 金币，共需 {total_deduction} 金币。\n"
                f"您当前存款: {current_balance} 金币。"
            )
            return

        await self.db_update_balance(user_id, -total_deduction)
        await self.economy_api.add_coins(user_id, amount, "银行取款")

        new_balance = current_balance - total_deduction
        yield event.plain_result(
            f"✅ 取款成功！\n"
            f"取出: {amount} 金币\n"
            f"手续费: {fee} 金币\n"
            f"当前银行余额: {round(new_balance, 2)} 金币。"
        )

    @filter.command("全部取出", alias={"全部提现"})
    async def withdraw_all(self, event: AstrMessageEvent):
        """将银行全部存款提现（自动扣除手续费）"""
        if not self.economy_api:
            yield event.plain_result("错误：经济系统未加载，无法取款。")
            return

        user_id = event.get_sender_id()
        current_balance = await self.db_get_balance(user_id)

        if current_balance <= 0:
            yield event.plain_result("您的银行账户没有存款可供取出。")
            return

        # 设到手金额为 A, 手续费率为 R, 银行余额为 B
        # A + A*R = B  =>  A * (1+R) = B  =>  A = B / (1+R)
        rate = self.config.withdrawal_fee_rate
        amount_to_receive = round(current_balance / (1 + rate), 2)
        fee = round(current_balance - amount_to_receive, 2)

        # 从银行扣除全部余额
        await self.db_update_balance(user_id, -current_balance)
        # 将计算后的金额发放到现金
        await self.economy_api.add_coins(user_id, amount_to_receive, "银行全部取出")

        yield event.plain_result(
            f"✅ 全部取出成功！\n"
            f"从银行账户提出总额: {current_balance} 金币\n"
            f"手续费 ({self.config.withdrawal_fee_rate * 100:.2f}%): {fee} 金币\n"
            f"实际到账现金: {amount_to_receive} 金币\n"
            f"您的银行余额现为 0 金币。"
        )

    @filter.command("贷款信息", alias={"查看贷款"})
    async def loan_info(self, event: AstrMessageEvent):
        """贷款相关信息"""
        if not self.industry_api:
            yield event.plain_result(
                "抱歉，由于未安装虚拟产业插件，本银行暂不提供贷款服务。"
            )
            return

        loan_info = await self.db_get_loan(event.get_sender_id())
        if loan_info:
            yield event.plain_result(
                f"📋 您当前的贷款信息:\n"
                f" - 原始本金: {loan_info['principal']} 金币\n"
                f" - 当前应还总额: {loan_info['amount_due']} 金币\n"
                f" - 贷款日期: {loan_info['loan_date']}\n"
                f"请使用 /还款 [金额] 来偿还贷款。"
            )
        else:
            yield event.plain_result(
                "💡 贷款服务说明:\n"
                "本行根据您在虚拟产业中的固定资产（如公司价值）进行评估，为您提供贷款。\n"
                f"最大贷款额度 = 固定资产价值 × {self.config.loan_to_value_ratio * 100:.0f}%\n"
                f"贷款将以 {self.config.loan_interest_rate * 100:.2f}% 的日利率计息。\n"
                f"申请贷款时，会预先扣除 {self.config.loan_origination_fee_rate * 100:.0f}% 的手续费。\n"
                "发送 /贷款 [金额] 来申请贷款。"
            )

    @filter.command("贷款", alias={"申请贷款"})
    async def apply_loan(self, event: AstrMessageEvent, amount: int):
        """申请一笔贷款"""
        if not self.industry_api:
            yield event.plain_result("错误：虚拟产业插件未加载，无法评估您的资产。")
            return

        user_id = event.get_sender_id()
        if await self.db_get_loan(user_id):
            yield event.plain_result("您已经有一笔尚未还清的贷款，请先还清再申请！")
            return

        if amount <= 0:
            yield event.plain_result("贷款金额必须为正数！")
            return

        try:
            company_asset = await self.industry_api.get_company_asset_value(user_id)
        except Exception as e:
            logger.error(f"调用 industry_api.get_company_asset_value 失败: {e}")
            yield event.plain_result("查询您的固定资产失败，请稍后再试。")
            return

        if company_asset <= 0:
            yield event.plain_result("您没有任何固定资产，无法申请贷款。")
            return

        max_loan_amount = company_asset * self.config.loan_to_value_ratio
        if amount > max_loan_amount:
            yield event.plain_result(
                f"您的资产最多只能贷款 {int(max_loan_amount)} 金币，无法申请 {amount} 金币。"
            )
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO loans (user_id, principal, amount_due, interest_rate, loan_date) VALUES (?, ?, ?, ?, ?)",
                (
                    user_id,
                    amount,
                    amount,
                    self.config.loan_interest_rate,
                    datetime.now().strftime("%Y-%m-%d"),
                ),
            )
            await db.commit()

        # 新增：计算并扣除手续费
        fee = round(amount * self.config.loan_origination_fee_rate, 2)
        net_amount = amount - fee
        await self.economy_api.add_coins(user_id, net_amount, "银行贷款发放")

        yield event.plain_result(
            f"🎉 贷款申请已批准！\n"
            f"贷款金额: {amount} 金币\n"
            f"手续费 ({self.config.loan_origination_fee_rate * 100:.0f}%): {fee} 金币\n"
            f"实际到账: {net_amount} 金币\n"
            f"请记得按时还款，日利率为 {self.config.loan_interest_rate * 100:.2f}%。"
        )

    @filter.command("还款", alias={"还贷"})
    async def repay_loan(self, event: AstrMessageEvent, amount: int):
        """偿还部分或全部贷款"""
        if not self.economy_api:
            yield event.plain_result("错误：经济系统未加载，无法还款。")
            return

        if amount <= 0:
            yield event.plain_result("还款金额必须为正数！")
            return

        user_id = event.get_sender_id()
        loan_info = await self.db_get_loan(user_id)
        if not loan_info:
            yield event.plain_result("您当前没有需要偿还的贷款。")
            return

        current_coins = await self.economy_api.get_coins(user_id)
        if current_coins < amount:
            yield event.plain_result(f"您的现金不足以支付 {amount} 金币的还款！")
            return

        success = await self.economy_api.add_coins(user_id, -amount, "偿还银行贷款")
        if not success:
            yield event.plain_result("还款失败，现金扣除时发生错误。")
            return

        amount_due = loan_info["amount_due"]
        repay_amount = min(amount, amount_due)

        new_amount_due = amount_due - repay_amount

        async with aiosqlite.connect(self.db_path) as db:
            if new_amount_due <= 0.01:
                await db.execute("DELETE FROM loans WHERE user_id = ?", (user_id,))
                yield event.plain_result("🎉 恭喜您！您已成功还清所有贷款！")
            else:
                await db.execute(
                    "UPDATE loans SET amount_due = ? WHERE user_id = ?",
                    (new_amount_due, user_id),
                )
                yield event.plain_result(
                    f"✅ 还款成功！\n本次还款: {repay_amount} 金币\n剩余应还: {round(new_amount_due, 2)} 金币。"
                )
            await db.commit()

    @filter.command("全部还款", alias={"还清贷款"})
    async def repay_all_loan(self, event: AstrMessageEvent):
        """一次性还清所有贷款"""
        if not self.economy_api:
            yield event.plain_result("错误：经济系统未加载，无法还款。")
            return

        user_id = event.get_sender_id()
        loan_info = await self.db_get_loan(user_id)
        if not loan_info:
            yield event.plain_result("您当前没有需要偿还的贷款。")
            return

        amount_to_repay = loan_info["amount_due"]
        current_coins = await self.economy_api.get_coins(user_id)

        if current_coins < amount_to_repay:
            yield event.plain_result(
                f"您的现金不足以还清全部贷款！\n需要: {amount_to_repay} 金币\n持有: {current_coins} 金币"
            )
            return

        success = await self.economy_api.add_coins(
            user_id, -amount_to_repay, "还清银行贷款"
        )
        if not success:
            yield event.plain_result("还款失败，现金扣除时发生错误。")
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM loans WHERE user_id = ?", (user_id,))
            await db.commit()

        yield event.plain_result(
            f"🎉 恭喜您！您已成功使用 {amount_to_repay} 金币还清所有贷款！"
        )

    # --- 后台任务 ---
    async def interest_calculation_task(self):
        """每日定时计算并结算所有账户和贷款的利息。"""
        while True:
            now = datetime.now()
            target_time = now.replace(
                hour=self.config.interest_calculation_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if now > target_time:
                target_time += timedelta(days=1)

            sleep_seconds = (target_time - now).total_seconds()
            logger.info(
                f"银行插件：下一次利息结算在 {target_time}, 等待 {sleep_seconds:.0f} 秒。"
            )
            await asyncio.sleep(sleep_seconds)

            logger.info("银行插件：开始执行每日利息结算...")
            async with aiosqlite.connect(self.db_path) as db:
                # 结算活期利息
                savings_rate = self.config.savings_interest_rate
                await db.execute(
                    """
                    UPDATE accounts
                    SET
                        total_interest_earned = total_interest_earned + (balance * ?),
                        balance = balance * (1 + ?)
                    WHERE balance > 0
                """,
                    (savings_rate, savings_rate),
                )

                # 结算贷款利息
                loan_rate = self.config.loan_interest_rate
                await db.execute(
                    "UPDATE loans SET amount_due = amount_due * (1 + ?)", (loan_rate,)
                )

                # 检查逾期贷款并触发成就
                if self.achievement_api:
                    cursor = await db.execute("SELECT user_id, loan_date FROM loans")
                    overdue_loans = await cursor.fetchall()
                    for user_id, loan_date_str in overdue_loans:
                        loan_date = datetime.fromisoformat(
                            loan_date_str.split(" ")[0]
                        )  # 兼容旧格式
                        if (datetime.now() - loan_date) > timedelta(days=3):
                            # 使用静默解锁，避免半夜打扰用户
                            await self.achievement_api.unlock_achievement(
                                user_id, "bank_loan_overdue_3_days", event=event
                            )
                            logger.info(
                                f"用户 {user_id} 贷款逾期超过3天，尝试静默触发成就。"
                            )

                await db.commit()
            logger.info("银行插件：每日利息结算完成。")
