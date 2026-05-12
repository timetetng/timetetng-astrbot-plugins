# -*- coding: utf-8 -*-
import os
import random
import json
import re
from io import BytesIO
import asyncio
import datetime  # [新增] 导入datetime模块
from PIL import Image as ImageW
from PIL import ImageDraw, ImageFont
from typing import Optional, Dict, Set
from astrbot.api.all import *
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register

# 尝试安装依赖
try:
    os.system("python -m pip install pyspellchecker")
except:
    logger.info("Pyspellchecker not installed this time.")


try:
    from ..common.services import shared_services
except (ImportError, ModuleNotFoundError):
    logger.warning(
        "无法导入 'shared_services'。经济和昵称系统功能将不可用。请检查插件结构。"
    )
    shared_services = None


class WordleAPI:
    """
    提供查询 Wordle 游戏统计数据的 API。
    """

    def __init__(self, plugin_instance):
        self._plugin = plugin_instance

    async def get_user_stats(self, user_id: str) -> Optional[Dict[str, int]]:
        """
        获取用户的 Wordle 统计数据。
        返回: 包含 'win_count' 和 'dividend_count' 的字典，或在用户无记录时返回 None。
        """
        user_stats = self._plugin.stats.get(user_id)
        if user_stats:
            return {
                "win_count": user_stats.get("win_count", 0),
                "dividend_count": user_stats.get("dividend_count", 0),
            }
        return None


def re_spell_check(word: str, re_word_list: list):
    """支持正则表达式的自定义单词检查"""
    for each_word in re_word_list:
        if each_word and re.search(f"{each_word}", word):
            return True
    return False


class WordleGame:
    def __init__(self, answer: str):
        self.answer = answer.upper()
        self.length = len(answer)
        self.max_attempts = (self.length) * 2 - 1

        # --- 游戏状态追踪 ---
        self.guesses: list[str] = []  # 存储猜测的单词
        self.feedbacks: list[list[int]] = []  # 存储每次猜测的反馈
        self.history_letters: list[str] = []  # 存储所有猜过的字母
        self.history_words: list[str] = []  # 存储所有猜过的单词（用于查重）

        # --- 经济系统相关追踪 ---
        self.guess_users: list[str] = []  # 存储每次猜测的用户ID
        self.hint_used_count: int = 0  # 提示使用次数
        self.player_contributions: dict[str, int] = {}  # 玩家贡献度（新发现的绿块数量）
        self.correct_positions: set[int] = set()  # 已确定的正确位置（绿块）集合

        # --- 图像生成相关 ---
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.font_file = os.path.join(self.plugin_dir, "MinecraftAE.ttf")
        self._font = ImageFont.truetype(self.font_file, 40)

    async def gen_image(self) -> bytes:
        CELL_COLORS = {
            2: (106, 170, 100),
            1: (201, 180, 88),
            0: (120, 124, 126),
            -1: (211, 214, 218),
        }
        BACKGROUND_COLOR, TEXT_COLOR = (255, 255, 255), (255, 255, 255)
        CELL_SIZE, CELL_MARGIN, GRID_MARGIN = 60, 5, 5
        cell_stride = CELL_SIZE + CELL_MARGIN
        width = GRID_MARGIN * 2 + cell_stride * self.length - CELL_MARGIN
        height = GRID_MARGIN * 2 + cell_stride * self.max_attempts - CELL_MARGIN
        image = ImageW.new("RGB", (width, height), BACKGROUND_COLOR)
        draw = ImageDraw.Draw(image)
        for row in range(self.max_attempts):
            y = GRID_MARGIN + row * cell_stride
            for col in range(self.length):
                x = GRID_MARGIN + col * cell_stride
                if row < len(self.guesses) and col < len(self.guesses[row]):
                    letter, feedback_value = (
                        self.guesses[row][col].upper(),
                        self.feedbacks[row][col],
                    )
                    cell_color = CELL_COLORS[feedback_value]
                else:
                    letter, cell_color = "", CELL_COLORS[-1]
                draw.rectangle(
                    [x, y, x + CELL_SIZE, y + CELL_SIZE], fill=cell_color, outline=None
                )
                if letter:
                    text_bbox = draw.textbbox((0, 0), letter, font=self._font)
                    text_width, text_height = (
                        text_bbox[2] - text_bbox[0],
                        text_bbox[3] - text_bbox[1],
                    )
                    letter_x, letter_y = (
                        x + (CELL_SIZE - text_width) // 2 + 2.5,
                        y + (CELL_SIZE - text_height) // 2 + 1,
                    )
                    draw.text(
                        (letter_x, letter_y), letter, fill=TEXT_COLOR, font=self._font
                    )
        with BytesIO() as output:
            image.save(output, format="PNG")
            return output.getvalue()

    async def gen_image_hint(self, word) -> bytes:
        CELL_COLORS = {
            2: (106, 170, 100),
            1: (201, 180, 88),
            0: (120, 124, 126),
            -1: (211, 214, 218),
        }
        BACKGROUND_COLOR, TEXT_COLOR = (255, 255, 255), (255, 255, 255)
        CELL_SIZE, CELL_MARGIN, GRID_MARGIN = 60, 5, 5
        cell_stride = CELL_SIZE + CELL_MARGIN
        width = GRID_MARGIN * 2 + cell_stride * self.length - CELL_MARGIN
        height = GRID_MARGIN * 2 + cell_stride * 1 - CELL_MARGIN
        image = ImageW.new("RGB", (width, height), BACKGROUND_COLOR)
        draw = ImageDraw.Draw(image)
        for row in range(1):
            y = GRID_MARGIN + row * cell_stride
            for col in range(self.length):
                x = GRID_MARGIN + col * cell_stride
                cell_color = CELL_COLORS[-1] if word[col] == " " else CELL_COLORS[2]
                letter = word[col]
                draw.rectangle(
                    [x, y, x + CELL_SIZE, y + CELL_SIZE], fill=cell_color, outline=None
                )
                text_bbox = draw.textbbox((0, 0), letter, font=self._font)
                text_width, text_height = (
                    text_bbox[2] - text_bbox[0],
                    text_bbox[3] - text_bbox[1],
                )
                letter_x, letter_y = (
                    x + (CELL_SIZE - text_width) // 2 + 2.5,
                    y + (CELL_SIZE - text_height) // 2 + 1,
                )
                draw.text(
                    (letter_x, letter_y), letter, fill=TEXT_COLOR, font=self._font
                )
        with BytesIO() as output:
            image.save(output, format="PNG")
            return output.getvalue()

    async def is_guessed(self, word: str) -> bool:
        word = word.upper()
        if word in self.history_words:
            logger.info(f"{word}这个单词已经猜过了。")
            return True
        else:
            self.history_words.append(word)
            return False

    async def guess(self, word: str, user_id: str) -> bytes:
        word = word.upper()
        self.guesses.append(word)
        self.guess_users.append(user_id)
        for i in range(len(word)):
            if word.count(word[i]) > self.history_letters.count(word[i]):
                self.history_letters.append(word[i])
        feedback = [0] * self.length
        answer_char_counts: dict[str, int] = {}
        for i in range(self.length):
            if word[i] == self.answer[i]:
                feedback[i] = 2
            else:
                answer_char_counts[self.answer[i]] = (
                    answer_char_counts.get(self.answer[i], 0) + 1
                )
        for i in range(self.length):
            if feedback[i] != 2:
                char = word[i]
                if char in answer_char_counts and answer_char_counts[char] > 0:
                    feedback[i] = 1
                    answer_char_counts[char] -= 1
        self.feedbacks.append(feedback)
        newly_correct = 0
        for i, result in enumerate(feedback):
            if result == 2 and i not in self.correct_positions:
                newly_correct += 1
                self.correct_positions.add(i)
        if newly_correct > 0:
            self.player_contributions[user_id] = (
                self.player_contributions.get(user_id, 0) + newly_correct
            )
            logger.info(
                f"玩家 {user_id} 贡献了 {newly_correct} 个新线索。总贡献: {self.player_contributions}"
            )
        return await self.gen_image()

    async def hint(self) -> bytes:
        if not any(char in self.history_letters for char in self.answer):
            logger.info("用户还未猜出任何字母。")
            return False
        hint_word = ""
        tem1 = self.history_letters.copy()
        for char in self.answer:
            if char in tem1:
                hint_word += char
                tem1.remove(char)
            else:
                hint_word += " "
        return await self.gen_image_hint(hint_word.upper())

    @property
    def is_game_over(self):
        return self.guesses and len(self.guesses) >= self.max_attempts

    @property
    def is_won(self):
        return self.guesses and self.guesses[-1].upper() == self.answer


register(
    "astrbot_plugin_wordle_2_msg",
    "Raven95676, whzc, Gemini",
    "Wordle游戏（响应消息内容版），集成了经济和昵称系统",
    "3.3.0",
    "https://github.com/whzcc/astrbot_plugin_wordle_2_msg",
)


class PluginWordle(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.game_sessions: dict[str, WordleGame] = {}
        self.config = config
        self.economy_api = None
        self.nickname_api = None
        self.achievement_api = None
        # --- 统计数据初始化 ---
        self.stats_dir = "data/wordle_stats"
        os.makedirs(self.stats_dir, exist_ok=True)
        self.stats_file = os.path.join(self.stats_dir, "stats.json")
        self.stats: Dict[str, Dict[str, int]] = self._load_stats()

        # ---  加载用于验证的词库 ---
        self.validation_word_set: Set[str] = self._load_validation_words()

        # ---  注册 WordleAPI ---
        if shared_services is not None:
            self.api = WordleAPI(self)
            shared_services["wordle_api"] = self.api
            logger.info("Wordle 统计服务(WordleAPI)已成功注册到全局服务。")

        asyncio.create_task(self._async_init())

    # --- 统计数据读写方法 ---
    def _load_stats(self) -> Dict[str, Dict[str, int]]:
        """从文件加载统计数据"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载 Wordle 统计数据失败: {e}")
        return {}

    def _save_stats(self):
        """保存统计数据到文件"""
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=4)
        except IOError as e:
            logger.error(f"保存 Wordle 统计数据失败: {e}")

    # --- 加载 all_wordlist 中的所有单词用于验证 ---
    def _load_validation_words(self) -> Set[str]:
        """从 all_wordlist 文件夹加载所有单词到一个集合中用于快速验证。"""
        validation_set = set()
        try:
            wordlist_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "all_wordlist"
            )
            if not os.path.exists(wordlist_path):
                logger.error("all_wordlist 文件夹不存在，单词验证功能将无法使用。")
                return validation_set

            for word_file in os.listdir(wordlist_path):
                if not word_file.endswith(".json"):
                    continue

                with open(
                    os.path.join(wordlist_path, word_file), "r", encoding="utf-8"
                ) as f:
                    try:
                        word_dict = json.load(f)
                        # 将所有单词（字典的键）转换为大写并添加到集合中
                        validation_set.update(
                            [word.upper() for word in word_dict.keys()]
                        )
                    except json.JSONDecodeError:
                        logger.error(f"解析 all_wordlist 中的JSON文件失败: {word_file}")

            logger.info(f"成功加载 {len(validation_set)} 个单词用于验证。")
            return validation_set

        except Exception as e:
            logger.error(f"加载 all_wordlist 时发生未知错误: {e!s}")
            return validation_set

    # --- 检查单词是否在验证词库中 ---
    async def is_valid_word(self, word: str) -> bool:
        """检查一个单词是否存在于 all_wordlist 词库中。"""
        return word.upper() in self.validation_word_set

    async def _async_init(self):
        """异步初始化方法，用于加载依赖API。"""
        logger.info("Wordle插件: 正在等待依赖API加载...")
        timeout_seconds = 30
        start_time = asyncio.get_event_loop().time()

        while self.economy_api is None or self.nickname_api is None:
            if shared_services:
                self.economy_api = shared_services.get("economy_api")
                self.nickname_api = shared_services.get("nickname_api")
                self.achievement_api = shared_services.get("achievement_api")
            if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                logger.warning("Wordle插件: 等待依赖API超时，部分功能将受限！")
                break
            await asyncio.sleep(1)  # 每隔1秒重试一次

        if self.economy_api:
            logger.info("Wordle插件：已成功连接到经济系统API。")
        else:
            logger.error(
                "Wordle插件：金币奖励已启用，但未能获取经济系统API！奖励功能将无法使用。"
            )

        if self.nickname_api:
            logger.info("Wordle插件：已成功连接到昵称系统API。")
        else:
            logger.warning("Wordle插件：未能获取昵称系统API，将无法显示自定义昵称。")

        if self.achievement_api:
            logger.info("Wordle插件：已成功连接到成就系统API。")
        else:
            logger.warning("Wordle插件：未能获取成就系统API，成就解锁功能将不可用。")

    @property
    def custom_word_list(self):
        return self.config.get("custom_word_list", "").split(";")

    @staticmethod
    async def get_answer(length):
        try:
            wordlist_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "wordlist"
            )
            if not os.path.exists(wordlist_path):
                logger.error("词表文件夹不存在")
                return None, None

            #  创建一个列表来存储所有符合条件的单词和释义，而不是使用会覆盖的字典
            eligible_words = []

            word_file_list = os.listdir(wordlist_path)
            for word_file in word_file_list:
                # 确保只读取 .json 文件
                if not word_file.endswith(".json"):
                    continue

                with open(
                    os.path.join(wordlist_path, word_file), "r", encoding="utf-8"
                ) as f:
                    try:
                        full_dict = json.load(f)
                        for word, data in full_dict.items():
                            # 检查单词长度是否符合要求
                            if len(word) == length:
                                explanation = data.get("中释", "暂无释义")
                                eligible_words.append((word.upper(), explanation))
                    except json.JSONDecodeError:
                        logger.error(f"解析JSON文件失败: {word_file}")

            if not eligible_words:
                logger.warning(f"在所有词表中都找不到长度为 {length} 的单词。")
                return None, None

            # 从所有符合条件的单词中随机选择一个
            word, explanation = random.choice(eligible_words)

            logger.warning(f"选择了 {word} 单词，长度 {length}，释义为 {explanation}")
            return word, explanation

        except Exception as e:
            logger.error(f"加载词表时发生未知错误: {e!s}")
            return None, None

    # [新增] 统一的奖励发放函数，处理每日上限
    async def _award_coins(
        self, user_id: str, potential_amount: int, reason: str
    ) -> (int, str):
        """
        处理金币奖励，包括每日上限检查。
        返回 (实际奖励数额, 附加消息)
        """
        if potential_amount <= 0 or not self.economy_api:
            return 0, ""

        daily_limit = self.config.get("daily_reward_limit", 5000)
        if daily_limit <= 0:  # 0或负数表示无限制
            success = await self.economy_api.add_coins(
                user_id=user_id, amount=potential_amount, reason=reason
            )
            if not success:
                return 0, "（但金币发放失败了...）"
            return potential_amount, ""

        today_str = datetime.date.today().isoformat()
        # 安全地初始化用户统计数据
        user_stats = self.stats.setdefault(
            user_id, {"win_count": 0, "dividend_count": 0, "daily_earnings": {}}
        )
        daily_earnings_dict = user_stats.setdefault("daily_earnings", {})

        current_earnings = daily_earnings_dict.get(today_str, 0)
        remaining_allowance = daily_limit - current_earnings

        if remaining_allowance <= 0:
            return 0, f"（已达到今日 {daily_limit} 金币上限）"

        actual_amount = min(potential_amount, remaining_allowance)

        success = await self.economy_api.add_coins(
            user_id=user_id, amount=actual_amount, reason=reason
        )

        if success:
            daily_earnings_dict[today_str] = current_earnings + actual_amount
            message = (
                f"（已达到今日 {daily_limit} 金币上限）"
                if actual_amount < potential_amount
                else ""
            )
            return actual_amount, message
        else:
            return 0, "（但金币发放失败了...）"

    async def _handle_win(
        self, event: AstrMessageEvent, game: WordleGame, explanation: str
    ):
        winner_id = game.guess_users[-1]

        # --- 成就触发：第一次就猜中 ---
        if self.achievement_api and len(game.guesses) == 1 and game.length >= 5:
            # 调用API解锁成就，并传入event以便发送通知
            was_unlocked = await self.achievement_api.unlock_achievement(
                user_id=winner_id, achievement_id="wordle_first_try_win", event=event
            )
            if was_unlocked:
                logger.info(
                    f"用户 {winner_id} 通过猜单词一击制胜解锁了成就 [wordle_first_try_win]。"
                )

        # --- 更新胜利者统计数据 ---
        self.stats.setdefault(
            winner_id, {"win_count": 0, "dividend_count": 0, "daily_earnings": {}}
        )
        self.stats[winner_id]["win_count"] += 1
        # --- 奖励计算逻辑 ---
        base_reward = self.config.get("base_reward", 500)
        length_bonus = (game.length - 5) * self.config.get("length_multiplier", 100)
        attempts_ratio = (game.max_attempts - len(game.guesses)) / game.max_attempts
        speed_bonus = attempts_ratio * self.config.get("speed_bonus_max", 1000)
        pre_penalty_reward = base_reward + length_bonus + speed_bonus
        penalty_rate = game.hint_used_count * self.config.get(
            "hint_penalty_percentage", 0.2
        )
        potential_winner_reward = max(
            0, int(pre_penalty_reward * (1 - min(penalty_rate, 0.9)))
        )

        reward_messages = []

        # --- 胜利者奖励发放 ---
        if self.config.get("reward_enabled", False):
            awarded_amount, limit_msg = await self._award_coins(
                winner_id, potential_winner_reward, "Wordle 游戏胜利"
            )
            if awarded_amount > 0:
                reward_messages.append(
                    f"恭喜你猜对了！获得 {awarded_amount} 金币！{limit_msg}"
                )
            else:
                reward_messages.append(f"恭喜你猜对了！{limit_msg}")
        else:
            reward_messages.append("恭喜你猜对了！")

        # --- 分红逻辑 ---
        if (
            self.config.get("clue_dividend_enabled", True)
            and game.player_contributions
            and self.economy_api
        ):
            top_contributor_id = max(
                game.player_contributions, key=game.player_contributions.get
            )
            top_contribution = game.player_contributions[top_contributor_id]

            if top_contributor_id != winner_id and top_contribution > 0:
                potential_dividend = int(
                    potential_winner_reward
                    * self.config.get("clue_dividend_percentage", 0.15)
                )
                if potential_dividend > 0:
                    awarded_dividend, dividend_limit_msg = await self._award_coins(
                        top_contributor_id, potential_dividend, "Wordle 最佳线索分红"
                    )

                    if awarded_dividend > 0:
                        self.stats.setdefault(
                            top_contributor_id,
                            {"win_count": 0, "dividend_count": 0, "daily_earnings": {}},
                        )
                        self.stats[top_contributor_id]["dividend_count"] += 1

                        # --- 获取贡献者昵称逻辑 ---
                        display_name = top_contributor_id
                        if self.nickname_api:
                            custom_name = await self.nickname_api.get_nickname(
                                top_contributor_id
                            )
                            if custom_name:
                                display_name = custom_name

                        if display_name == top_contributor_id and self.economy_api:
                            profile = await self.economy_api.get_user_profile(
                                top_contributor_id
                            )
                            if profile and profile.get("nickname"):
                                display_name = profile["nickname"]

                        if (
                            display_name == top_contributor_id
                            and event.get_group_id()
                            and event.get_platform_name() == "aiocqhttp"
                        ):
                            try:
                                from astrbot.api.platform import AiocqhttpAdapter

                                platform = self.context.get_platform(
                                    filter.PlatformAdapterType.AIOCQHTTP
                                )
                                if platform and isinstance(platform, AiocqhttpAdapter):
                                    client = platform.get_client()
                                    member_info = await client.api.call_action(
                                        "get_group_member_info",
                                        group_id=int(event.get_group_id()),
                                        user_id=int(top_contributor_id),
                                    )
                                    if member_info and member_info.get("card"):
                                        display_name = member_info["card"]
                                    elif member_info and member_info.get("nickname"):
                                        display_name = member_info["nickname"]
                            except Exception as e:
                                logger.warning(
                                    f"Wordle: 调用平台API获取用户({top_contributor_id})昵称失败: {e}"
                                )

                        reward_messages.append(
                            f"\n特别感谢玩家【{display_name}】提供的关键线索，获得 {awarded_dividend} 金币分红！{dividend_limit_msg}"
                        )

        # --- 游戏结束后保存一次统计数据 ---
        self._save_stats()

        return f"“{game.answer}”的意思是“{explanation}”。\n" + "".join(reward_messages)

    @event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        msg, session_id = (
            event.get_message_str().strip().lower(),
            event.unified_msg_origin,
        )
        if msg in ["猜单词结束", "结束猜单词", "退出猜单词", "猜单词退出"]:
            if session_id in self.game_sessions:
                game, _ = self.game_sessions[session_id]
                del self.game_sessions[session_id]
                yield event.plain_result(f"猜单词已结束，正确答案是 {game.answer}。")
            else:
                yield event.plain_result("游戏还没开始呢！")
            return
        if msg in ["猜单词提示", "提示猜单词"]:
            if session_id in self.game_sessions:
                game, _ = self.game_sessions[session_id]
                game.hint_used_count += 1
                image_result_hint = await game.hint()
                if image_result_hint:
                    filename = (
                        f"{session_id.replace(':', '')}_hint_{game.hint_used_count}.png"
                    )
                    temp_img_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), filename
                    )
                    try:
                        with open(temp_img_path, "wb") as f:
                            f.write(image_result_hint)
                        chain = [
                            Image.fromFileSystem(temp_img_path),
                            Plain("这是你已经猜出的字母。"),
                        ]
                        yield event.chain_result(chain)
                    finally:
                        if os.path.exists(temp_img_path):
                            os.remove(temp_img_path)
                else:
                    i = random.randint(0, len(game.answer) - 1)
                    yield event.plain_result(
                        f"提示：第 {i + 1} 个字母是 {game.answer[i]}。"
                    )
            else:
                yield event.plain_result("游戏还没开始，输入“猜单词”来开始游戏吧！")
            return
        if msg.startswith("猜单词") or msg.startswith("/猜单词"):
            parts = msg.replace("/猜单词", "猜单词").split()
            length_str = parts[1] if len(parts) > 1 else "5"
            try:
                length = int(length_str)
                if not 3 <= length <= 10:
                    yield event.plain_result("单词长度必须在3到10之间哦！")
                    return
            except ValueError:
                yield event.plain_result("请输入有效的单词长度数字！")
                return
            if session_id in self.game_sessions:
                del self.game_sessions[session_id]
            answer, explanation = await self.get_answer(length)
            if not answer:
                yield event.plain_result(
                    random.choice(
                        [
                            f"{length}个字母的单词我找不到...",
                            f"{length}个字母的单词太稀有啦！",
                        ]
                    )
                )
            else:
                self.game_sessions[session_id] = (WordleGame(answer), explanation)
                logger.debug(f"答案是：{answer}")
                yield event.plain_result(f"游戏开始！请输入长度为 {length} 的单词。")
            return
        if session_id in self.game_sessions:
            game, explanation = self.game_sessions[session_id]
            if not (msg.isascii() and msg.isalpha()):
                return
            if len(msg) != game.length:
                yield event.plain_result(
                    f"不太对哦，要输入{game.length}个字母的英语单词🔡。\n输入“猜单词结束”可结束游戏。"
                )
                return

            # --- [新增] 单词有效性检查 ---
            if not await self.is_valid_word(msg):
                yield event.plain_result(
                    "这好像不是一个有效的英文单词哦，换一个试试吧！🤔"
                )
                return

            if await game.is_guessed(msg):
                yield event.plain_result("这个单词已经猜过了！")
                return
            image_result = await game.guess(msg, event.get_sender_id())
            game_status = ""
            if game.is_won:
                if self.config.get("reward_enabled", False) and self.economy_api:
                    game_status = await self._handle_win(event, game, explanation)
                else:
                    game_status = (
                        f"恭喜你猜对了！“{game.answer}”的意思是“{explanation}”。"
                    )
                del self.game_sessions[session_id]
            elif game.is_game_over:
                game_status = (
                    f"机会用完啦！正确答案是“{game.answer}”，意思是“{explanation}”。"
                )
                del self.game_sessions[session_id]
            else:
                game_status = f"已猜测 {len(game.guesses)}/{game.max_attempts} 次。"
            filename = f"{session_id.replace(':', '')}_game_{len(game.guesses)}.png"
            temp_img_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), filename
            )
            try:
                with open(temp_img_path, "wb") as f:
                    f.write(image_result)
                chain = [Image.fromFileSystem(temp_img_path), Plain(game_status)]
                yield event.chain_result(chain)
            finally:
                if os.path.exists(temp_img_path):
                    os.remove(temp_img_path)
