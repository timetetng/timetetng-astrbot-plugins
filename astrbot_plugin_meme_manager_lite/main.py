import json
import os
import random
import re
import shutil

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Image, Plain
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.star_tools import StarTools


@register(
    "astrbot_plugin_meme_manager_lite",
    "timetetng",
    "允许LLM在回答中使用表情包 轻量级！添加函数工具",
    "3.0",  # 版本号更新，体现架构变化
    "https://github.com/timetetng/astrbot_plugin_meme_manager_lite",
)
class StickerManagerLitePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        """
        根据文档，插件的独立配置会作为第二个参数 config 传入
        """
        super().__init__(context)
        self.config = config

        self.max_stickers_per_message = self.config.get("max_stickers_per_message", 1)
        self.clean_sticker_tags = self.config.get("clean_sticker_tags", True)

        probability = self.config.get("sticker_trigger_probability", 1.0)
        self.sticker_trigger_probability = max(0.0, min(1.0, probability))

        self.PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
        self.DATA_DIR = os.path.normpath(StarTools.get_data_dir())
        self.STICKERS_DIR = os.path.join(self.DATA_DIR, "memes")
        self.STICKERS_DATA_FILE = os.path.join(self.DATA_DIR, "memes_data.json")
        self.stickers_data: dict[str, str] = {}

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        self._init_default_config()
        self._load_stickers_data()
        logger.info("贴纸管理器插件已初始化 (v3.0 统一出口方案)")
        logger.info(f"当前表情触发率: {self.sticker_trigger_probability * 100}%")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("贴纸管理器插件已停止")

    def _init_default_config(self):
        """初始化默认配置，如果配置文件不存在则复制默认配置"""
        try:
            os.makedirs(self.DATA_DIR, exist_ok=True)

            if not os.path.exists(self.STICKERS_DATA_FILE):
                default_config_path = os.path.join(
                    self.PLUGIN_DIR, "default", "memes_data.json"
                )
                if os.path.exists(default_config_path):
                    shutil.copy2(default_config_path, self.STICKERS_DATA_FILE)
                else:
                    logger.error("默认配置文件不存在，创建空配置文件")
                    with open(self.STICKERS_DATA_FILE, "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)

            if not os.path.exists(self.STICKERS_DIR):
                default_stickers_dir = os.path.join(self.PLUGIN_DIR, "default", "memes")
                if os.path.exists(default_stickers_dir):
                    os.makedirs(self.STICKERS_DIR, exist_ok=True)
                    for sticker_name in os.listdir(default_stickers_dir):
                        default_sticker_dir = os.path.join(
                            default_stickers_dir, sticker_name
                        )
                        target_sticker_dir = os.path.join(
                            self.STICKERS_DIR, sticker_name
                        )

                        if os.path.isdir(default_sticker_dir) and not os.path.exists(
                            target_sticker_dir
                        ):
                            shutil.copytree(default_sticker_dir, target_sticker_dir)
                else:
                    logger.error("默认贴纸目录不存在")

        except Exception as e:
            logger.error(f"初始化默认配置失败: {e}")

    def _load_stickers_data(self):
        """加载贴纸数据"""
        try:
            if os.path.exists(self.STICKERS_DATA_FILE):
                with open(self.STICKERS_DATA_FILE, encoding="utf-8") as f:
                    self.stickers_data = json.load(f)
                logger.info(f"已加载 {len(self.stickers_data)} 个贴纸数据")
            else:
                logger.warning("贴纸数据文件不存在，使用空配置")
                self.stickers_data = {}
        except json.JSONDecodeError as e:
            logger.error(f"贴纸数据文件格式错误: {e}")
            self.stickers_data = {}
        except Exception as e:
            logger.error(f"加载贴纸数据失败: {e}")
            self.stickers_data = {}

    def _get_sticker_image_path(self, sticker_name: str) -> str | None:
        """获取贴纸图片路径，存在多张图片时随机选择"""
        sticker_dir = os.path.join(self.STICKERS_DIR, sticker_name)
        if os.path.exists(sticker_dir):
            try:
                image_files = []
                for file in os.listdir(sticker_dir):
                    if file.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".gif", ".webp")
                    ):
                        image_files.append(os.path.join(sticker_dir, file))
                if image_files:
                    return random.choice(image_files)
            except Exception as e:
                logger.error(f"读取贴纸目录失败: {e}")
        return None

    def _remove_sticker_tags(self, text: str) -> str:
        """移除文本中的贴纸标签"""
        # 这个正则表达式现在可以正确处理带属性的标签
        pattern = r"<sticker\s*[^>]*\/>"
        return re.sub(pattern, "", text).strip()

    def _generate_sticker_list(self) -> str:
        """生成贴纸清单"""
        sticker_list = []
        for name, description in self.stickers_data.items():
            sticker_list.append(f"- [{name}]：{description}")
        return "\n".join(sticker_list)

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        【已修改】
        向LLM注入新的、更精确的系统指令，教会它使用 force="true" 属性。
        """
        sticker_list = self._generate_sticker_list()

        instruction_prompt = f"""
[表情包系统指令]
你可以在对话中通过在文字回复里嵌入标签来使用表情包，让回复更生动。

1.  **日常对话（首选）**: 在你的文字回复中，通过嵌入 `<sticker name="贴纸名称"/>` 标签来附加表情。这用于自然地增添对话趣味。
    -   例如: "原来是这样呀<sticker name="oops"/>，我记错了。"

2.  **响应用户指令（特殊）**: 仅当用户最新的消息是“明确要求你发送表情”的指令时（例如“发个xx表情”、“来三个开心的表情”），你才应该使用带有 `force="true"` 属性的标签。
    -   例如，用户说“发个开心的表情”，你应该回复类似：“当然！<sticker name="happy" force="true"/>”
    -   例如，用户说“来三个哈气的表情”，你应该回复类似：“没问题！<sticker name="hachi" force="true"/><sticker name="hachi" force="true"/><sticker name="hachi" force="true"/>”

`force="true"` 属性将确保表情一定会被发送。请严格遵守上述规则。

「可用贴纸清单」:
{sticker_list}
"""
        req.system_prompt += f"\n\n{instruction_prompt}"

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        """处理 LLM 响应，解析贴纸标签并根据分数筛选"""
        event.set_extra("resp", resp)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """标签解析与最终消息链构建"""
        resp = event.get_extra("resp")
        if isinstance(resp, LLMResponse) and self.clean_sticker_tags:
            resp.completion_text = self._remove_sticker_tags(resp.completion_text)

        result = event.get_result()
        chain = result.chain
        new_chain = []
        for item in chain:
            if isinstance(item, Plain):
                # 使用新的处理逻辑
                components = await self._process_text_with_sticker(item.text)
                new_chain.extend(components)
            else:
                new_chain.append(item)
        result.chain = new_chain

    async def _process_text_with_sticker(self, text: str):
        """
        【已修改】
        处理包含sticker标签的文本，将其拆分成Plain和Image组件，并支持force属性。
        """
        components = []
        try:
            if not self.stickers_data:
                if text.strip():
                    components.append(Plain(text.strip()))
                return components

            pattern = r"(<sticker.*?\/>)"
            parts = re.split(pattern, text, flags=re.DOTALL)

            for i, part in enumerate(parts):
                if not part:
                    continue

                # 偶数索引是文本部分
                if i % 2 == 0:
                    if part.strip():
                        components.append(Plain(part.strip()))
                # 奇数索引是标签部分
                else:
                    tag = part
                    name_match = re.search(r'name="([^"]+)"', tag)
                    force_match = re.search(
                        r'force="true"', tag
                    )  # 检查是否存在force="true"

                    if name_match:
                        sticker_name = name_match.group(1)
                        image_path = self._get_sticker_image_path(sticker_name)

                        # 【核心判断逻辑】
                        # 如果标签包含 force="true"，或者随机概率命中
                        if image_path and (
                            force_match
                            or random.random() < self.sticker_trigger_probability
                        ):
                            components.append(Image.fromFileSystem(image_path))
                        # 如果是强制发送但图片不存在，可以给个提示
                        elif force_match and not image_path:
                            components.append(
                                Plain(f"（菲比没有找到“{sticker_name}”表情）")
                            )

        except Exception as e:
            logger.error(f"处理文本和sticker标签时出错: {e}")
            if text.strip():  # 发生错误时，确保原始文本不丢失
                components.append(Plain(text.strip()))

        return components

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        pass
