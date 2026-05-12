import os
import time
import random
import asyncio
import aiohttp
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

GREEK_MAP = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "ε": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
    "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu",
    "ν": "nu", "ξ": "xi", "ο": "omicron", "π": "pi",
    "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega"
}

def add_chromatic_aberration(image: Image.Image, intensity: int = 4) -> Image.Image:
    """色散效果"""
    intensity = max(1, min(20, intensity))
    r, g, b = image.split()[:3]
    
    r_offset = ImageChops.offset(r, -intensity, -intensity)
    g_offset = ImageChops.offset(g, 0, 0)
    b_offset = ImageChops.offset(b, intensity, intensity)
    
    if len(image.split()) == 4:
        a = image.split()[3]
        return Image.merge("RGBA", (r_offset, g_offset, b_offset, a))
    else:
        return Image.merge("RGB", (r_offset, g_offset, b_offset))


def add_glitch_effect(image: Image.Image, intensity: int = 0) -> Image.Image:
    """故障效果"""
    intensity = max(0, min(5, intensity))
    if intensity == 0:
        return image.copy()
    
    width, height = image.size
    glitched = image.copy()
    
    if intensity >= 1:
        num_shifts = min(3, max(1, intensity))
        for _ in range(num_shifts):
            max_shift = max(5, int(width * 0.1 * intensity / 5))
            shift_amount = random.randint(2, max_shift)
            shift_direction = random.choice([-1, 1])
            min_shift_height = height // 20
            max_shift_height = height // 6 + (height // 12) * (intensity - 1)
            shift_height = random.randint(min_shift_height, max_shift_height)
            shift_y = random.randint(0, height - shift_height)
            
            region = glitched.crop((0, shift_y, width, shift_y + shift_height))
            glitched.paste(region, (shift_amount * shift_direction, shift_y))
        
    if intensity >= 2:
        base_noise = 50
        noise_intensity = base_noise * (intensity ** 2)
        for _ in range(noise_intensity):
            x = random.randint(0, width - 1)
            y = random.randint(0, height - 1)
            color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255), 255)
            glitched.putpixel((x, y), color)
        
        if intensity >= 3:
            num_blocks = random.randint(1, intensity - 1)
            for _ in range(num_blocks):
                block_width = random.randint(5, 20)
                block_height = random.randint(5, 20)
                block_x = random.randint(0, width - block_width)
                block_y = random.randint(0, height - block_height)
                for bx in range(block_width):
                    for by in range(block_height):
                        if random.random() < 0.7:
                            px = min(block_x + bx, width - 1)
                            py = min(block_y + by, height - 1)
                            color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255), 255)
                            glitched.putpixel((px, py), color)
    
    if intensity >= 3:
        scanline_spacing = random.randint(8 - intensity, 15 - intensity)
        scanline_probability = 0.15 + (intensity - 3) * 0.05
        for y in range(0, height, scanline_spacing):
            if random.random() < scanline_probability:
                line_height = random.randint(1, 2)
                line_region = glitched.crop((0, y, width, y + line_height))
                brightness = 150 + (intensity - 3) * 25
                line_region = ImageChops.multiply(line_region, Image.new("RGBA", (width, line_height), (brightness, brightness, brightness, 255)))
                glitched.paste(line_region, (0, y))
                
    if intensity >= 4:
        blur_radius = 0.5 + (intensity - 4) * 0.5
        glitched = glitched.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        if intensity >= 5:
            if len(glitched.split()) >= 3:
                r, g, b = glitched.split()[:3]
                offset_x = random.randint(-3, 3)
                offset_y = random.randint(-3, 3)
                r_offset = ImageChops.offset(r, offset_x, offset_y)
                b_offset = ImageChops.offset(b, -offset_x, -offset_y)
                if len(glitched.split()) == 4:
                    a = glitched.split()[3]
                    glitched = Image.merge("RGBA", (r_offset, g, b_offset, a))
                else:
                    glitched = Image.merge("RGB", (r_offset, g, b_offset))
    return glitched


def resize_greek_image(greek_img: Image.Image, original_width: int, original_height: int) -> Image.Image:
    """调整字母图片大小"""
    greek_w, greek_h = greek_img.size
    min_original_dimension = min(original_width, original_height)
    target_size = int(min_original_dimension * 1.8)
    scale_ratio = target_size / max(greek_w, greek_h)
    new_width = int(greek_w * scale_ratio)
    new_height = int(greek_h * scale_ratio)
    if new_width < 200:
        new_width = 200
        new_height = int(greek_h * (200 / greek_w))
    return greek_img.resize((new_width, new_height), Image.Resampling.LANCZOS)


@register("astrbot_plugin_osugreek", "YakumoZn", "在图片中央贴上神秘4k希腊字母并添加色散效果的插件", "1.0.9", "https://github.com/YakumoZn/nonebot-plugin-osugreek")
class OsugreekPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 初始化图片目录和缓存目录
        self.image_dir = Path(__file__).parent / "images"
        self.cache_dir = Path("data/osugreek_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(exist_ok=True)

    async def cleanup_temp_file(self, file_path: Path, delay: float = 5.0):
        """延迟清理临时图片"""
        await asyncio.sleep(delay)
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass

    async def _fetch_reply_image(self, event: AstrMessageEvent, reply_id: str) -> str:
        """健壮的提取引用的图片 URL 函数，兼容各端数据结构"""
        if not hasattr(event, "bot") or not hasattr(event.bot, "api"):
            return ""
        
        reply_id_str = str(reply_id)
        # 组装请求参数列表，兼容不同的字段要求以及 int 强校验
        params_list = [{"message_id": reply_id_str}, {"id": reply_id_str}]
        if reply_id_str.isdigit():
            params_list.insert(1, {"message_id": int(reply_id_str)})
            params_list.append({"id": int(reply_id_str)})
            
        for params in params_list:
            try:
                ret = await event.bot.api.call_action("get_msg", **params)
                if not ret: continue
                
                # 兼容不同实现: 部分端返回 {data: {...}}, 部分直接返回 {...}
                msg_data = ret.get("data") if (isinstance(ret, dict) and "data" in ret and isinstance(ret["data"], dict)) else ret
                if not isinstance(msg_data, dict): continue
                
                msg_list = msg_data.get("message", msg_data.get("messages", []))
                if isinstance(msg_list, list):
                    for seg in msg_list:
                        if isinstance(seg, dict) and seg.get("type") in ("image", "mface"):
                            d = seg.get("data", {})
                            url = d.get("url") or d.get("file", "")
                            if url: return str(url)
            except Exception:
                pass
        return ""

    @filter.command("osugreek", alias={"希腊字母"})
    async def osugreek_cmd(self, event: AstrMessageEvent):
        # 提取文本参数
        msg_text = event.message_str.strip()
        parts = msg_text.split()
        if parts and (parts[0].endswith("osugreek") or parts[0].endswith("希腊字母")):
            parts = parts[1:]

        greek_name = parts[0] if len(parts) > 0 else ""
        
        # 获取帮助信息
        if greek_name == "help" or not greek_name:
            help_text = "用法：/osugreek <希腊字母名称> [色散强度] [故障强度]\n(支持引用图片/直接发图)\n参数说明: \n-色散强度: 范围[1,20], 不填则默认4。\n-故障强度: 范围[0,5], 不填则默认0。"
            yield event.plain_result(help_text)
            available = [f.stem for f in self.image_dir.glob("*.png")]
            available.sort()
            yield event.plain_result(f"可用的希腊字母名称有: {', '.join(available)}")
            return
        
        chromatic_intensity = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 4
        glitch_intensity = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

        # 名称转换映射 (支持大写 LN10 -> ln10, 中文希腊字母 γ -> gamma)
        greek_name = greek_name.lower()
        greek_name = GREEK_MAP.get(greek_name, greek_name)

        greek_img_path = self.image_dir / f"{greek_name}.png"
        if not greek_img_path.exists():
            available = [f.stem for f in self.image_dir.glob("*.png")]
            available.sort()
            yield event.plain_result(f"未找到字母图片 {greek_name}.png\n可用的有: {', '.join(available)}")
            return

        image_url = ""
        # 1. 优先查消息体自带的图片 (直接发图触发)
        for comp in event.message_obj.message:
            if isinstance(comp, Comp.Image):
                image_url = getattr(comp, "url", getattr(comp, "file", ""))
                if image_url: break

        # 2. 如果没带图，查找是否有引用 (Reply)
        if not image_url:
            for comp in event.message_obj.message:
                if isinstance(comp, Comp.Reply):
                    # a) 尝试从 AstrBot 封装的 origin/message 拿
                    for attr in ("message", "origin", "content"):
                        payload = getattr(comp, attr, None)
                        if isinstance(payload, list):
                            for seg in payload:
                                if isinstance(seg, Comp.Image):
                                    image_url = getattr(seg, "url", getattr(seg, "file", ""))
                                    if image_url: break
                        if image_url: break
                    
                    # b) 调用 API 获取被引用原文中的图片
                    if not image_url:
                        reply_id = getattr(comp, "id", getattr(comp, "message_id", None))
                        if not reply_id and hasattr(comp, "data") and isinstance(comp.data, dict):
                            reply_id = comp.data.get("id", comp.data.get("message_id"))
                            
                        if reply_id:
                            image_url = await self._fetch_reply_image(event, reply_id)
                if image_url: break

        if not image_url:
            yield event.plain_result("请发送一张图片或回复引用一张图片~")
            return

        # 下载及处理图片
        try:
            if str(image_url).startswith("http"):
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status != 200:
                            yield event.plain_result("图片下载失败，请重试")
                            return
                        img_data = await resp.read()
                original_img = Image.open(BytesIO(img_data)).convert("RGBA")
            else:
                original_img = Image.open(image_url).convert("RGBA")
            
            # 应用效果
            chromatic_img = add_chromatic_aberration(original_img, intensity=chromatic_intensity)
            if glitch_intensity > 0:
                chromatic_img = add_glitch_effect(chromatic_img, glitch_intensity)
            
            # 叠加希腊字母图片
            greek_img = Image.open(greek_img_path).convert("RGBA")
            greek_img = resize_greek_image(greek_img, original_img.width, original_img.height)
            orig_w, orig_h = chromatic_img.size
            greek_w, greek_h = greek_img.size
            x = (orig_w - greek_w) // 2
            y = (orig_h - greek_h) // 2
            
            combined = Image.new("RGBA", chromatic_img.size)
            combined.paste(chromatic_img, (0, 0))
            combined.paste(greek_img, (x, y), greek_img)
            
            # 生成临时文件并发送
            temp_filename = f"processed_{int(time.time() * 1000)}_{random.randint(1000, 9999)}.png"
            temp_output_path = self.cache_dir / temp_filename
            combined.save(temp_output_path, format="PNG")
            
            yield event.image_result(str(temp_output_path.absolute()))
            
            # 后台清理该临时文件
            asyncio.create_task(self.cleanup_temp_file(temp_output_path))
            
        except Exception as e:
            yield event.plain_result(f"图片处理失败: {str(e)}")
            logger.error(f"osugreek 图片处理失败: {e}")
