import aiohttp
import json
import asyncio
import urllib.parse
from pathlib import Path
from typing import List, Dict, Tuple

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, AstrBotConfig


@register("astrbot_plugin_mihomo", "timetetng", "Mihomo内核管理", "1.0.2", "")
class MihomoPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.api_url = self.config.get("api_url", "http://127.0.0.1:9090").rstrip("/")
        self.api_secret = self.config.get("api_secret", "")
        self.headers = {"Content-Type": "application/json"}
        if self.api_secret:
            self.headers["Authorization"] = f"Bearer {self.api_secret}"

        # 缓存
        self.selection_cache = {}

        self.data_dir = Path(StarTools.get_data_dir("astrbot_plugin_mihomo"))
        self.data_file = self.data_dir / "data.json"

        # 加载数据
        self.data = self._load_data()

        # 启动后台监控任务
        self.monitor_task = asyncio.create_task(self._monitor_loop())

    # ================= 数据持久化 =================

    def _load_data(self) -> dict:
        if not self.data_file.exists():
            return {"custom_groups": {}, "auto_tasks": {}}
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[Mihomo] Load data failed: {e}")
            return {"custom_groups": {}, "auto_tasks": {}}

    def _save_data(self):
        try:
            # 确保目录存在
            if not self.data_dir.exists():
                self.data_dir.mkdir(parents=True, exist_ok=True)

            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Mihomo] Save data failed: {e}")

    # ================= 核心工具 =================

    async def _request(self, method: str, path: str, data: dict = None, timeout=5):
        url = f"{self.api_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url, json=data, headers=self.headers, timeout=timeout
                ) as resp:
                    if resp.status == 204:
                        return True
                    if resp.status >= 400:
                        return {
                            "error": f"HTTP {resp.status}",
                            "detail": await resp.text(),
                        }
                    return await resp.json()
        except Exception as e:
            return {"error": "Error", "detail": str(e)}

    def _parse_delay(self, history: List[Dict]) -> Tuple[int, str]:
        if not history:
            return 99999, "N/A"
        delay = history[-1].get("delay", 0)
        if delay == 0:
            return 88888, "Timeout"
        return delay, f"{delay}ms"

    async def _get_smart_group(self) -> Tuple[str, str]:
        """获取主策略组名称"""
        data = await self._request("GET", "/proxies")
        if isinstance(data, dict) and "error" in data:
            return None, "API连接失败"

        selectors = [
            k
            for k, v in data.get("proxies", {}).items()
            if v.get("type") == "Selector" and k not in ["GLOBAL", "REJECT", "PASS"]
        ]

        if not selectors:
            return None, "无策略组"
        # 优先匹配
        for kw in ["机场", "Proxy", "节点", "Select"]:
            for s in selectors:
                if kw in s:
                    return s, None
        return selectors[0], None

    async def _resolve_nodes(self, target: str) -> List[str]:
        """解析目标（关键词或自定义组）为具体的节点名称列表"""
        data = await self._request("GET", "/proxies")
        if not data or "error" in data:
            return []

        all_proxies = data.get("proxies", {})

        # 1. 检查是否为自定义组
        if target in self.data["custom_groups"]:
            return [n for n in self.data["custom_groups"][target] if n in all_proxies]

        # 2. 否则视为关键词，从主策略组筛选
        group_name, _ = await self._get_smart_group()
        if not group_name:
            return []

        group_info = all_proxies.get(group_name, {})
        all_nodes = group_info.get("all", [])

        if not target:
            return all_nodes  # 全部
        return [n for n in all_nodes if target.lower() in n.lower()]

    # ================= 后台监控逻辑 =================

    async def _monitor_loop(self):
        """常驻后台任务：定期检查并切换节点"""
        logger.info("[Mihomo] Auto-monitor started")
        while True:
            try:
                tasks = self.data.get("auto_tasks", {})
                if not tasks:
                    await asyncio.sleep(60)  # 没有任务时休眠久一点
                    continue

                for target, config in list(tasks.items()):
                    if not config.get("enable", False):
                        continue

                    # 获取节点列表
                    nodes = await self._resolve_nodes(target)
                    if not nodes:
                        continue

                    # 1. 测速
                    group_name, _ = await self._get_smart_group()
                    if not group_name:
                        continue

                    # 触发测速
                    encoded = urllib.parse.quote(group_name)
                    await self._request(
                        "GET",
                        f"/group/{encoded}/delay?url=http://www.gstatic.com/generate_204&timeout=2000",
                        timeout=3,
                    )
                    await asyncio.sleep(3)  # 等待结果

                    # 2. 获取最新延迟
                    proxy_data = await self._request("GET", "/proxies")
                    if not proxy_data or "error" in proxy_data:
                        continue

                    valid_nodes = []
                    for n in nodes:
                        info = proxy_data["proxies"].get(n, {})
                        delay, _ = self._parse_delay(info.get("history", []))
                        if delay < 5000:  # 过滤超时
                            valid_nodes.append((n, delay))

                    if not valid_nodes:
                        logger.warning(
                            f"[Mihomo] Auto: Target {target} has NO valid nodes!"
                        )
                        continue

                    # 3. 选最快
                    valid_nodes.sort(key=lambda x: x[1])
                    best_node, best_delay = valid_nodes[0]

                    # 4. 检查是否需要切换
                    current_node = proxy_data["proxies"][group_name]["now"]

                    # 只有当新节点比当前快 100ms 以上时才切换，避免抖动
                    curr_info = proxy_data["proxies"].get(current_node, {})
                    curr_delay, _ = self._parse_delay(curr_info.get("history", []))

                    if current_node != best_node:
                        if curr_delay > 5000 or (curr_delay - best_delay > 100):
                            logger.info(
                                f"[Mihomo] Auto-Switch: {current_node} -> {best_node} ({best_delay}ms)"
                            )
                            await self._request(
                                "PUT",
                                f"/proxies/{urllib.parse.quote(group_name)}",
                                {"name": best_node},
                            )

                await asyncio.sleep(config.get("interval", 300))  # 默认等待时间

            except Exception as e:
                logger.error(f"[Mihomo] Monitor loop error: {e}")
                await asyncio.sleep(60)

    # ================= 指令区域 =================

    @filter.command_group("mihomo")
    def mihomo(self):
        pass

    # --- 自定义组管理 ---
    @mihomo.command("cgroup")
    async def cgroup_cmd(
        self, event: AstrMessageEvent, action: str, name: str = "", keyword: str = ""
    ):
        """自定义组: create/add/del/list"""

        if action == "list":
            groups = self.data["custom_groups"]
            if not groups:
                yield event.plain_result("暂无自定义组")
                return
            msg = ["📂 自定义节点组"]
            for gname, nodes in groups.items():
                msg.append(f"• {gname}: {len(nodes)} 个节点")
            yield event.plain_result("\n".join(msg))
            return

        if not name:
            yield event.plain_result("❌ 请指定组名")
            return

        if action in ["create", "add"]:
            if not keyword:
                yield event.plain_result("❌ 请指定要添加的节点关键词")
                return

            # 搜索节点
            data = await self._request("GET", "/proxies")
            if not data or "error" in data:
                return

            group_name_api, _ = await self._get_smart_group()
            all_nodes = data["proxies"][group_name_api]["all"]

            matched = [n for n in all_nodes if keyword.lower() in n.lower()]
            if not matched:
                yield event.plain_result(f"⚠️ 未找到包含 '{keyword}' 的节点")
                return

            # 保存
            if name not in self.data["custom_groups"]:
                self.data["custom_groups"][name] = []

            # 去重添加
            current_set = set(self.data["custom_groups"][name])
            added_count = 0
            for n in matched:
                if n not in current_set:
                    self.data["custom_groups"][name].append(n)
                    added_count += 1

            self._save_data()
            yield event.plain_result(
                f"✅ 已将 {added_count} 个节点加入组 [{name}]\n当前共 {len(self.data['custom_groups'][name])} 个节点"
            )

        elif action == "del":
            if name in self.data["custom_groups"]:
                del self.data["custom_groups"][name]
                self._save_data()
                yield event.plain_result(f"🗑️ 已删除组 [{name}]")
            else:
                yield event.plain_result(f"❌ 组 [{name}] 不存在")

    # --- 自动优选管理 ---
    @mihomo.command("auto")
    async def auto_cmd(self, event: AstrMessageEvent, action: str, target: str = ""):
        """自动优选: start/stop/list [目标]"""

        if action == "list":
            tasks = self.data.get("auto_tasks", {})
            if not tasks:
                yield event.plain_result("没有正在运行的自动优选任务")
                return
            msg = ["🤖 后台优选任务"]
            for t, conf in tasks.items():
                status = "🟢 运行中" if conf["enable"] else "🔴 已暂停"
                msg.append(f"• {t}: {status} (间隔: {conf['interval']}s)")
            yield event.plain_result("\n".join(msg))
            return

        if not target:
            yield event.plain_result("❌ 请指定目标 (关键词或自定义组名)")
            return

        if action == "start":
            self.data["auto_tasks"][target] = {"enable": True, "interval": 300}
            self._save_data()
            yield event.plain_result(f"✅ 已启动 [{target}] 的自动优选 (每5分钟检测)")

        elif action == "stop":
            if target in self.data["auto_tasks"]:
                del self.data["auto_tasks"][target]
                self._save_data()
                yield event.plain_result(f"🛑 已停止 [{target}] 的自动优选")
            else:
                yield event.plain_result(f"❌ 未找到 [{target}] 的任务")

    # --- 测速 ---
    @mihomo.command("speed")
    async def speed_cmd(self, event: AstrMessageEvent, target: str = ""):
        """测速: /mihomo speed [目标]"""
        nodes = await self._resolve_nodes(target)
        if not nodes:
            yield event.plain_result("❌ 未找到匹配节点")
            return

        group_name, _ = await self._get_smart_group()
        yield event.plain_result(f"🚀 正在对 {len(nodes)} 个节点进行测速...")

        # 触发API测速
        encoded = urllib.parse.quote(group_name)
        await self._request(
            "GET",
            f"/group/{encoded}/delay?url=http://www.gstatic.com/generate_204&timeout=2000",
            timeout=3,
        )
        await asyncio.sleep(3)

        # 获取结果
        data = await self._request("GET", "/proxies")
        results = []
        for n in nodes:
            info = data["proxies"].get(n, {})
            delay, delay_text = self._parse_delay(info.get("history", []))
            if delay < 5000:  # 排除超时
                results.append((n, delay, delay_text))

        results.sort(key=lambda x: x[1])
        top_10 = results[:10]

        msg = [f"📊 测速 Top 10 ({target if target else '全部'})", "-" * 20]
        for i, (name, _, delay_text) in enumerate(top_10, 1):
            msg.append(f"{i}. {name} | {delay_text}")

        if not top_10:
            msg.append("⚠️ 所有节点均不可用")

        yield event.plain_result("\n".join(msg))

    # --- 常规切换 ---
    @mihomo.command("group")
    async def group_cmd(self, event: AstrMessageEvent, target: str = ""):
        """列出节点: /mihomo group [目标]"""
        group_name, _ = await self._get_smart_group()
        nodes = await self._resolve_nodes(target)
        if not nodes:
            yield event.plain_result("❌ 未找到节点")
            return

        # 获取当前状态
        data = await self._request("GET", "/proxies")
        current = data["proxies"][group_name]["now"]

        mapping = {}
        lines = [f"📂 {target if target else group_name}", f"当前: {current}", "-" * 20]

        idx = 1
        for n in nodes:
            info = data["proxies"].get(n, {})
            delay, delay_text = self._parse_delay(info.get("history", []))

            # 过滤显示：仅显示可用，或者当前正在使用的
            if delay > 5000 and n != current:
                continue

            mark = "🟢" if n == current else f"[{idx}]"
            lines.append(f"{mark} {n} | {delay_text}")
            mapping[idx] = n
            idx += 1

        self.selection_cache = {"group_name": group_name, "mapping": mapping}
        lines.append("-" * 20)
        lines.append("💡 发送 /mihomo use <序号> 切换")

        yield event.plain_result("\n".join(lines))

    @mihomo.command("use")
    async def use_cmd(self, event: AstrMessageEvent, index: int):
        """切换: /mihomo use <序号>"""
        if not self.selection_cache:
            yield event.plain_result("❌ 请先执行 group 命令")
            return

        node = self.selection_cache["mapping"].get(index)
        if not node:
            yield event.plain_result("❌ 序号不存在")
            return

        group = self.selection_cache["group_name"]
        await self._request(
            "PUT", f"/proxies/{urllib.parse.quote(group)}", {"name": node}
        )
        yield event.plain_result(f"✅ 已切换至: {node}")
