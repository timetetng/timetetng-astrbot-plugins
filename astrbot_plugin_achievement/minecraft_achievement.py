import base64
import io
import math
import os
import re

from PIL import Image, ImageDraw, ImageFont

# 注意：删除了 urllib.request 的导入

class AchievementGenerator:
    """
    一个功能强大的 Minecraft 风格成就图片生成器。
    负责生成单个成就的图片对象。
    """
    DEFAULT_COLORS = {
        "bg": (42, 42, 42, 220), "desc": (255, 255, 255),
        "border_dark": (25, 25, 25, 255), "border_light": (85, 85, 85, 255),
        "shadow": (10, 10, 10, 255)
    }

    TITLE_COLORS = {
        "common":    (255, 255, 255),   # 普通: 白色
        "rare":      (100, 180, 255),   # 稀有: 蓝色
        "epic":      (200, 100, 255),   # 史诗: 紫色
        "legendary": (255, 170, 50),    # 传说: 橙色
        "mythic":    (255, 85, 85),     # 神话: 红色
        "miracle":   (255, 235, 100),   # 奇迹: 亮金色
        "flawless":  "gradient",        # 无瑕: 渐变色
        "locked":    (120, 120, 120)    # 未解锁: 灰色
    }
    FLAWLESS_GRADIENT_COLORS = [(255, 205, 26), (255, 46, 157)]

    def __init__(self, font_path, icon_cache_manager): # <-- 修改点
        if not os.path.exists(font_path):
            raise FileNotFoundError(f"字体文件未找到: {font_path}")

        self.font_path = font_path
        self.title_font = ImageFont.truetype(self.font_path, 16)
        self.desc_font = ImageFont.truetype(self.font_path, 16)
        self.icon_cache = icon_cache_manager # <-- 新增

        # --- 可配置参数 ---
        self.content_width = 320
        self.min_content_height = 64
        self.border_width = 2
        self.corner_radius = 5
        self.padding = 12
        self.icon_size = 32
        self.text_spacing = 4
        self.title_y_adjust = -3
        self.title_char_spacing = 1
        self.desc_char_spacing = 0

    def _strip_minecraft_codes(self, text: str) -> str:
        return re.sub(r"§.", "", text)

    def _pixelate_icon(self, icon_image: Image.Image) -> Image.Image:
        icon = icon_image.resize((self.icon_size, self.icon_size), Image.Resampling.NEAREST)
        return icon.convert("RGBA")

    def _draw_text_custom(self, draw, pos, text, font, fill, shadow_color, char_spacing=0, line_spacing=4):
        x, y = pos
        lines = text.split("\n")
        font_height_bbox = font.getbbox("Tg")
        font_height = font_height_bbox[3] - font_height_bbox[1]

        for line in lines:
            current_x = x
            for i, char in enumerate(line):
                if isinstance(fill, list):
                    num_chars = len(line)
                    if num_chars > 1:
                        ratio = i / (num_chars - 1)
                        r = int(fill[0][0] * (1 - ratio) + fill[1][0] * ratio)
                        g = int(fill[0][1] * (1 - ratio) + fill[1][1] * ratio)
                        b = int(fill[0][2] * (1 - ratio) + fill[1][2] * ratio)
                        char_color = (r, g, b)
                    else:
                        char_color = fill[0]
                else:
                    char_color = fill

                draw.text((current_x + 1, y + 1), char, font=font, fill=shadow_color)
                draw.text((current_x, y), char, font=font, fill=char_color)

                char_width = draw.textbbox((0,0), char, font=font)[2]
                current_x += char_width + char_spacing

            y += font_height + line_spacing

    def _wrap_text_by_pixel(self, text: str, max_width: int, font: ImageFont.FreeTypeFont, draw_context: ImageDraw.ImageDraw, char_spacing=0) -> str:
        words = re.findall(r"[a-zA-Z0-9]+|\s+|[^\s\da-zA-Z]", text)
        lines = []
        current_line = ""
        for word in words:
            if not current_line and word.isspace(): continue
            test_line = current_line + word
            bbox = draw_context.textbbox((0, 0), test_line, font=font)
            line_width = (bbox[2] - bbox[0])
            if len(test_line) > 1:
                line_width += (len(test_line) - 1) * char_spacing

            if line_width <= max_width:
                current_line = test_line
            else:
                lines.append(current_line.strip())
                current_line = word.strip()
        if current_line:
            lines.append(current_line.strip())
        return "\n".join(lines)

    async def create(self, title: str, description: str, icon_path: str, # <-- 修改点
               theme: str = "common",
               output_path: str = None,
               output_format: str = "file",
               wrap_text: bool = True):

        title = self._strip_minecraft_codes(title)
        description = self._strip_minecraft_codes(description)

        if output_format == "file" and not output_path:
            raise ValueError("当 output_format='file' 时，必须提供 output_path。")
        colors = self.DEFAULT_COLORS.copy()
        title_color_or_flag = self.TITLE_COLORS.get(theme, self.TITLE_COLORS["common"])

        text_x = self.padding + self.icon_size + self.padding
        max_text_width = self.content_width - text_x - self.padding
        temp_img = Image.new("RGB", (1,1)); temp_draw = ImageDraw.Draw(temp_img)

        if wrap_text:
            wrapped_desc = self._wrap_text_by_pixel(description, max_text_width, self.desc_font, temp_draw, char_spacing=self.desc_char_spacing)
        else:
            wrapped_desc = description

        title_bbox = temp_draw.textbbox((0,0), title, font=self.title_font)
        title_height = title_bbox[3] - title_bbox[1]

        desc_height = 0
        if wrapped_desc:
            num_lines = len(wrapped_desc.split("\n"))
            font_height_bbox = self.desc_font.getbbox("Tg")
            font_height = font_height_bbox[3] - font_height_bbox[1]
            desc_height = (num_lines * font_height) + (num_lines - 1) * self.text_spacing

        total_text_height = title_height + (self.text_spacing + desc_height if wrapped_desc else 0)
        content_height = max(self.min_content_height, total_text_height + self.padding * 2)
        total_height = content_height + self.border_width * 2
        total_width = self.content_width + self.border_width * 2

        final_image = Image.new("RGBA", (total_width, total_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(final_image)
        draw.rounded_rectangle((0, 0, total_width, total_height), fill=colors["border_dark"], radius=self.corner_radius + self.border_width)
        draw.rounded_rectangle((1, 1, total_width - 1, total_height - 1), fill=colors["border_light"], radius=self.corner_radius + self.border_width - 1)
        draw.rounded_rectangle((self.border_width, self.border_width, total_width - self.border_width, total_height - self.border_width), fill=colors["bg"], radius=self.corner_radius)

        # --- 全新的、基于缓存的图标处理逻辑 ---
        try:
            local_icon_path = icon_path
            # 1. 如果是网络路径，则通过缓存管理器获取本地路径
            if icon_path.startswith(("http://", "https://")):
                local_icon_path = await self.icon_cache.get_local_path(icon_path)

            # 2. 统一从本地路径加载图标
            if local_icon_path and os.path.exists(local_icon_path):
                with Image.open(local_icon_path) as raw_icon:
                    pixelated_icon = self._pixelate_icon(raw_icon)
                    icon_y = (total_height - self.icon_size) // 2
                    final_image.paste(pixelated_icon, (self.padding + self.border_width, icon_y), pixelated_icon)
            else:
                 # 如果路径为空或文件不存在（可能缓存也失败了），则不显示图标
                 print(f"Warning: Icon path is invalid or file does not exist: {local_icon_path}")

        except Exception as e:
            # 静默处理，避免因一个图标错误导致整个功能崩溃
            print(f"Warning: Failed to process icon from {icon_path}. Error: {e}")
            pass
        # --- 图标处理逻辑结束 ---

        text_start_y = (total_height - total_text_height) // 2
        title_pos = (text_x + self.border_width, text_start_y + self.title_y_adjust)
        desc_pos = (text_x + self.border_width, text_start_y + title_height + self.text_spacing)

        fill_color = self.FLAWLESS_GRADIENT_COLORS if title_color_or_flag == "gradient" else title_color_or_flag
        self._draw_text_custom(draw, title_pos, title, self.title_font, fill_color, colors["shadow"], char_spacing=self.title_char_spacing, line_spacing=self.text_spacing)
        if wrapped_desc:
            self._draw_text_custom(draw, desc_pos, wrapped_desc, self.desc_font, colors["desc"], colors["shadow"], char_spacing=self.desc_char_spacing, line_spacing=self.text_spacing)

        if output_format == "file":
            final_image.save(output_path)
            return None
        elif output_format == "object":
            return final_image
        elif output_format == "base64":
            buffer = io.BytesIO()
            final_image.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("utf-8")
        else:
            raise ValueError(f"未知的 output_format: '{output_format}'。请使用 'file', 'object', 或 'base64'。")


class AchievementBoardGenerator:
    """
    成就看板生成器。
    使用 AchievementGenerator 生成所有独立的成就图片，然后将它们拼接成一个总览图。
    """
    RARITY_ORDER = ["flawless", "miracle", "mythic", "legendary", "epic", "rare", "common"]
    RARITY_NAMES = {
        "common": "普通 Common", "rare": "稀有 Rare", "epic": "史诗 Epic",
        "legendary": "传说 Legendary", "mythic": "神话 Mythic",
        "miracle": "奇迹 Miracle", "flawless": "无瑕 Flawless"
    }

    def __init__(self, generator: AchievementGenerator, font_path: str):
        self.generator = generator
        self.board_font_title = ImageFont.truetype(font_path, 32)
        self.board_font_rarity = ImageFont.truetype(font_path, 24)
        self.board_font_progress = ImageFont.truetype(font_path, 18)
        self.lock_icon_path = "lock_icon.png" # 确保此文件存在于插件根目录
        # --- 可配置参数 ---
        self.board_width = 750
        self.padding = 20
        self.columns = 2
        self.grid_spacing_x = 20
        self.grid_spacing_y = 15
        self.section_spacing = 30
        self.bg_color = (50, 50, 50)
        self.title_color = (255, 255, 255)
        self.progress_text_color = (200, 200, 200)

    def _draw_rarity_title(self, draw, pos, text, rarity):
        color_or_flag = self.generator.TITLE_COLORS.get(rarity)
        if color_or_flag == "gradient":
            gradient_colors = self.generator.FLAWLESS_GRADIENT_COLORS
            start_color, end_color = gradient_colors
            num_chars = len(text)
            current_x = pos[0]
            for i, char in enumerate(text):
                if num_chars > 1:
                    ratio = i / (num_chars - 1)
                    r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
                    g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
                    b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
                    char_color = (r, g, b)
                else:
                    char_color = start_color
                draw.text((current_x, pos[1]), char, font=self.board_font_rarity, fill=char_color)
                char_width = draw.textbbox((0,0), char, font=self.board_font_rarity)[2]
                current_x += char_width
        else:
            draw.text(pos, text, font=self.board_font_rarity, fill=color_or_flag)

    def _prepare_assets(self, create_if_missing=False):
        if not os.path.exists(self.lock_icon_path) and create_if_missing:
            lock_img = Image.new("RGBA", (32, 32), (0,0,0,0))
            d = ImageDraw.Draw(lock_img)
            d.rectangle([(8, 14), (24, 30)], fill=(80,80,80))
            d.pieslice([(4,6),(28,24)], 180, 0, fill=(100,100,100))
            d.pieslice([(8,6),(24,24)], 180, 0, fill=self.bg_color)
            lock_img.save(self.lock_icon_path)

    async def create_board(self, user_name: str, all_achievements: dict, unlocked_ids: list, unlocked_count: int, total_count: int, output_path: str): # <-- 修改点
        self._prepare_assets(create_if_missing=True) # 自动创建锁图标

        ach_by_rarity = {rarity: [] for rarity in self.RARITY_ORDER}
        for ach_id, ach_data in all_achievements.items():
            rarity = ach_data.get("rarity", "common").lower()
            if rarity in ach_by_rarity:
                ach_by_rarity[rarity].append((ach_id, ach_data))

        # 预计算总高度
        total_height = self.padding
        title_bbox = self.board_font_title.getbbox(f"成就殿堂 - {user_name}")
        total_height += title_bbox[3] - title_bbox[1] + self.section_spacing

        single_ach_img = await self.generator.create("a", "b", self.lock_icon_path, output_format="object") # <-- 修改点

        ach_width, ach_height = single_ach_img.size
        for rarity in self.RARITY_ORDER:
            ach_list = ach_by_rarity.get(rarity, [])
            if not ach_list: continue

            rarity_bbox = self.board_font_rarity.getbbox(self.RARITY_NAMES[rarity])
            total_height += rarity_bbox[3] - rarity_bbox[1] + self.grid_spacing_y
            rows = math.ceil(len(ach_list) / self.columns)
            total_height += rows * (ach_height + self.grid_spacing_y)
            total_height += self.section_spacing
        total_height = int(total_height)

        board = Image.new("RGB", (self.board_width, total_height), self.bg_color)
        draw = ImageDraw.Draw(board)

        current_y = self.padding

        # --- 绘制标题和进度 ---
        title_text = f"成就殿堂 - {user_name}"
        draw.text((self.padding, current_y), title_text, font=self.board_font_title, fill=self.title_color)

        progress_text = f"已解锁 ({unlocked_count}/{total_count})"
        progress_bbox = draw.textbbox((0, 0), progress_text, font=self.board_font_progress)
        progress_width = progress_bbox[2] - progress_bbox[0]
        progress_height = progress_bbox[3] - progress_bbox[1]

        title_height = title_bbox[3] - title_bbox[1]

        progress_x = self.board_width - self.padding - progress_width
        progress_y = current_y + (title_height - progress_height)

        draw.text((progress_x, progress_y), progress_text, font=self.board_font_progress, fill=self.progress_text_color)

        current_y += title_height + self.section_spacing

        for rarity in self.RARITY_ORDER:
            ach_list = ach_by_rarity.get(rarity, [])
            if not ach_list: continue

            self._draw_rarity_title(draw, (self.padding, current_y), self.RARITY_NAMES[rarity], rarity)
            rarity_bbox = self.board_font_rarity.getbbox(self.RARITY_NAMES[rarity])
            current_y += rarity_bbox[3] - rarity_bbox[1] + self.grid_spacing_y

            col_count = 0
            for ach_id, ach_data in ach_list:
                is_unlocked = ach_id in unlocked_ids

                if is_unlocked:
                    img = await self.generator.create(ach_data["title"], ach_data["description"], ach_data["icon_path"], theme=rarity, output_format="object") # <-- 修改点
                else:
                    img = await self.generator.create("??????", "条件尚未达成", self.lock_icon_path, theme="locked", output_format="object") # <-- 修改点

                if img:
                    x = self.padding + (col_count * (ach_width + self.grid_spacing_x))
                    y = current_y
                    board.paste(img, (x, y), img if img.mode == "RGBA" else None)

                col_count += 1
                if col_count >= self.columns:
                    col_count = 0
                    current_y += ach_height + self.grid_spacing_y

            if col_count != 0:
                current_y += ach_height + self.grid_spacing_y

            current_y += self.section_spacing

        board.save(output_path)
