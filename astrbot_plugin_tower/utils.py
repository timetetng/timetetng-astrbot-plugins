import httpx
import base64
import re
import json
from typing import Dict, Any, List
from .config import ELEMENT_MAP

async def fetch_image_as_base64(client: httpx.AsyncClient, url: str) -> str:
    """抓取图片并转换为 base64"""
    if not url:
        return ""
    if url.startswith('/'):
        url = "https://api.encore.moe" + url
    try:
        resp = await client.get(url, timeout=5)
        if resp.status_code == 200:
            return f"data:image/png;base64,{base64.b64encode(resp.content).decode('utf-8')}"
    except Exception as e:
        print(f"Fetch image error for {url}: {e}")
    return ""

async def get_processed_tower_data(client: httpx.AsyncClient, period: int) -> List[Dict]:
    """从 encore.moe 获取深塔数据并处理为模板需要的格式"""
    url = f"https://api-v2.encore.moe/api/zh-Hans/toa/{period}"
    
    try:
        response = await client.get(url, timeout=10)
        response.raise_for_status()
        raw_data = response.json()
    except Exception as e:
        print(f"Error fetching tower data: {e}")
        return []
        
    data_content = raw_data.get(str(period))
    if not data_content:
        return []
    
    area_names = {"1": "残响之塔", "2": "深境之塔", "3": "回音之塔"}
    processed_towers = []
    
    for area_id in ["1", "2", "3"]:
        if area_id not in data_content:
            continue
        
        tower_info = {
            "name": area_names[area_id],
            "groups": []
        }
        
        floors_dict = data_content[area_id]
        sorted_floor_keys = sorted(floors_dict.keys(), key=int)
        
        groups = []
        current_group = None
        
        for f_key in sorted_floor_keys:
            # 修复 KeyError: 0
            # encore API 结构为 Area -> Floor -> Cost -> Array
            cost_dict = floors_dict[f_key]
            if not cost_dict:
                continue
            
            # 取出字典的值并获取第一条数据的第一个塔层信息
            raw_floor = list(cost_dict.values())[0][0]
            
            # 清洗Buff的HTML标签
            buffs = [{"text": re.sub(r'<[^>]+>', '', b.get("desc", ""))} for b in raw_floor.get("buffs", [])]
            buffs_str = json.dumps(buffs, ensure_ascii=False)
            
            monsters = []
            for m in raw_floor.get("monsters", []):
                element_name = m["elements"][0]["name"] if m.get("elements") else "物理"
                
                e_color = "#ffffff"
                e_class = ""
                e_icon_url = ""
                for eid, e_info in ELEMENT_MAP.items():
                    if e_info["name"] == element_name:
                        e_color = e_info["color"]
                        e_icon_url = e_info["icon"]
                        if element_name == "物理":
                            e_class = "physical-icon"
                        break
                
                monster_icon = m.get("icon", "")
                if monster_icon and not monster_icon.startswith('http'):
                    monster_icon = "https://api.encore.moe" + monster_icon
                    
                monsters.append({
                    "name": m.get("name", "未知"),
                    "icon": monster_icon,
                    "element_color": e_color,
                    "element_class": e_class,
                    "element_icon_url": e_icon_url
                })
                
            floor_data = {
                "name": f"第{f_key}层",
                "monsters": monsters
            }
            
            # 将同Buff的层数归组（比如1-2层同组）
            if current_group and current_group["_buffs_str"] == buffs_str:
                current_group["floors"].append(floor_data)
                current_group["end_floor"] = f_key
            else:
                current_group = {
                    "_buffs_str": buffs_str,
                    "buffs": buffs,
                    "start_floor": f_key,
                    "end_floor": f_key,
                    "floors": [floor_data],
                    "recommended_elements": []
                }
                groups.append(current_group)
                
        # 处理每组的标题并根据Buff反推推荐属性
        for g in groups:
            if g["start_floor"] == g["end_floor"]:
                g["buff_title"] = f"第{g['start_floor']}层 推荐属性"
            else:
                g["buff_title"] = f"第{g['start_floor']}-{g['end_floor']}层 推荐属性"
                
            rec_elements = set()
            for b in g["buffs"]:
                text = b["text"]
                for e_id, e_info in ELEMENT_MAP.items():
                    if f"{e_info['name']}伤害" in text or f"{e_info['name']}抗性降低" in text:
                        rec_elements.add(e_id)
            g["recommended_elements"] = [{"name": ELEMENT_MAP[eid]["name"], "icon": ELEMENT_MAP[eid]["icon"]} for eid in rec_elements]
            
        tower_info["groups"] = groups
        processed_towers.append(tower_info)
        
    return processed_towers
