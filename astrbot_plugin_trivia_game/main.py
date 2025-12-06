# ... (imports保持不变)
import asyncio
import collections
import difflib
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

try:
    from ..common.services import shared_services
except ImportError:
    logger.warning("无法导入 shared_services，经济功能将不可用。")
    shared_services = {}

class GameState:
    # ... (此类内容不变)
    def __init__(self, question_data: dict[str, Any], timeout_task: asyncio.Task):
        self.question_data = question_data
        self.hints_given = 0
        self.timeout_task = timeout_task
        self.is_active = True
        self.wrong_guesses = 0
        self.participants = set()

@register(
    "TriviaGame",
    "Gemini",
    "一个调用LLM出题的趣味猜题插件",
    "3.3.0", # 版本号升级
    ""
)

class TriviaGamePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        general_config = self.config.get("general", {})
        self.GAME_TIMEOUT_SECONDS = general_config.get("timeout_seconds", 60)
        self.LLM_TIMEOUT_SECONDS = general_config.get("llm_timeout_seconds", 30)
        self.LLM_MAX_RETRIES = general_config.get("llm_max_retries", 2) # 此参数在旧逻辑中使用，可保留或移除

        content_config = self.config.get("content", {})
        self.TOPICS = [topic.strip() for topic in content_config.get("topics", "").split(",") if topic.strip()]
        self.SEED_WORDS = [word.strip() for word in content_config.get("seed_words", "").split(",") if word.strip()]

        llm_params_config = self.config.get("llm_parameters", {})
        self.llm_temperature = llm_params_config.get("temperature", 0.8)
        self.llm_top_p = llm_params_config.get("top_p", 0.95)

        self.game_states: dict[str, GameState] = {}
        self.economy_api = None
        self.daily_rewards: dict[str, dict[str, Any]] = {}

        # 用户统计数据
        self.stats_file = Path("data/trivia_game_stats.json")
        self.user_stats: dict[str, dict[str, Any]] = {}
        self._load_stats()

        # 主题选择历史
        history_len = content_config.get("topic_history_length", 5)
        self.topic_history = collections.deque(maxlen=history_len)

        # 防止并发生成的锁
        self.generating_groups = set()

        # --- 新增：历史答案库 ---
        self.history_file = Path("data/trivia_answer_history.json")
        # 结构: {"历史": [["秦始皇", "嬴政"], ["滑铁卢战役"]], "科学": [["光合作用"]]}
        self.answer_history: dict[str, list[list[str]]] = {}
        self._load_history()

        if shared_services:
            asyncio.create_task(self.initialize_apis())

    # --- 新增/修改：历史答案库的加载和保存 ---
    def _load_history(self):
        try:
            if self.history_file.exists():
                with open(self.history_file, encoding="utf-8") as f:
                    self.answer_history = json.load(f)
                logger.info("已成功加载历史答案库。")
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"加载历史答案库失败: {e}")
            self.answer_history = {}

    async def _save_history(self):
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self.history_file.write_text, json.dumps(self.answer_history, ensure_ascii=False, indent=4), encoding="utf-8")
        except OSError as e:
            logger.error(f"保存历史答案库失败: {e}")

    # --- 新增：精准的核心答案重复检查函数 ---
    def _is_answer_duplicate(self, new_answers: list, topic: str) -> bool:
        """通过比较答案列表，检查题目核心内容是否重复"""
        if not new_answers or topic not in self.answer_history:
            return False

        # 将新答案列表转为集合，方便快速比较
        new_answers_set = set(str(a).lower().strip() for a in new_answers)

        for old_answers_list in self.answer_history[topic]:
            old_answers_set = set(str(a).lower().strip() for a in old_answers_list)
            # 只要新旧答案有任何一个交集，就认为是重复题目
            if not new_answers_set.isdisjoint(old_answers_set):
                logger.warning(f"检测到重复的核心答案。新: {new_answers_set} | 旧: {old_answers_set}")
                return True
        return False

    def _load_stats(self):
        try:
            if self.stats_file.exists():
                with open(self.stats_file, encoding="utf-8") as f:
                    self.user_stats = json.load(f)
                logger.info("已成功加载猜题游戏玩家统计数据。")
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"加载猜题统计数据失败: {e}")
            self.user_stats = {}

    async def _save_stats(self):
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self.stats_file.write_text, json.dumps(self.user_stats, ensure_ascii=False, indent=4), encoding="utf-8")
        except OSError as e:
            logger.error(f"保存猜题统计数据失败: {e}")

    async def wait_for_api(self, api_name: str, timeout: int = 30):
        logger.info(f"TriviaGame 正在等待 {api_name} 加载...")
        start_time = asyncio.get_event_loop().time()
        while True:
            api_instance = shared_services.get(api_name)
            if api_instance:
                logger.info(f"TriviaGame 已成功加载 {api_name}。")
                return api_instance
            if asyncio.get_event_loop().time() - start_time > timeout:
                logger.warning(f"TriviaGame 等待 {api_name} 超时，相关功能将受限！")
                return None
            await asyncio.sleep(1)

    async def initialize_apis(self):
        self.economy_api = await self.wait_for_api("economy_api")
        if self.economy_api:
            logger.info("TriviaGame 经济系统接口已就绪，奖励功能已启用。")
        else:
            logger.error("TriviaGame 未能加载经济系统接口，奖励功能将无法使用！")

    async def terminate(self):
        for group_id, state in list(self.game_states.items()):
            if state.timeout_task and not state.timeout_task.done():
                state.timeout_task.cancel()
        self.game_states.clear()
        logger.info("所有猜题游戏状态已清理。")

    async def _secondary_llm_check(self, state: GameState, user_answer: str) -> bool:
        validation_config = self.config.get("validation", {})
        provider_id = validation_config.get("secondary_llm_provider_id")

        if not validation_config.get("use_secondary_llm") or not provider_id:
            return False

        provider = self.context.get_provider_by_id(provider_id)
        if not provider:
            logger.warning(f"未找到用于二次校验的LLM提供商: {provider_id}")
            return False

        prompt = f"""
你是一位知识问答比赛的最终裁判，你需要对一个有争议的答案做出公正的裁决。请严格按照规则判断。
【规则】
1.  你的回答只能是单个词：“正确”或“错误”。
2.  不要进行任何解释或说明。
【比赛信息】
-   问题描述：{state.question_data['题目描述']}
-   已知的标准答案列表：{state.question_data['题目可能的答案']}
-   选手给出的答案：{user_answer}
【你的裁决】
请判断选手的答案是否可以被认为是正确的（即使它不在标准答案列表中，但可能是同义词、别称或正确的另一种表述）。
你的回答：
"""
        try:
            timeout = validation_config.get("secondary_llm_timeout", 10)
            response = await asyncio.wait_for(provider.text_chat(prompt), timeout=timeout)
            response_text = response.completion_text.strip()
            logger.info(f"LLM二次校验结果: {response_text}")
            return "正确" in response_text
        except asyncio.TimeoutError:
            logger.warning("LLM二次校验超时。")
            return False
        except Exception as e:
            logger.error(f"LLM二次校验时发生错误: {e}")
            return False

    @filter.on_llm_request()
    async def check_answer_hook(self, event: AstrMessageEvent, req: ProviderRequest):
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_states or not self.game_states[group_id].is_active:
            return

        state = self.game_states[group_id]
        user_answer_text = event.message_str.strip()
        if not user_answer_text: return

        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        if user_id not in self.user_stats:
            self.user_stats[user_id] = {"correct": 0, "attempts": 0, "name": user_name}

        self.user_stats[user_id]["name"] = user_name
        if user_id not in state.participants:
            self.user_stats[user_id]["attempts"] += 1
            state.participants.add(user_id)
            await self._save_stats()

        correct_answers = [str(a).lower().strip() for a in state.question_data["题目可能的答案"]]
        user_answer_lower = user_answer_text.lower()

        is_correct = False
        if user_answer_lower in correct_answers:
            is_correct = True
        else:
            sim_threshold = self.config.get("validation", {}).get("similarity_threshold", 0.85)
            for correct_answer in correct_answers:
                if difflib.SequenceMatcher(None, user_answer_lower, correct_answer).ratio() >= sim_threshold:
                    is_correct = True
                    break
        if not is_correct:
            is_correct = await self._secondary_llm_check(state, user_answer_text)

        if is_correct:
            self.user_stats[user_id]["correct"] += 1
            await self._save_stats()

            reward_message = ""
            if self.economy_api:
                rewards_config = self.config.get("rewards", {})
                base_reward = rewards_config.get("base_reward", 50)
                diff_mults = rewards_config.get("difficulty_multipliers", {})

                difficulty = state.question_data.get("题目难度", "普通")
                difficulty_multiplier = {
                    "简单": diff_mults.get("simple", 1.0),
                    "普通": diff_mults.get("normal", 1.3),
                    "困难": diff_mults.get("hard", 2.0)
                }.get(difficulty, 1.0)

                penalty_per_guess = rewards_config.get("penalty_per_wrong_guess", 0.1)
                max_penalty = rewards_config.get("max_wrong_guess_penalty", 0.5)

                penalty_multiplier = max(1.0 - max_penalty, 1.0 - (state.wrong_guesses * penalty_per_guess))

                final_reward = int(base_reward * difficulty_multiplier * penalty_multiplier * (0.5 ** state.hints_given))

                daily_cap = rewards_config.get("daily_reward_cap", 1000)
                today = datetime.now().strftime("%Y-%m-%d")
                user_daily_data = self.daily_rewards.get(user_id, {"date": "", "total": 0})

                if user_daily_data["date"] != today:
                    user_daily_data["date"], user_daily_data["total"] = today, 0

                remaining_limit = daily_cap - user_daily_data["total"]
                actual_reward = min(final_reward, remaining_limit)

                if actual_reward > 0:
                    await self.economy_api.add_coins(user_id, actual_reward, "猜题游戏胜利")
                    user_daily_data["total"] += actual_reward
                    self.daily_rewards[user_id] = user_daily_data
                    reward_message = f"恭喜获得 {actual_reward} 金币！"
                else:
                    reward_message = "今日奖励已达上限啦！"

            if state.timeout_task and not state.timeout_task.done():
                state.timeout_task.cancel()

            matched_answer = ""
            highest_sim = 0.0
            for ans in state.question_data["题目可能的答案"]:
                sim = difflib.SequenceMatcher(None, user_answer_lower, str(ans).lower().strip()).ratio()
                if sim > highest_sim:
                    highest_sim = sim
                    matched_answer = ans

            success_message = event.plain_result(
                f"🎉 恭喜 @{user_name} 回答正确！\n"
                f"💡 正确答案就是：【{matched_answer or correct_answers[0]}】\n"
                f"😎 {reward_message}"
            )

            await event.send(success_message)
            del self.game_states[group_id]
            event.stop_event()

        else:
            state.wrong_guesses += 1
            error_message = event.plain_result(f"🤔 “{user_answer_text}”似乎不是正确答案哦，再想想吧！")
            await event.send(error_message)
            event.stop_event()

    @filter.command("猜题", alias={"出题"})
    async def start_game(self, event: AstrMessageEvent, difficulty: str = None):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("这个游戏只能在群聊里玩哦～")
            return

        if group_id in self.game_states and self.game_states[group_id].is_active:
            yield event.plain_result("当前群里已经有一个猜题游戏正在进行啦！")
            return

        if group_id in self.generating_groups:
            yield event.plain_result("别着急，上一题还没想好呢！请稍后再试。")
            return

        try:
            self.generating_groups.add(group_id)

            VALID_DIFFICULTIES = ["简单", "普通", "困难"]
            selected_difficulty = ""

            if difficulty:
                if difficulty in VALID_DIFFICULTIES:
                    selected_difficulty = difficulty
                    yield event.plain_result(f"已收到您的请求，正在准备一道【{difficulty}】难度的题目...")
                else:
                    error_msg = f"'{difficulty}' 不是一个有效的难度选项。\n请从以下选项中选择：{', '.join(VALID_DIFFICULTIES)}"
                    yield event.plain_result(error_msg)
                    return
            else:
                yield event.plain_result("正在随机挑选领域和难度，请稍等...")
                diff_weights = [0.3, 0.5, 0.2]
                selected_difficulty = random.choices(VALID_DIFFICULTIES, weights=diff_weights, k=1)[0]

            if not self.TOPICS:
                yield event.plain_result("错误：管理员尚未配置任何出题领域。")
                return

            weights = [
                (0.2 * (list(self.topic_history).index(topic) + 1)) if topic in self.topic_history else 1.0
                for topic in self.TOPICS
            ]
            selected_topic = random.choices(self.TOPICS, weights=weights, k=1)[0]
            self.topic_history.append(selected_topic)

            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            if not provider:
                yield event.plain_result("哎呀，获取大语言模型失败了，暂时无法出题。")
                return

            selected_seed_word = random.choice(self.SEED_WORDS) if self.SEED_WORDS else "普通"

            # --- 轻量级前置规避 ---
            avoid_answers_prompt = ""
            if selected_topic in self.answer_history and self.answer_history[selected_topic]:
                sample_answers = random.sample(self.answer_history[selected_topic], k=min(5, len(self.answer_history[selected_topic])))
                avoid_keywords = {item for sublist in sample_answers for item in sublist}
                avoid_answers_prompt = f"5.  请尽量避免出核心答案是关于 '{'、'.join(avoid_keywords)}' 的题目。"

            # --- 第一次尝试的 Prompt ---
            prompt_attempt_1 = f"""
请你扮演一个知识渊博的出题人，为我设计一个题目。
# 核心要求
1.  题目领域必须是关于：【{selected_topic}】。
2.  题目难度必须是：【{selected_difficulty}】。
3.  请围绕【{selected_seed_word}】这个角度或风格来出题，确保题目新颖。
4.  “题目描述”字段的内容，最后必须以一个明确的疑问句结尾。
{avoid_answers_prompt}
# JSON格式定义
{{
  "题目描述": "请用简要描述问题，并确保描述的最后是一个明确的疑问句（例如：‘这是什么现象？’、‘这位人物是谁？’）。",
  "题目可能的答案": ["答案1", "答案2", "..."], 
  "题目难度": "这里必须填写我为你指定的难度：【{selected_difficulty}】。",
  "答案提示": ["关于答案的第一个提示", "第二个更明显的提示", "最后一个决定性的提示"]
}}
# “题目可能的答案”字段填写指南
请在这个字段中，尽可能全面地列出所有可能的正确答案。
现在，请严格按照以上所有要求出题。
"""

            question_data = None
            raw_llm_text = ""

            # --- 第一次生成尝试 ---
            try:
                logger.info(f"为群组 {group_id} 首次生成题目... 领域: {selected_topic}, 难度: {selected_difficulty}")
                llm_resp = await asyncio.wait_for(
                    provider.text_chat(prompt_attempt_1, temperature=self.llm_temperature, top_p=self.llm_top_p),
                    timeout=self.LLM_TIMEOUT_SECONDS
                )
                raw_llm_text = llm_resp.completion_text if llm_resp else ""
            except asyncio.TimeoutError:
                yield event.plain_result("出题超时了，我的思路可能有点卡壳，请稍后再试吧！")
                return
            except Exception as e:
                logger.error(f"LLM首次请求失败: {e}")
                yield event.plain_result("糟糕，连接出题大脑时出错了，请稍后再试。")
                return

            # --- 解析和校验 ---
            if raw_llm_text:
                try:
                    start_index = raw_llm_text.find("{")
                    end_index = raw_llm_text.rfind("}")
                    if start_index == -1 or end_index == -1: raise ValueError("JSON not found")
                    json_part = raw_llm_text[start_index : end_index + 1]
                    parsed_data = json.loads(json_part)

                    if not all(k in parsed_data for k in ["题目描述", "题目可能的答案", "题目难度", "答案提示"]):
                         raise ValueError("JSON missing required keys")

                    # 检查是否重复
                    if not self._is_answer_duplicate(parsed_data.get("题目可能的答案", []), selected_topic):
                        question_data = parsed_data # 成功，不重复！
                    else:
                        # --- 触发“纠错式”二次生成 ---
                        yield event.plain_result("这题好像出过了，我立即换一题...")

                        repeated_answers = "、".join(map(str, parsed_data.get("题目可能的答案", ["未知"])))
                        prompt_attempt_2 = f"""
你是一个出题人。我刚才让你就【{selected_topic}】领域出一个【{selected_difficulty}】难度的题目，但你给我的题目核心答案是关于【{repeated_answers}】的，这个和我题库里的重复了。

**请你立即换一个全新的、与【{repeated_answers}】完全无关的人物、事件或概念**，重新给我一个关于【{selected_topic}】领域的题目。

请务必保持与之前完全相同的JSON格式输出。
"""
                        logger.info(f"检测到答案重复，进行纠错式二次生成... 规避答案: {repeated_answers}")
                        llm_resp_2 = await asyncio.wait_for(
                            provider.text_chat(prompt_attempt_2, temperature=self.llm_temperature + 0.1), # 稍微提高一点随机性
                            timeout=self.LLM_TIMEOUT_SECONDS
                        )
                        raw_llm_text_2 = llm_resp_2.completion_text if llm_resp_2 else ""
                        if raw_llm_text_2:
                             start_index_2 = raw_llm_text_2.find("{")
                             end_index_2 = raw_llm_text_2.rfind("}")
                             if start_index_2 == -1 or end_index_2 == -1: raise ValueError("JSON not found in retry")
                             json_part_2 = raw_llm_text_2[start_index_2 : end_index_2 + 1]
                             question_data = json.loads(json_part_2) # 直接采纳第二次的结果
                except Exception as e:
                     logger.error(f"处理LLM题目时出错: {e}\n原始返回: {raw_llm_text}")

            # --- 最后处理 ---
            if not question_data:
                yield event.plain_result("糟糕，我想题目的时候走神了，没想好。再试一次吧！")
                return

            # 成功获得题目，存入历史库并开始游戏
            new_answers = question_data.get("题目可能的答案", [])
            if new_answers:
                if selected_topic not in self.answer_history:
                    self.answer_history[selected_topic] = []
                # 确保答案是字符串
                self.answer_history[selected_topic].append([str(ans) for ans in new_answers])
                await self._save_history()

            timeout_task = asyncio.create_task(self._game_timeout(group_id, event))
            self.game_states[group_id] = GameState(question_data, timeout_task)
            final_difficulty = question_data.get("题目难度", selected_difficulty)
            description = question_data.get("题目描述", "糟糕，题目描述丢了！")
            announcement = (
                f"🎉 猜题游戏开始啦！(领域: {selected_topic} | 难度: {final_difficulty})\n"
                f"--------------------\n"
                f"题目：\n{description}\n"
                f"--------------------\n"
                f"⏱️ 你有 {int(self.GAME_TIMEOUT_SECONDS)} 秒的时间回答！\n"
                f"👉 直接在群里说出你的答案即可！\n"
                f"💡 仍然可以使用 `/提示`、`/结束答题` 或 `/猜题排行`。"
            )
            yield event.plain_result(announcement)

        finally:
            if group_id in self.generating_groups:
                self.generating_groups.remove(group_id)

    @filter.command("猜题排行", alias={"猜题榜","答题榜"})
    async def show_leaderboard(self, event: AstrMessageEvent):
        if not self.user_stats:
            yield event.plain_result("还没有任何玩家记录，快来玩一局吧！")
            return
        stats_list = [{"id": uid, **data} for uid, data in self.user_stats.items()]
        sorted_stats = sorted(stats_list, key=lambda x: x["correct"], reverse=True)
        leaderboard_lines = ["🏆 猜题风云榜 🏆", "--------------------"]
        for i, user in enumerate(sorted_stats[:10]):
            rank = i + 1
            name = user["name"]
            correct = user["correct"]
            attempts = user["attempts"]
            accuracy = f"{(correct / attempts * 100):.1f}%" if attempts > 0 else "0.0%"
            line = f"🏅 第 {rank} 名: {name}\n    答对: {correct} | 尝试: {attempts} (正确率: {accuracy})"
            leaderboard_lines.append(line)
        final_text = "\n".join(leaderboard_lines)
        yield event.plain_result(final_text)

    @filter.command("结束答题", alias={"结束"})
    async def end_game(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_states or not self.game_states[group_id].is_active:
            yield event.plain_result("当前没有正在进行的猜题游戏哦。")
            return
        state = self.game_states[group_id]
        if state.timeout_task and not state.timeout_task.done():
            state.timeout_task.cancel()
        ender_name = event.get_sender_name()
        answers_str = "、".join(map(str, state.question_data["题目可能的答案"]))
        yield event.plain_result(
            f"应 @{ender_name} 的要求，本轮猜题已提前结束。\n"
            f"正确答案是：【{answers_str}】"
        )
        del self.game_states[group_id]

    @filter.command("提示")
    async def get_hint(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_states or not self.game_states[group_id].is_active:
            return
        state = self.game_states[group_id]
        hints_list = state.question_data["答案提示"]
        if state.hints_given < len(hints_list):
            hint = hints_list[state.hints_given]
            state.hints_given += 1
            yield event.plain_result(
                f"🤫 提示来啦 (第{state.hints_given}条)：\n"
                f"{hint}"
            )
        else:
            yield event.plain_result("🤔 所有的提示都已经给完啦，靠你自己咯！")

    async def _game_timeout(self, group_id: str, event: AstrMessageEvent):
        try:
            await asyncio.sleep(self.GAME_TIMEOUT_SECONDS)
            if group_id in self.game_states and self.game_states[group_id].is_active:
                state = self.game_states[group_id]
                answers_str = "、".join(map(str, state.question_data["题目可能的答案"]))
                timeout_message = MessageChain().message(
                    f"⌛️ 时间到！很遗憾没有人答出来呢。\n"
                    f"公布答案：【{answers_str}】\n"
                    f"下次继续努力哦！"
                )
                await self.context.send_message(event.unified_msg_origin, timeout_message)
                del self.game_states[group_id]
        except asyncio.CancelledError:
            logger.info(f"群组 {group_id} 的猜题游戏计时器被正常取消。")
        except Exception as e:
            logger.error(f"游戏计时器发生异常: {e}")
            if group_id in self.game_states:
                del self.game_states[group_id]
