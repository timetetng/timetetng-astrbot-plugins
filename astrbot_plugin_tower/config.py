# config.py

import os

# --- 插件基础配置 ---
PLUGIN_NAME = "astrbot_plugin_tower"
PLUGIN_DATA_DIR = os.path.join("data", "plugin_data", PLUGIN_NAME)
CACHE_DIR = os.path.join(PLUGIN_DATA_DIR, "cache")
INDEX_FILE = os.path.join(PLUGIN_DATA_DIR, "index.json")

# --- 深塔数据探测配置 ---
MIN_TOWER_ID_PROBE = 26  # 从第几期开始请求
MAX_TOWER_ID_PROBE = 200  

# --- 游戏元素与样式配置 ---
ELEMENT_MAP = {
    1: {"name": "冷凝", "icon": "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconElement/T_IconElementIce2.webp", "color": "#41aefb"},
    2: {"name": "热熔", "icon": "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconElement/T_IconElementFire2.webp", "color": "#f0744e"},
    3: {"name": "导电", "icon": "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconElement/T_IconElementThunder2.webp", "color": "#b45bff"},
    4: {"name": "气动", "icon": "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconElement/T_IconElementWind2.webp", "color": "#53f9b1"},
    5: {"name": "衍射", "icon": "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconElement/T_IconElementLight2.webp", "color": "#f7ca2f"},
    6: {"name": "湮灭", "icon": "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconElement/T_IconElementDark2.webp", "color": "#e649a6"},
    7: {"name": "物理", "icon": "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconElement/T_IconElementZero2.webp", "color": "#ffffff"},
}

KEYWORD_STYLES = {
    "冷凝抗性": f'<strong style="color: {ELEMENT_MAP[1]["color"]};">冷凝抗性</strong>',
    "热熔抗性": f'<strong style="color: {ELEMENT_MAP[2]["color"]};">热熔抗性</strong>',
    "导电抗性": f'<strong style="color: {ELEMENT_MAP[3]["color"]};">导电抗性</strong>',
    "气动抗性": f'<strong style="color: {ELEMENT_MAP[4]["color"]};">气动抗性</strong>',
    "衍射抗性": f'<strong style="color: {ELEMENT_MAP[5]["color"]};">衍射抗性</strong>',
    "湮灭抗性": f'<strong style="color: {ELEMENT_MAP[6]["color"]};">湮灭抗性</strong>',
}

# --- 资源 URL  ---
BUFF_ICON_URL = "https://api.encore.moe/resource/Data/Game/Aki/UI/UIResources/Common/Image/IconAttribute/T_Iconpropertyredattack_UI.webp"

# 透明背景占位符
TRANSPARENT_PIXEL_BASE64 = (
    "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
