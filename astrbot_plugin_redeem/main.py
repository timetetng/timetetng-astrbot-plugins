# 导入所需的库
import asyncio
import os
import json
import time
import uuid
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

# 从 astrbot.api 导入核心模块
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# 导入 FastAPI 和相关模块
try:
    import uvicorn
    from fastapi import FastAPI, Depends, HTTPException, Security, status
    from fastapi.security import APIKeyHeader
    from pydantic import BaseModel, Field, constr
except ImportError:
    logger.error(
        "RedeemCode 插件缺少 fastapi 或 uvicorn 依赖。请创建 requirements.txt 并重启 AstrBot。"
    )
    FastAPI = None  # 设置为 None 以便后续检查

# 共享服务
try:
    from ..common.services import shared_services
except (ImportError, ValueError):
    shared_services = {}


# --- Pydantic 模型定义 (用于 API) ---
class CreateCodeRequest(BaseModel):
    code_type: constr(pattern=r"^(universal|single)$")  # type: ignore
    amount: int = Field(..., gt=0)
    duration: constr(pattern=r"^\d+[dhm]$")  # type: ignore


class CreateCodeResponse(BaseModel):
    status: str = "success"
    code: str
    type: str
    reward_amount: int
    expires_at_str: str
    expires_at_ts: float


# --- 插件核心类 ---
@register(
    "astrbot_plugin_redeem",
    "timetetng一个兑换码插件，支持指令和 Web API 创建兑换码。",
    "2.1.0",
    "https://github.com/your-repo",
)
class RedeemCodePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        if FastAPI is None:
            raise ImportError("FastAPI 或 uvicorn 未安装。")

        self.config = config
        self.economy_api: Optional[Any] = None
        self.uvicorn_server: Optional[uvicorn.Server] = None
        self.app: Optional[FastAPI] = None

        data_root_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        self.data_dir = os.path.join(data_root_dir, "redeem_code")
        self.codes_file = os.path.join(self.data_dir, "codes.json")
        self.usage_file = os.path.join(self.data_dir, "usage_records.json")

        self.codes: Dict[str, Any] = {}
        self.usage_records: Dict[str, Any] = {}
        self._setup()

        if self.config.get("enable_api"):
            logger.info("配置检测到 API 已启用，准备启动 Web 服务...")
            self.setup_api_routes()
            asyncio.create_task(self._start_web_server())
        else:
            logger.info("Web API 服务未在配置中启用。")

        asyncio.create_task(self.initialize_apis())

    def _setup(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self._load_data()

    def _load_data(self):
        try:
            self.codes = (
                json.load(open(self.codes_file, "r", encoding="utf-8"))
                if os.path.exists(self.codes_file)
                else {}
            )
            self.usage_records = (
                json.load(open(self.usage_file, "r", encoding="utf-8"))
                if os.path.exists(self.usage_file)
                else {"universal_codes": {}, "single_use_codes": {}}
            )
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载兑换码数据失败: {e}")

    def _save_codes(self):
        try:
            with open(self.codes_file, "w", encoding="utf-8") as f:
                json.dump(self.codes, f, indent=4, ensure_ascii=False)
        except IOError as e:
            logger.error(f"保存兑换码失败: {e}")

    def _save_usage(self):
        try:
            with open(self.usage_file, "w", encoding="utf-8") as f:
                json.dump(self.usage_records, f, indent=4, ensure_ascii=False)
        except IOError as e:
            logger.error(f"保存使用记录失败: {e}")

    # --- 核心逻辑 (指令和 API 共用) ---
    def _parse_duration(self, duration_str: str) -> Optional[timedelta]:
        match = re.match(r"(\d+)([dhm])", duration_str.lower())
        if not match:
            return None
        value, unit = int(match.group(1)), match.group(2)
        if unit == "d":
            return timedelta(days=value)
        if unit == "h":
            return timedelta(hours=value)
        if unit == "m":
            return timedelta(minutes=value)
        return None

    def _generate_code_data(
        self, code_type: str, amount: int, duration: str
    ) -> Optional[Dict[str, Any]]:
        time_delta = self._parse_duration(duration)
        if not time_delta:
            return None

        while True:
            new_code = uuid.uuid4().hex[:12].upper()
            if new_code not in self.codes:
                break

        created_at = datetime.now().timestamp()
        expires_at = (datetime.now() + time_delta).timestamp()

        code_data = {
            "code": new_code,
            "type": code_type,
            "reward_type": "coins",
            "reward_amount": amount,
            "created_at": created_at,
            "expires_at": expires_at,
        }
        return code_data

    # --- API 相关方法 ---
    async def terminate(self):
        if self.uvicorn_server and self.uvicorn_server.started:
            logger.info("正在关闭兑換码 Web API 服务...")
            self.uvicorn_server.should_exit = True

    def setup_api_routes(self):
        self.app = FastAPI(title="AstrBot Redeem Code API", version="1.0.0")
        api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

        async def get_api_key(api_key: str = Security(api_key_header)):
            if self.config.get("api_key") and api_key == self.config["api_key"]:
                return api_key
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API Key",
            )

        @self.app.post("/api/redeem/create", response_model=CreateCodeResponse)
        async def api_create_code(
            request: CreateCodeRequest, api_key: str = Depends(get_api_key)
        ):
            code_data = self._generate_code_data(
                request.code_type, request.amount, request.duration
            )
            if not code_data:
                raise HTTPException(status_code=400, detail="Invalid duration format")

            code = code_data.pop("code")
            self.codes[code] = code_data
            self._save_codes()

            return CreateCodeResponse(
                code=code,
                type=code_data["type"],
                reward_amount=code_data["reward_amount"],
                expires_at_str=datetime.fromtimestamp(code_data["expires_at"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                expires_at_ts=code_data["expires_at"],
            )

    async def _start_web_server(self):
        if not self.app:
            return
        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 9090)
        config = uvicorn.Config(self.app, host=host, port=port, log_level="info")
        self.uvicorn_server = uvicorn.Server(config)
        logger.info(f"兑换码 Web API 服务即将启动于 http://{host}:{port}")
        try:
            await self.uvicorn_server.serve()
        except asyncio.CancelledError:
            logger.info("Web API 服务任务被取消。")
        except Exception as e:
            logger.error(f"Web API 服务启动失败: {e}")

    # --- 指令相关方法 ---
    async def initialize_apis(self):
        self.economy_api = await self.wait_for_api("economy_api")

    async def wait_for_api(self, api_name: str, timeout: int = 30):
        logger.info(f"正在等待 {api_name} 加载...")
        start_time = asyncio.get_event_loop().time()
        while True:
            api_instance = shared_services.get(api_name)
            if api_instance:
                logger.info(f"{api_name} 已成功加载。")
                return api_instance
            if asyncio.get_event_loop().time() - start_time > timeout:
                logger.warning(f"等待 {api_name} 超时，依赖此API的功能将受限！")
                return None
            await asyncio.sleep(1)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("createcode", alias={"创建兑换码"})
    async def create_code(
        self, event: AstrMessageEvent, code_type: str, amount: int, duration: str
    ):
        if code_type not in ["universal", "single"]:
            yield event.plain_result(
                "错误：类型必须是 'universal' (通用码) 或 'single' (一次性码)。"
            )
            return
        if amount <= 0:
            yield event.plain_result("错误：奖励金币数量必须是正整数。")
            return

        code_data = self._generate_code_data(code_type, amount, duration)
        if not code_data:
            yield event.plain_result(
                "错误：无效的有效期格式。请使用如 '7d', '24h', '30m' 的格式。"
            )
            return

        code = code_data.pop("code")
        self.codes[code] = code_data
        self._save_codes()

        expire_time_str = datetime.fromtimestamp(code_data["expires_at"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        type_str = (
            "通用码 (每人可用一次)"
            if code_data["type"] == "universal"
            else "一次性码 (仅限一人使用)"
        )
        reply_msg = (
            f"✅ 兑换码创建成功！\n"
            f"码: {code}\n"
            f"类型: {type_str}\n"
            f"奖励: {amount} 金币\n"
            f"有效期至: {expire_time_str}"
        )
        yield event.plain_result(reply_msg)

    @filter.command("redeem", alias={"兑换码"})
    async def redeem_code(self, event: AstrMessageEvent, code: str):
        user_id = event.get_sender_id()
        code = code.upper()

        if not self.economy_api:
            yield event.plain_result("抱歉，奖励系统当前不可用，请联系管理员。")
            return

        code_data = self.codes.get(code)
        if not code_data:
            yield event.plain_result("❌ 无效的兑换码。")
            return

        if time.time() > code_data["expires_at"]:
            yield event.plain_result("⌛️ 此兑换码已过期。")
            return

        code_type = code_data["type"]
        if code_type == "single":
            if code in self.usage_records["single_use_codes"]:
                yield event.plain_result("❌ 此兑换码已被使用。")
                return
        elif code_type == "universal":
            if code in self.usage_records.get(
                "universal_codes", {}
            ) and user_id in self.usage_records["universal_codes"].get(code, []):
                yield event.plain_result("❌ 您已经兑换过此奖励。")
                return

        reward_amount = code_data["reward_amount"]
        try:
            success = await self.economy_api.add_coins(
                user_id=user_id, amount=reward_amount, reason=f"兑换码: {code}"
            )
            if not success:
                raise Exception("EconomyAPI add_coins returned False")
        except Exception as e:
            logger.error(f"为用户 {user_id} 发放兑换码 {code} 奖励失败: {e}")
            yield event.plain_result("服务器内部错误，奖励发放失败，请联系管理员。")
            return

        if code_type == "single":
            self.usage_records.setdefault("single_use_codes", {})[code] = {
                "user_id": user_id,
                "timestamp": time.time(),
            }
        elif code_type == "universal":
            self.usage_records.setdefault("universal_codes", {}).setdefault(
                code, []
            ).append(user_id)
        self._save_usage()

        yield event.plain_result(f"🎉 兑换成功！您已获得 {reward_amount} 金币！")
