# astrbot_tower/utils.py

import asyncio
import base64
import json
import os
import re

import httpx
from playwright.async_api import async_playwright

from astrbot.api import logger

from .config import (
    CACHE_DIR,
    ELEMENT_MAP,
    INDEX_FILE,
    KEYWORD_STYLES,
    TRANSPARENT_PIXEL_BASE64,
)


class ImageDownloadError(Exception): pass

async def fetch_image_as_base64(http_client: httpx.AsyncClient, url: str) -> str:
    """下载图片并转为Base64，增加了重试机制。"""
    if not url:
        raise ImageDownloadError("URL为空")

    last_exception = None
    for attempt in range(3):  # 总共尝试3次
        try:
            resp = await http_client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/webp")
            encoded_string = base64.b64encode(resp.content).decode("utf-8")
            return f"data:{content_type};base64,{encoded_string}"
        except Exception as e:
            last_exception = e
            logger.warning(f"下载图片失败 (第 {attempt + 1}/3 次): {url}, 错误: {e}")
            if attempt < 2:
                await asyncio.sleep(2)  # 等待2秒后重试
    raise ImageDownloadError(f"下载图片 {url} 3次均失败") from last_exception

async def local_render_html(html_content: str, number: int):
    """使用Playwright将HTML渲染为图片。"""
    output_path = os.path.join(CACHE_DIR, f"shenta_image_{number}.png")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(device_scale_factor=2)
        await page.set_content(html_content)
        locator = page.locator(".main-container")
        await locator.screenshot(path=output_path, type="png")
        await browser.close()

async def load_index_data(index_lock: asyncio.Lock) -> dict:
    """线程安全地加载索引文件。"""
    async with index_lock:
        try:
            with open(INDEX_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

def clean_and_highlight(desc: str) -> str:
    """清理并高亮描述文本中的关键词。"""
    cleaned_desc = re.sub(r"</?color.*?>", "", desc)
    for keyword, styled in KEYWORD_STYLES.items():
        cleaned_desc = cleaned_desc.replace(keyword, styled)
    effect_color = "#f7ca2f"
    cleaned_desc = re.sub(r"(【.*?效应】)", f'<strong style="color: {effect_color};">\\1</strong>', cleaned_desc)
    return cleaned_desc

async def process_monsters(http_client: httpx.AsyncClient, monsters_obj: dict) -> list:
    """处理怪物信息，下载并转换图标。"""
    monster_details = []
    if not isinstance(monsters_obj, dict):
        return []

    for monster_data in monsters_obj.values():
        element_id = monster_data.get("Element")
        element_info = ELEMENT_MAP.get(element_id, ELEMENT_MAP[7])

        json_icon_path = monster_data.get("Icon", "")
        base_filename = ""
        if json_icon_path:
            filename_part = json_icon_path.split("/")[-1]
            base_filename = filename_part.split(".")[0]

        full_icon_url = f"https://api.hakush.in/ww/UI/UIResources/Common/Image/IconMonsterHead/{base_filename}.webp"

        monster_details.append({
            "name": monster_data.get("Name"),
            "icon_url": full_icon_url,
            "element_icon_url": element_info["icon"],
            "element_color": element_info["color"],
            "element_id": element_id if element_id else 7 # <-- 新增：保存元素的ID
        })

    unique_urls = {m[key] for m in monster_details for key in ("icon_url", "element_icon_url") if m.get(key)}

    tasks = {url: fetch_image_as_base64(http_client, url) for url in unique_urls}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    url_to_base64_map = {url: res for url, res in zip(tasks.keys(), results) if not isinstance(res, Exception)}

    processed_list = []
    for m in monster_details:
        # 判断是否为物理属性 (ID为7)，并添加一个专用的CSS类
        element_class = "physical-icon" if m.get("element_id") == 7 else ""

        processed_list.append({
            "name": m["name"],
            "icon_base64": url_to_base64_map.get(m["icon_url"], TRANSPARENT_PIXEL_BASE64),
            "element_icon_base64": url_to_base64_map.get(m["element_icon_url"], TRANSPARENT_PIXEL_BASE64),
            "element_color": m["element_color"],
            "element_class": element_class # <-- 新增：传递CSS类名
        })
    return processed_list

def get_buffs(floor_data: dict) -> list:
    """获取并处理楼层Buff。"""
    buffs_list = []
    if isinstance(buffs_obj := floor_data.get("Buffs", {}), dict):
        for buff_content in buffs_obj.values():
            if isinstance(buff_content, dict) and "Desc" in buff_content:
                buffs_list.append({"text": clean_and_highlight(buff_content["Desc"])})
    return buffs_list

def get_recommended_elements(floor_data: dict) -> list:
    """获取推荐元素列表。"""
    element_ids = floor_data.get("RecommendElement", [])
    return [ELEMENT_MAP[el_id] for el_id in element_ids if el_id in ELEMENT_MAP]

async def process_area_1(http_client: httpx.AsyncClient, floor_4_data):
    """处理区域1的数据。"""
    return {
        "name": floor_4_data.get("AreaName", "残响之塔"),
        "groups": [{
            "buff_title": "第4层 推荐属性",
            "recommended_elements": get_recommended_elements(floor_4_data),
            "buffs": get_buffs(floor_4_data),
            "floors": [{
                "name": "第4层",
                "monsters": await process_monsters(http_client, floor_4_data.get("Monsters"))
            }]
        }]
    }

async def process_area_2(http_client: httpx.AsyncClient, area_2_floors):
    """处理区域2的数据。"""
    floor_1, floor_2, floor_3, floor_4 = area_2_floors.get("1"), area_2_floors.get("2"), area_2_floors.get("3"), area_2_floors.get("4")
    groups = []
    if floor_1 and floor_2:
        groups.append({
            "buff_title": "第1-2层 推荐属性",
            "recommended_elements": get_recommended_elements(floor_1),
            "buffs": get_buffs(floor_1),
            "floors": [
                {"name": "第1层", "monsters": await process_monsters(http_client, floor_1.get("Monsters"))},
                {"name": "第2层", "monsters": await process_monsters(http_client, floor_2.get("Monsters"))}
            ]
        })
    if floor_3 and floor_4:
        groups.append({
            "buff_title": "第3-4层 推荐属性",
            "recommended_elements": get_recommended_elements(floor_3),
            "buffs": get_buffs(floor_3),
            "floors": [
                {"name": "第3层", "monsters": await process_monsters(http_client, floor_3.get("Monsters"))},
                {"name": "第4层", "monsters": await process_monsters(http_client, floor_4.get("Monsters"))}
            ]
        })
    if groups:
        return {"name": floor_1.get("AreaName", "深境之塔") if floor_1 else "深境之塔", "groups": groups}
    return None

async def process_area_3(http_client: httpx.AsyncClient, floor_4_data):
    """处理区域3的数据。"""
    return {
        "name": floor_4_data.get("AreaName", "回音之塔"),
        "groups": [{
            "buff_title": "第4层 推荐属性",
            "recommended_elements": get_recommended_elements(floor_4_data),
            "buffs": get_buffs(floor_4_data),
            "floors": [{
                "name": "第4层",
                "monsters": await process_monsters(http_client, floor_4_data.get("Monsters"))
            }]
        }]
    }
