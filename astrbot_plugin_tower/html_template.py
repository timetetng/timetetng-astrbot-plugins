HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: sans-serif; background-color: #0d0a20; color: #E2E8F0; padding: 20px; width: 800px; display: inline-block; }
        .main-container { border: 1px solid #374151; border-radius: 12px; padding: 16px; background-color: #0d0a20; }
        .main-title { color: #ffffff; font-size: 24px; font-weight: bold; text-align: center; margin-bottom: 20px; border-bottom: 2px solid #4f46e5; padding-bottom: 10px; }
        .main-subtitle { color: #a5b4fc; font-size: 16px; text-align: center; margin-top: -10px; margin-bottom: 15px; } /* 新增副标题样式 */
        .tower-section { margin-bottom: 25px; }
        .tower-title { font-size: 22px; font-weight: bold; color: #e0e7ff; margin-bottom: 15px; padding-left: 10px; border-left: 5px solid #6366f1; }
        .buff-group { background-color: #1f2937; border-radius: 8px; margin-bottom: 15px; padding: 15px; }
        .buff-title-container { display: flex; align-items: center; margin-bottom: 12px; }
        .buff-title { font-size: 16px; font-weight: bold; color: #c7d2fe; }
        .recommended-elements { margin-left: 10px; display: flex; gap: 5px; }
        .recommended-elements img { width: 24px; height: 24px; border-radius: 50%; }
        .buff-list { list-style-type: none; padding-left: 5px; }
        .buff-item { display: flex; align-items: flex-start; margin-bottom: 8px; font-size: 14px; color: #d1d5db; line-height: 1.6; }
        .buff-icon { width: 20px; height: 20px; margin-right: 10px; filter: brightness(0.8); flex-shrink: 0; margin-top: 3px; }
        .monster-section { padding-left: 10px; }
        .floor-title { font-weight: 500; color: #a5b4fc; margin-bottom: 10px; font-size: 15px; }
        .monster-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(90px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .monster-card { display: flex; flex-direction: column; align-items: center; text-align: center; }
        .monster-img { width: 70px; height: 70px; border-radius: 50%; background-color: #374151; border: 2px solid #4f46e5; margin-bottom: 5px; }
        .monster-info { display: flex; align-items: center; justify-content: center; font-size: 13px; }
        .element-icon { width: 16px; height: 16px; margin-right: 4px; }
        .element-icon.physical-icon{border:1.5px solid white;border-radius:50%;box-sizing:border-box;background-color:#374151}
        /* 将 footer 样式添加到这里 */
        .footer {
            /* 视觉分割线: 增加一条与主容器边框同色的顶部分割线 */
            border-top: 1px solid #374151; 
            
            /* 间距调整: 增加上下间距，使其呼吸感更强 */
            margin-top: 25px;  /* 从内容区到分割线的距离 */
            padding-top: 15px; /* 从分割线到文字的距离 */
            
            /* 文本样式: 使用副标题的颜色，但降低不透明度，使其既融合又不抢眼 */
            font-size: 12px;
            color: #a5b4fc;    /* 使用与副标题一致的淡紫色 */
            opacity: 0.75;     /* 降低不透明度，使其作为次要信息 */
            text-align: center;
            letter-spacing: 0.5px; /* 略微增加字间距，提升精致感 */
        }
    </style>
</head>
<body>
    <div class="main-container">
        <div class="main-title">逆境深塔 (ID: {{ tower_id }})</div>
        {% if tower_dates %}
        <div class="main-subtitle">{{ tower_dates }}</div> {% endif %}
        {% for tower in towers %}
        <div class="tower-section">
            <div class="tower-title">{{ tower.name }}</div>
            {% for group in tower.groups %}
            <div class="buff-group">
                <div class="buff-title-container">
                    <span class="buff-title">{{ group.buff_title }}</span>
                    <div class="recommended-elements">
                        {% for element in group.recommended_elements %}
                        <img src="{{ element.icon_base64 }}" title="{{ element.name }}">
                        {% endfor %}
                    </div>
                </div>
                <ul class="buff-list">
                    {% for buff in group.buffs %}
                    <li class="buff-item">
                        <img class="buff-icon" src="{{ buff_icon_base64 }}">
                        <span>{{ buff.text | safe }}</span>
                    </li>
                    {% endfor %}
                </ul>
            </div>
            <div class="monster-section">
                {% for floor in group.floors %}
                <div class="floor-title">{{ floor.name }} - 挑战对象</div>
                <div class="monster-grid">
                    {% for monster in floor.monsters %}
                    <div class="monster-card">
                        <img class="monster-img" src="{{ monster.icon_base64 }}" style="border-color: {{ monster.element_color }};">
                        <div class="monster-info">
                            <img class="element-icon {{ monster.element_class }}" src="{{ monster.element_icon_base64 }}">
                            <span>{{ monster.name }}</span>
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% endfor %}
            </div>
            {% endfor %}
        </div>
        {% endfor %}
        
        <div class="footer">Created by timetetng & Power by Hakush.in</div>
    </div>
</body>
</html>
"""
