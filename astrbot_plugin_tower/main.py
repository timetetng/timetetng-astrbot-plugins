# main.py

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from jinja2 import Template

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .config import *
from .html_template import HTML_TEMPLATE
from .utils import *


@register(PLUGIN_NAME, "TimeXingjian", "查询鸣潮境深塔信息并生成图片", "1.0.6")
class ShentaScreenshotPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.http_client = httpx.AsyncClient(timeout=10) # 缩短单次超时，依赖重试
        self.index_lock = asyncio.Lock()
        os.makedirs(PLUGIN_DATA_DIR, exist_ok=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
        if not os.path.exists(INDEX_FILE):
            with open(INDEX_FILE, "w") as f: json.dump({}, f)
        asyncio.create_task(self._periodic_update_task())
        logger.info(f"{PLUGIN_NAME} 插件已加载 (v11.0.6 - 标题显示时间范围)，后台更新任务已启动。")

    async def _periodic_update_task(self):
        while True:
            try: await self.update_tower_index()
            except Exception as e: logger.error(f"深塔索引更新失败: {e}")
            await asyncio.sleep(60 * 60 * 24)

    async def update_tower_index(self):
        logger.info("开始更新深塔索引...")
        async with self.index_lock:
            try:
                with open(INDEX_FILE, encoding="utf-8") as f: index_data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError): index_data = {}
            latest_id = max([int(k) for k in index_data.keys()] or [0])
            start_scan_id = max(MIN_TOWER_ID_PROBE, latest_id + 1 if latest_id > 0 else MIN_TOWER_ID_PROBE)
            for i in range(start_scan_id, MAX_TOWER_ID_PROBE):
                api_url = f"https://api.hakush.in/ww/data/zh/tower/{i}.json"
                try:
                    resp = await self.http_client.get(api_url)
                    if resp.status_code == 404: break
                    resp.raise_for_status()
                    content_bytes, tower_info = resp.content, json.loads(resp.content)
                    new_hash, begin_date, end_date = hashlib.md5(content_bytes).hexdigest(), tower_info.get("Begin"), tower_info.get("End")
                    if not (begin_date and end_date): continue
                    is_future = datetime.strptime(begin_date, "%Y-%m-%d").date() > datetime.now(timezone.utc).date()
                    old_entry = index_data.get(str(i))
                    if is_future and old_entry and old_entry.get("hash") != new_hash:
                        logger.info(f"检测到第 {i} 期深塔数据更新，清除旧缓存。")
                        cache_path = os.path.join(CACHE_DIR, f"shenta_image_{i}.png")
                        if os.path.exists(cache_path): os.remove(cache_path)
                    index_data[str(i)] = {"begin": begin_date, "end": end_date, "hash": new_hash}
                except httpx.HTTPStatusError: break
                except Exception as e: logger.warning(f"处理第 {i} 期数据时出错: {e}")
            with open(INDEX_FILE, "w", encoding="utf-8") as f: json.dump(index_data, f, ensure_ascii=False, indent=4)
        logger.info("深塔索引更新完成。")

    def get_period_id(self, term: str) -> int | None:
        """
        正确处理更新日当天新旧周期并存的问题。
        """
        logger.info("--- get_period_id 开始执行 (最终修复版) ---")
        logger.info(f"接收到的查询参数 term: '{term}'")
        try:
            with open(INDEX_FILE, encoding="utf-8") as f: index_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.error("深塔索引文件读取失败或不存在。")
            return None

        tz_server = timezone(timedelta(hours=8))
        now_server_time = datetime.now(tz_server)
        if now_server_time.hour < 4:
            today = (now_server_time - timedelta(days=1)).date()
            logger.info(f"当前服务器时间 {now_server_time.strftime('%H:%M')} (早于4点)，游戏有效日期判定为: {today}")
        else:
            today = now_server_time.date()
            logger.info(f"当前服务器时间 {now_server_time.strftime('%H:%M')} (晚于4点)，游戏有效日期判定为: {today}")

        potential_matches = []
        future_periods = []
        past_periods = []

        # 1. 遍历所有周期，分类存放
        for pid_str, dates in index_data.items():
            if not (dates.get("begin") and dates.get("end")): continue
            try:
                pid, begin_date, end_date = int(pid_str), datetime.strptime(dates["begin"], "%Y-%m-%d").date(), datetime.strptime(dates["end"], "%Y-%m-%d").date()
            except (ValueError, TypeError): continue

            if begin_date <= today <= end_date:
                potential_matches.append({"id": pid, "begin": begin_date})
            elif today > end_date:
                past_periods.append({"id": pid, "end": end_date})
            elif today < begin_date:
                future_periods.append({"id": pid, "begin": begin_date})

        # 2. 根据优先级确定基准ID
        current_id_base = None
        if potential_matches:
            # 如果有多个匹配（只会在更新日发生），按开始日期倒序排，取最新的那个
            potential_matches.sort(key=lambda p: p["begin"], reverse=True)
            current_id_base = potential_matches[0]["id"]
        elif future_periods:
            future_periods.sort(key=lambda p: p["begin"])
            current_id_base = future_periods[0]["id"]
        elif past_periods:
            past_periods.sort(key=lambda p: p["end"], reverse=True)
            current_id_base = past_periods[0]["id"]

        if current_id_base is None:
            logger.warning("未能根据当前日期计算出任何有效的深塔期数。")
            return None

        # 3. 计算最终目标ID
        offset = {"上期": -1, "当期": 0, "本期": 0, "下期": 1, "下下期": 2}.get(term, None)
        final_target_id = None
        if offset is not None:
            final_target_id = current_id_base + offset
        else:
            try:
                final_target_id = int(term)
                logger.info(f"参数 '{term}' 为数字，直接作为最终目标ID: {final_target_id}")
            except (ValueError, TypeError):
                final_target_id = current_id_base
                logger.warning(f"无法解析的查询参数 '{term}'，将默认返回当期ID: {final_target_id}")

        return final_target_id

    @filter.command("深塔", alias={"深渊"})
    async def shenta_info(self, event: AstrMessageEvent, period: str = "当期"):

        target_id = self.get_period_id(period)

        if target_id is None:
            logger.error(f"无法获取到有效的目标ID (输入参数: '{period}')，流程中止。")
            await event.send(event.plain_result(f"无法计算“{period}”对应的期数，请检查索引文件或后台日志。"))
            return

        index_data = await load_index_data(self.index_lock)
        if str(target_id) not in index_data:
            logger.warning(f"目标ID {target_id} 在索引文件中不存在。")
            await event.send(event.plain_result(f"暂无“{period}”({target_id}期)的数据。"))
            return

        cache_path = os.path.join(CACHE_DIR, f"shenta_image_{target_id}.png")

        if os.path.exists(cache_path):
            logger.info(f"✅ 命中缓存！直接发送图片: {cache_path}")
            yield event.image_result(cache_path)
            return

        logger.info("❌ 未命中缓存，开始生成新图片...")

        # 获取深塔时间范围
        tower_dates_str = None
        if str(target_id) in index_data:
            dates = index_data[str(target_id)]
            begin_date_obj = datetime.strptime(dates["begin"], "%Y-%m-%d")
            end_date_obj = datetime.strptime(dates["end"], "%Y-%m-%d")
            # 格式化为 MM.DD - MM.DD
            tower_dates_str = f"{begin_date_obj.month:02d}.{begin_date_obj.day:02d} - {end_date_obj.month:02d}.{end_date_obj.day:02d}"

        try:
            api_url = f"https://api.hakush.in/ww/data/zh/tower/{target_id}.json"
            headers = { "User-Agent": "Mozilla/5.0", "Referer": "https://ww2.hakush.in/" }
            response = await self.http_client.get(api_url, headers=headers)
            response.raise_for_status()
            tower_data = response.json()
            tasks = []
            all_areas = tower_data.get("Area", {})
            if "1" in all_areas and (floor_4_data := all_areas["1"].get("Floor", {}).get("4")):
                tasks.append(process_area_1(self.http_client, floor_4_data))
            if "2" in all_areas:
                tasks.append(process_area_2(self.http_client, all_areas["2"].get("Floor", {})))
            if "3" in all_areas and (floor_4_data := all_areas["3"].get("Floor", {}).get("4")):
                tasks.append(process_area_3(self.http_client, floor_4_data))

            processed_towers = await asyncio.gather(*tasks)
            all_image_urls_to_fetch = { el["icon"] for el in ELEMENT_MAP.values() }
            all_image_urls_to_fetch.add(BUFF_ICON_URL)

            base64_map = {
                url: b64 for url, b64 in zip(
                    all_image_urls_to_fetch,
                    await asyncio.gather(*(fetch_image_as_base64(self.http_client, url) for url in all_image_urls_to_fetch))
                )
            }

            for tower in processed_towers:
                if not tower: continue
                for group in tower["groups"]:
                    for element in group["recommended_elements"]:
                        element["icon_base64"] = base64_map.get(element["icon"], TRANSPARENT_PIXEL_BASE64)

            template_data = {
                "tower_id": target_id,
                "towers": [t for t in processed_towers if t],
                "buff_icon_base64": base64_map.get(BUFF_ICON_URL),
                "tower_dates": tower_dates_str
            }
            html_content = Template(HTML_TEMPLATE).render(template_data)

            await local_render_html(html_content, target_id)
            yield event.image_result(cache_path)

        except ImageDownloadError as e:
            logger.error(f"图片生成失败，因资源下载不完整: {e}")
            yield event.plain_result("图片生成失败，部分资源下载超时或失败。该结果不会被缓存，请稍后重试。")
        except httpx.HTTPStatusError as e:
            logger.error("API请求失败: %s", e)
            yield event.plain_result(f"获取第 {target_id} 期信息失败：服务器返回错误 (状态码: {e.response.status_code})。")
        except Exception:
            logger.exception("处理深塔信息时发生未知错误")
            yield event.plain_result(f"处理第 {target_id} 期信息时发生未知错误，请检查后台日志。")

    # --- 快捷命令 (保持不变) ---
    @filter.command("当期深塔", alias={"本期深塔"})
    async def shenta_current_period(self, event: AstrMessageEvent):
        """处理“当期深塔”和“本期深塔”命令"""
        async for result in self.shenta_info(event, period="当期"):
            yield result

    @filter.command("上期深塔")
    async def shenta_previous_period(self, event: AstrMessageEvent):
        """处理“上期深塔”命令"""
        async for result in self.shenta_info(event, period="上期"):
            yield result

    @filter.command("下期深塔")
    async def shenta_next_period(self, event: AstrMessageEvent):
        """处理“下期深塔”命令"""
        async for result in self.shenta_info(event, period="下期"):
            yield result

    @filter.command("下下期深塔")
    async def shenta_next_next_period(self, event: AstrMessageEvent):
        """处理“下下期深塔”命令"""
        async for result in self.shenta_info(event, period="下下期"):
            yield result

    @filter.command("清除深塔缓存")
    async def clear_shenta_cache(self, event: AstrMessageEvent):
        """处理“清除深塔缓存”命令"""
        if not os.path.exists(CACHE_DIR):
            await event.send(event.plain_result("缓存目录不存在，无需清除。"))
            return

        try:
            files_in_cache = [f for f in os.listdir(CACHE_DIR) if os.path.isfile(os.path.join(CACHE_DIR, f))]
            num_files = len(files_in_cache)

            if num_files == 0:
                await event.send(event.plain_result("缓存已为空，无需清除。"))
                return

            for filename in files_in_cache:
                os.remove(os.path.join(CACHE_DIR, filename))

            logger.info(f"成功清除了 {num_files} 个深塔缓存文件。")
            await event.send(event.plain_result(f"已成功清除 {num_files} 个深塔缓存文件。"))

        except Exception as e:
            logger.error(f"清除深塔缓存时发生错误: {e}")
            await event.send(event.plain_result("清除缓存时发生错误，请检查后台日志。"))

    @filter.command("深塔帮助")
    async def shenta_help(self, event: AstrMessageEvent):
        """显示深塔查询插件的帮助信息"""
        help_text = (
            "--- 逆境深塔查询帮助 ---\n"
            "/深塔 [参数]\n"
            "说明: 查询鸣潮逆境深塔信息。\n\n"
            "▶ 参数说明:\n"
            "• (无参数): 查询当期深塔。\n"
            "  示例: /深塔\n\n"
            "• [期数]: 查询指定期数的深塔。\n"
            "  示例: /深塔 26\n\n"
            "• [关键词]: 使用关键词查询\n"
            "  示例: /深塔 下期\n\n"
            "▶ 快捷指令:\n"
            "• /当期深塔 (或 /本期深塔)\n"
            "• /上期深塔\n"
            "• /下期深塔\n"
            "• /下下期深塔"
        )
        await event.send(event.plain_result(help_text))
