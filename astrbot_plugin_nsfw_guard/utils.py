import time
from pathlib import Path
from astrbot.api import logger

# 尝试导入绘图库
try:
    from PIL import Image, ImageDraw, ImageFont
    import jieba
    from wordcloud import WordCloud
    LIBS_LOADED = True
except ImportError:
    LIBS_LOADED = False

class Visualizer:
    def __init__(self, data_dir: Path, config: dict):
        self.data_dir = data_dir
        self.config = config
        self.font_path = self._get_font_path()

    def _get_font_path(self) -> str:
        font_name = self.config.get("wordcloud_font_path", "simhei.ttf")
        font_path = Path(font_name)
        if not font_path.is_absolute():
            font_path = self.data_dir / font_name
        
        if not font_path.exists():
            # 尝试回退到系统字体或报错，这里简单处理
            return str(font_path) 
        return str(font_path)

    def text_to_image(self, text: str) -> str:
        """将文本转换为图片并保存，返回路径"""
        if not LIBS_LOADED:
            raise RuntimeError("缺少 PIL 库，无法生成图片")

        # 简单的绘图逻辑
        font_size = 20
        padding = 20
        line_spacing = 5
        
        try:
            font = ImageFont.truetype(self.font_path, font_size)
        except:
            font = ImageFont.load_default()

        lines = text.split('\n')
        max_width = 0
        total_height = 2 * padding
        
        # 计算尺寸
        for line in lines:
            bbox = font.getbbox(line)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            max_width = max(max_width, w)
            total_height += h + line_spacing

        img_width = max_width + 2 * padding
        img_height = total_height
        
        image = Image.new('RGB', (img_width, img_height), color='white')
        draw = ImageDraw.Draw(image)
        
        y = padding
        for line in lines:
            draw.text((padding, y), line, font=font, fill='black')
            bbox = font.getbbox(line)
            h = bbox[3] - bbox[1]
            y += h + line_spacing

        output_path = self.data_dir / f"stats_{int(time.time())}.png"
        image.save(output_path)
        return str(output_path)

    def generate_wordcloud(self, messages: list[str]) -> str:
        """根据违规消息列表生成词云"""
        if not LIBS_LOADED:
            raise RuntimeError("缺少 jieba 或 wordcloud 库")
            
        if not messages:
            raise ValueError("没有足够的数据生成词云")

        text = " ".join(messages)
        
        # 分词
        word_list = jieba.lcut(text)
        
        # 停用词
        stopwords = {
            '内容', '相关', '可能', '涉及', '包含', '存在', '以及', '一个', '用户', '输入', '信息', '描述',
            '什么', '怎么', '如果', '但是', '为什么', '这个', '那个', 'bot', 'Bot', '机器人', 'http', 'https'
        }
        filtered_words = [w for w in word_list if len(w) > 1 and w not in stopwords]

        if not filtered_words:
            raise ValueError("过滤后关键词为空")

        wc = WordCloud(
            font_path=self.font_path,
            width=1000,
            height=700,
            background_color='white',
            max_words=100,
            collocations=False
        )
        
        wc.generate(" ".join(filtered_words))
        output_path = self.data_dir / f"nsfw_wordcloud_{int(time.time())}.png"
        wc.to_file(str(output_path))
        
        return str(output_path)
