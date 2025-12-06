# main.py

import asyncio
import json
import math
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any, Set

# 导入 asteval 用于安全计算表达式
try:
    from asteval import Interpreter
except ImportError:
    raise ImportError("缺少 'asteval' 库，请运行 'pip install asteval' 或在插件的 requirements.txt 中添加它。")

# 导入 AstrBot 相关 API
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.api.event import MessageChain

# 尝试导入共享服务，用于经济API
try:
    from ..common.services import shared_services
except (ImportError, ModuleNotFoundError):
    logger.warning("无法导入 shared_services，经济功能将不可用。")
    shared_services = {}

class GameState:
    """扩展游戏状态以支持多种模式"""
    def __init__(self, numbers: List[int], solutions: List[str], difficulty: int, timeout_task: asyncio.Task, mode: str):
        self.numbers = numbers
        self.solutions = solutions
        self.difficulty = difficulty
        self.start_time = time.time()
        self.timeout_task = timeout_task
        self.is_active = True
        self.mode = mode  # 'timed' (传统计时模式) 或 'score' (比分模式)
        # 仅在比分模式中使用
        self.participants: Dict[str, Dict[str, Any]] = {} # {user_id: {"name": "xxx", "score": 100, "expr": "..."}}

@register(
    "Game24",
    "Gemini",
    "一个带有难度选择、比分模式和排行榜的24点小游戏插件",
    "3.5.0", # 版本号更新 (集成所有修复)
    "https://github.com/AstrBotDevs/AstrBot"
)
class Game24Plugin(Star):
    # 为不同模式设置不同的超时时间
    TIMED_MODE_TIMEOUT = 90.0
    SCORE_MODE_TIMEOUT = 180.0
    SCORE_MODE_PRIZE_POOL = 300 # 比分模式的奖金池

    def __init__(self, context: Context):
        super().__init__(context)
        # 分离不同模式的游戏实例
        self.active_games: Dict[str, GameState] = {}
        self.aeval = self._setup_safe_eval()
        self.economy_api = None
        self.daily_rewards: Dict[str, Dict[str, Any]] = {}
        
        # 玩家统计数据
        self.stats_file = Path("data/game24_stats.json")
        self.user_stats: Dict[str, Dict[str, Any]] = {}
        
        # 解法排行榜数据
        self.solution_leaderboard_file = Path("data/game24_solutions.json")
        self.solution_leaderboard: List[Dict[str, Any]] = []

        asyncio.create_task(self.initialize_apis())
        self._load_stats()
        self._load_solution_leaderboard()

    async def initialize_apis(self):
        logger.info("24点插件正在等待经济API...")
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 30:
            api = shared_services.get("economy_api")
            if api:
                self.economy_api = api
                logger.info("✅ 24点插件已成功连接到经济API！")
                return
            await asyncio.sleep(1)
        logger.warning("⚠️ 24点插件等待经济API超时，奖励功能将无法使用。")

    # region 数据读写
    def _load_stats(self):
        try:
            if self.stats_file.exists():
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    self.user_stats = json.load(f)
                logger.info("已成功加载24点游戏玩家统计数据。")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载24点游戏统计数据失败: {e}")
            self.user_stats = {}

    async def _save_stats(self):
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                self.stats_file.write_text, 
                json.dumps(self.user_stats, ensure_ascii=False, indent=4), 
                encoding='utf-8'
            )
        except IOError as e:
            logger.error(f"保存24点游戏统计数据失败: {e}")

    def _load_solution_leaderboard(self):
        try:
            if self.solution_leaderboard_file.exists():
                with open(self.solution_leaderboard_file, 'r', encoding='utf-8') as f:
                    self.solution_leaderboard = json.load(f)
                logger.info("已成功加载24点解法排行榜数据。")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载24点解法排行榜数据失败: {e}")
            self.solution_leaderboard = []

    async def _save_solution_leaderboard(self):
        try:
            self.solution_leaderboard_file.parent.mkdir(parents=True, exist_ok=True)
            # 排序并只保留前10名
            self.solution_leaderboard.sort(key=lambda x: x.get('score', 0), reverse=True)
            self.solution_leaderboard = self.solution_leaderboard[:10]
            await asyncio.to_thread(
                self.solution_leaderboard_file.write_text,
                json.dumps(self.solution_leaderboard, ensure_ascii=False, indent=4),
                encoding='utf-8'
            )
        except IOError as e:
            logger.error(f"保存24点解法排行榜失败: {e}")
    # endregion
    
    def _normalize_parentheses(self, expression: str) -> Tuple[str, int]:
        """
        规范化表达式，去除多余的外层括号。
        返回核心表达式和被移除的冗余括号对数。
        """
        stripped_pairs = 0
        core_expr = expression
        while core_expr.startswith('(') and core_expr.endswith(')'):
            # 检查括号是否是包裹整个表达式的匹配对
            balance = 0
            is_wrapping_pair = True
            for i, char in enumerate(core_expr[1:-1]):
                if char == '(':
                    balance += 1
                elif char == ')':
                    balance -= 1
                if balance < 0: # 括号不平衡，说明不是包裹对
                    is_wrapping_pair = False
                    break
            
            if is_wrapping_pair and balance == 0:
                core_expr = core_expr[1:-1]
                stripped_pairs += 1
            else:
                break # 不是包裹对，停止剥离
        return core_expr, stripped_pairs

    def _setup_safe_eval(self) -> Interpreter:
        aeval = Interpreter()
        for func in ['open', 'eval', 'exec', 'import_module', '__import__']:
            if func in aeval.symtable: del aeval.symtable[func]
        def factorial_safe(n):
            if isinstance(n, float) and n != int(n):
                raise ValueError("阶乘只能用于整数")
            n = int(n)
            if n < 0: raise ValueError("阶乘不能用于负数")
            if n > 20: raise ValueError("计算的数字太大了！")
            return math.factorial(n)
        aeval.symtable['factorial'] = factorial_safe
        return aeval
    
    # region 核心游戏逻辑
    def _find_all_solutions(self, nums: List[float]) -> Dict[float, Set[str]]:
        if len(nums) == 1:
            return {nums[0]: {str(int(nums[0])) if nums[0] == int(nums[0]) else str(nums[0])}}
        results = {}
        # 注意：为了让题目更有趣，这里允许数字交换位置来寻找解法，但在验证玩家答案时，依然要求顺序不变。
        from itertools import permutations
        for p_nums in set(permutations(nums)):
            # 内部递归求解时，我们用分治法，不需要再全排列
            sub_results = self._solve_recursive(list(p_nums))
            for val, exprs in sub_results.items():
                if val not in results: results[val] = set()
                results[val].update(exprs)
        return results

    def _solve_recursive(self, nums: List[float]) -> Dict[float, Set[str]]:
        """
        (最终修复版) 递归求解器，重构了阶乘逻辑以确保其在任何情况下都安全。
        """
        # 基础情况：当列表只有一个数字时
        if len(nums) == 1:
            n = nums[0]
            n_str = str(int(n)) if n == int(n) else str(n)
            results = {n: {n_str}}
            
            # 安全地对基础数字尝试阶乘
            if n == int(n) and 0 <= n <= 20:
                try:
                    fact_n = float(math.factorial(int(n))) # 确保结果是浮点数
                    if fact_n != n:
                        results[fact_n] = results.get(fact_n, set())
                        results[fact_n].add(f"factorial({n_str})")
                except (ValueError, OverflowError):
                    pass
            return results

        # 递归步骤：分割列表并组合结果
        results = {}
        for i in range(1, len(nums)):
            left_map = self._solve_recursive(nums[:i])
            right_map = self._solve_recursive(nums[i:])
            
            for v1, exprs1 in left_map.items():
                for v2, exprs2 in right_map.items():
                    for e1 in exprs1:
                        for e2 in exprs2:
                            # 定义基础运算
                            ops = {
                                '+': (v1 + v2, f"({e1}+{e2})"),
                                '-': (v1 - v2, f"({e1}-{e2})"),
                                '*': (v1 * v2, f"({e1}*{e2})"),
                            }
                            if v2 != 0:
                                ops['/'] = (v1 / v2, f"({e1}/{e2})")
                            
                            if abs(v1) < 10 and abs(v2) < 5 and not (v1 == 0 and v2 == 0):
                                try:
                                    ops['**'] = (v1 ** v2, f"({e1}**{e2})")
                                except (ValueError, OverflowError):
                                    pass

                            # 遍历所有运算组合
                            for op_key, (res_val, res_expr) in ops.items():
                                # 1. 添加直接运算的结果
                                if res_val not in results: results[res_val] = set()
                                results[res_val].add(res_expr)

                                # 2. 【核心修改】对运算结果进行严格前置检查后再尝试阶乘
                                if res_val == int(res_val) and 0 <= res_val <= 20:
                                    try:
                                        fact_res = float(math.factorial(int(res_val)))
                                        if fact_res != res_val:
                                            fact_expr = f"factorial({res_expr})"
                                            if fact_res not in results: results[fact_res] = set()
                                            results[fact_res].add(fact_expr)
                                    except (ValueError, OverflowError):
                                        pass
        return results
    def _format_expression_for_display(self, expression: str) -> str:
        """将内部表达式转换为人类可读的格式。"""
        # 1. 将 factorial(x) 转换为 (x)!
        # 使用正则表达式，可以正确处理 factorial((1+2)) 这样的情况
        import re
        # 使用循环以处理可能的多层阶乘（尽管当前求解器不会生成）
        while 'factorial' in expression:
            expression = re.sub(r'factorial\((.*?)\)', r'(\1)!', expression)
        
        # 2. 将 x**y 转换成 x^y
        expression = expression.replace('**', '^')
        
        # 3. 移除表达式最外层多余的括号，让输出更简洁
        core_expr, _ = self._normalize_parentheses(expression)
        
        return core_expr

    def _generate_problem(self, difficulty: str = "普通") -> Optional[Tuple[List[int], List[str], int]]:
        num_range = {
            "简单": (1, 7),
            "普通": (1, 10),
            "困难": (1, 13)
        }.get(difficulty, (1, 10))

        for _ in range(500): # 增加尝试次数以找到合适的题目
            nums = [random.randint(num_range[0], num_range[1]) for _ in range(4)]
            try:
                # --- V2: 修复浮点数精度问题 ---
                # 步骤1：用宽松容差广泛搜集候选解
                all_results = self._solve_recursive(nums) 
                candidate_solutions = set()
                for val, exprs in all_results.items():
                    if abs(val - 24) < 1e-6: # 宽松容差
                        candidate_solutions.update(exprs)
                
                # 步骤2：对候选解进行严格的自验算过滤
                verified_solutions = []
                if candidate_solutions:
                    for expr in candidate_solutions:
                        try:
                            # 使用 asteval 进行精确计算
                            result = self.aeval.eval(expr)
                            # 使用极严格的容差进行最终验证
                            if abs(result - 24) < 1e-9: 
                                verified_solutions.append(expr)
                        except Exception:
                            # 如果表达式在精确计算时出错，则跳过
                            continue
                # --- 修改结束 ---

                if verified_solutions: # 使用严格验证后的解列表
                    num_solutions = len(verified_solutions)
                    # 根据难度调整筛选条件
                    if difficulty == "困难" and num_solutions > 15: continue
                    if difficulty == "简单" and num_solutions < 5: continue
                    
                    # 难度评分，解法越少越难
                    diff_score = max(0, 10 - num_solutions) * 10
                    # 返回的是 verified_solutions
                    return nums, verified_solutions, diff_score
            except Exception:
                continue
        return None

    def _calculate_reward(self, state: GameState, processed_expression: str) -> Tuple[int, str, float]:
        """计算传统计时模式的奖励"""
        time_taken = time.time() - state.start_time
        base_reward = 30
        difficulty_bonus = state.difficulty
        speed_bonus = int(max(0, self.TIMED_MODE_TIMEOUT - 30 - time_taken) * 1.5)
        solution_bonus = 0
        if '**' in processed_expression: solution_bonus += 25
        if 'factorial' in processed_expression: solution_bonus += 40
        total_reward = base_reward + difficulty_bonus + speed_bonus + solution_bonus
        details = (f"基础分({base_reward}) + 难度分({difficulty_bonus}) + "
                   f"速度分({speed_bonus}) + 解法分({solution_bonus})")
        return total_reward, details, time_taken

    def _calculate_solution_score(self, processed_expression: str) -> Tuple[int, str]:
        """
        (V3) 计算比分模式中解法的趣味性得分，彻底修复刷分漏洞。
        """
        score = 10  # 基础分
        details = ["基础分(10)"]
        
        # 1. 基础运算符计分
        op_scores = {'+': 1, '-': 1, '*': 3, '/': 3, '**': 8}
        for op, op_score in op_scores.items():
            count = processed_expression.count(op)
            if count > 0:
                score += count * op_score
                details.append(f"{op}({count}*{op_score})")

        # 2. 阶乘计分 (平凡阶乘得0分)
        factorial_matches = re.findall(r'factorial\((.*?)\)', processed_expression)
        trivial_factorials = 0
        effective_factorials = 0
        for match in factorial_matches:
            try:
                # 计算括号内的值，判断是否为平凡阶乘
                value = self.aeval.eval(match)
                if value in [0, 1, 2]:
                    trivial_factorials += 1
                else:
                    effective_factorials += 1
            except Exception:
                effective_factorials += 1
        
        if trivial_factorials > 0:
            # --- 修改点：平凡阶乘得分为 0 ---
            score += trivial_factorials * 0
            details.append(f"平凡阶乘({trivial_factorials}*0)")
        if effective_factorials > 0:
            score += effective_factorials * 12
            details.append(f"有效阶乘({effective_factorials}*12)")

        # 3. 括号计分 (冗余括号得0分)
        core_expr, redundant_pairs = self._normalize_parentheses(processed_expression)
        meaningful_pairs = core_expr.count('(')

        if meaningful_pairs > 0:
            score += meaningful_pairs * 2
            details.append(f"有效括号({meaningful_pairs}*2)")
        if redundant_pairs > 0:
            # --- 修改点：冗余括号得分为 0 ---
            score += redundant_pairs * 0
            details.append(f"冗余括号({redundant_pairs}*0)")
            
        return score, " + ".join(details)
    # region 游戏指令
    @filter.command("24点", alias={'算24'})
    async def start_game_command(self, event: AstrMessageEvent):
        session_id = event.get_group_id() or event.get_sender_id()
        if session_id in self.active_games:
            game_mode_text = "比分赛" if self.active_games[session_id].mode == 'score' else "挑战赛"
            yield event.plain_result(f"本群已有一场 {game_mode_text} 正在进行中！")
            return

        difficulty_text = event.message_str.strip()
        difficulty = "普通"
        if "简单" in difficulty_text: difficulty = "简单"
        elif "困难" in difficulty_text: difficulty = "困难"
        
        yield event.plain_result(f"正在思考一道【{difficulty}】难度的题目，请稍候...")
        problem = self._generate_problem(difficulty)
        
        if not problem:
            yield event.plain_result("抱歉，脑子有点乱，没想出好题目，请稍后再试吧。")
            return
            
        numbers, solutions, diff_score = problem
        timeout_task = asyncio.create_task(self._game_timeout(session_id, event, self.TIMED_MODE_TIMEOUT, 'timed'))
        self.active_games[session_id] = GameState(numbers, solutions, diff_score, timeout_task, 'timed')
        
        nums_str = '、'.join(map(str, numbers))
        yield event.plain_result(
            f"🎲 24点【计时挑战赛】开始！ (难度: {difficulty})\n\n"
            f"请用【{nums_str}】这四个数（严格按顺序）计算出 24。\n\n"
            f"支持: `+ - * / ^ ! ()`\n"
            f"你有 {int(self.TIMED_MODE_TIMEOUT)} 秒时间！第一个答对者获胜！"
        )

    @filter.command("24点比分", alias={'24点比赛'})
    async def start_score_game_command(self, event: AstrMessageEvent):
        session_id = event.get_group_id() or event.get_sender_id()
        if session_id in self.active_games:
            game_mode_text = "比分赛" if self.active_games[session_id].mode == 'score' else "挑战赛"
            yield event.plain_result(f"本群已有一场 {game_mode_text} 正在进行中！")
            return

        yield event.plain_result("正在为【比分赛】挑选一道有趣的题目，请稍候...")
        # 比分赛默认使用普通难度
        problem = self._generate_problem("普通")
        if not problem:
            yield event.plain_result("抱歉，没能找到适合比赛的题目，请稍后再试。")
            return
            
        numbers, solutions, diff_score = problem
        timeout_task = asyncio.create_task(self._game_timeout(session_id, event, self.SCORE_MODE_TIMEOUT, 'score'))
        self.active_games[session_id] = GameState(numbers, solutions, diff_score, timeout_task, 'score')
        
        nums_str = '、'.join(map(str, numbers))
        yield event.plain_result(
            f"🏆 24点【比分大赛】开始！\n\n"
            f"请用【{nums_str}】这四个数（严格按顺序）计算出 24。\n\n"
            f"规则：\n"
            f"1. 在 {int(self.SCORE_MODE_TIMEOUT)} 秒内，任何人都可以提交答案。\n"
            f"2. 解法越“有趣”（如使用阶乘、幂、复杂括号），得分越高。\n"
            f"3. 游戏结束后，所有提交过答案的玩家将按最高分瓜分 {self.SCORE_MODE_PRIZE_POOL} 金币奖池！\n\n"
            f"发送 `/结束比分` 可提前结算。祝你好运！"
        )

    @filter.on_llm_request()
    async def answer_hook(self, event: AstrMessageEvent, req: ProviderRequest):
        session_id = event.get_group_id() or event.get_sender_id()
        if not session_id or session_id not in self.active_games:
            return

        state = self.active_games[session_id]
        if not state.is_active: return

        user_answer = event.message_str.strip()
        if user_answer.startswith('/'): return
        
        is_correct, message, processed_expr = self._check_user_expression(user_answer, state.numbers)
        if not is_correct:
            # 只有计时模式下才提示错误答案
            if state.mode == 'timed':
                await event.send(event.plain_result(f"🤔 @{event.get_sender_name()}，{message}"))
            event.stop_event()
            return
            
        # 根据游戏模式处理正确答案
        if state.mode == 'timed':
            await self._handle_timed_mode_win(event, state, user_answer, processed_expr)
        elif state.mode == 'score':
            await self._handle_score_mode_submit(event, state, user_answer, processed_expr)
            
        event.stop_event()

    @filter.command("结束24点", alias={'退出24点'})
    async def end_game_command(self, event: AstrMessageEvent):
        session_id = event.get_group_id() or event.get_sender_id()
        if session_id in self.active_games and self.active_games[session_id].mode == 'timed':
            state = self.active_games.pop(session_id)
            state.is_active = False
            state.timeout_task.cancel()
            solution_to_show = random.choice(state.solutions).replace(' ', '')
            yield event.plain_result(
                f"计时挑战赛已由 @{event.get_sender_name()} 结束。\n"
                f"一个可能的答案是：{solution_to_show}"
            )
        else:
            yield event.plain_result("当前没有正在进行的计时挑战赛。")

    @filter.command("结束比分", alias={'结算比分'})
    async def end_score_game_command(self, event: AstrMessageEvent):
        session_id = event.get_group_id() or event.get_sender_id()
        if session_id in self.active_games and self.active_games[session_id].mode == 'score':
            state = self.active_games.pop(session_id)
            if state.is_active:
                state.is_active = False
                state.timeout_task.cancel()
                await self._finalize_score_game(state, event.unified_msg_origin, f"比赛已由 @{event.get_sender_name()} 提前结束！")
        else:
            await event.send(event.plain_result("当前没有正在进行的比分大赛。"))
    # endregion

    # region 排行榜指令
    @filter.command("24点排行榜", alias={'24点排行', '24点榜'})
    async def show_leaderboard(self, event: AstrMessageEvent):
        if not self.user_stats:
            yield event.plain_result("目前还没有玩家记录，快来玩一局成为榜首吧！")
            return

        stats_list = list(self.user_stats.values())
        sorted_stats = sorted(stats_list, key=lambda x: x.get('total_score', 0), reverse=True)

        leaderboard_lines = ["🏆 24点玩家排行榜 🏆", "--------------------"]
        for i, user in enumerate(sorted_stats[:10]):
            rank = i + 1
            name = user.get('name', '匿名玩家')
            score = user.get('total_score', 0)
            games_won = user.get('games_won', 0)
            
            if games_won > 0:
                avg_time = user.get('total_time_taken', 0) / games_won
                avg_time_str = f"{avg_time:.2f}秒"
            else:
                avg_time_str = "N/A"

            line = f"🏅 第 {rank} 名: {name}\n   总分: {score} | 胜场: {games_won} | 平均耗时: {avg_time_str}"
            leaderboard_lines.append(line)
        
        final_text = "\n".join(leaderboard_lines)
        yield event.plain_result(final_text)

    @filter.command("24点解法榜", alias={'24解法榜'})
    async def show_solution_leaderboard(self, event: AstrMessageEvent):
        if not self.solution_leaderboard:
            yield event.plain_result("解法宗师殿堂虚位以待，快去比分模式中创造神仙解法吧！")
            return
        
        leaderboard_lines = ["✨ 24点神仙解法榜 ✨", "--------------------"]
        for i, entry in enumerate(self.solution_leaderboard[:10]):
            rank = i + 1
            nums_str = ', '.join(map(str, entry.get('numbers', [])))
            line = (f"👑 Top {rank}: {entry.get('score')} 分 - By {entry.get('user_name', '匿名宗师')}\n"
                    f"   题目: [{nums_str}]\n"
                    f"   解法: {entry.get('expression', 'N/A')}")
            leaderboard_lines.append(line)
        
        final_text = "\n".join(leaderboard_lines)
        yield event.plain_result(final_text)
    # endregion

    # region 内部处理函数
    async def _award_coins(self, user_id: str, amount: int, reason: str) -> Tuple[int, str]:
        """
        统一处理金币奖励，包含每日上限检查。
        返回实际奖励数量和给用户的提示消息。
        """
        if not self.economy_api:
            return 0, ""

        today = datetime.now().strftime("%Y-%m-%d")
        user_daily = self.daily_rewards.get(user_id, {"date": "", "total": 0})
        
        # 如果不是今天，则重置每日奖励记录
        if user_daily["date"] != today:
            user_daily = {"date": today, "total": 0}

        daily_cap = 1000
        remaining_cap = daily_cap - user_daily.get("total", 0)
        
        # 计算实际能获得的奖励
        actual_reward = min(amount, remaining_cap)
        
        if actual_reward > 0:
            await self.economy_api.add_coins(user_id, actual_reward, reason)
            user_daily["total"] += actual_reward
            self.daily_rewards[user_id] = user_daily
            msg = f"💰 恭喜你获得 {actual_reward} 金币！(今日已获 {user_daily['total']}/{daily_cap})"
            return actual_reward, msg
        else:
            total_earned = user_daily.get("total", 0)
            msg = f"👍 你今天已经拿满了奖励({total_earned}/{daily_cap})，明天再来吧！"
            return 0, msg

    async def _game_timeout(self, session_id: str, event: AstrMessageEvent, timeout: float, mode: str):
        try:
            await asyncio.sleep(timeout)
            if session_id in self.active_games and self.active_games[session_id].is_active:
                state = self.active_games.pop(session_id)
                state.is_active = False
                
                if mode == 'timed':
                    # --- 修改点在这里 ---
                    raw_solution = random.choice(state.solutions)
                    solution_to_show = self._format_expression_for_display(raw_solution)
                    # --- 修改结束 ---
                    timeout_message = MessageChain().message(
                        f"⌛️ 时间到！很遗憾，没人答对呢。\n"
                        f"公布答案：{solution_to_show}"
                    )
                    await self.context.send_message(event.unified_msg_origin, timeout_message)
                elif mode == 'score':
                    await self._finalize_score_game(state, event.unified_msg_origin, "⏱️ 时间到！比赛结束")
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"24点游戏计时器异常: {e}")
            if session_id in self.active_games:
                del self.active_games[session_id]

    async def terminate(self):
        for state in self.active_games.values():
            if not state.timeout_task.done():
                state.timeout_task.cancel()
        self.active_games.clear()
        logger.info("所有24点游戏已清理。")

    def _check_user_expression(self, expression: str, numbers: List[int]) -> Tuple[bool, str, Optional[str]]:
        try:
            processed_expr = self._preprocess_for_eval(expression)
        except ValueError as e: return False, str(e), None
        
        # 验证数字使用及顺序
        found_nums_str = re.findall(r'\d+', expression)
        expected_nums_str = [str(n) for n in numbers]
        if found_nums_str != expected_nums_str:
            msg = f"请严格按顺序使用数字 {', '.join(expected_nums_str)}！"
            return False, msg, None
            
        try:
            result = self.aeval.eval(processed_expr)
            if abs(result - 24) < 1e-6:
                return True, "计算正确！", processed_expr
            else:
                return False, f"计算结果是 {result:.2f}，不等于24哦。", None
        except Exception as e:
            logger.error(f"表达式计算失败: {expression} -> {processed_expr} | 错误: {e}")
            return False, "你的表达式好像有点问题，我算不出来呢。", None

    async def _handle_timed_mode_win(self, event: AstrMessageEvent, state: GameState, user_answer: str, processed_expr: str):
        session_id = event.get_group_id() or event.get_sender_id()
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        state.is_active = False
        state.timeout_task.cancel()
        del self.active_games[session_id]
        
        total_reward, details, time_taken = self._calculate_reward(state, processed_expr)
        
        # 更新玩家统计数据
        if user_id not in self.user_stats:
            self.user_stats[user_id] = {"name": user_name, "total_score": 0, "total_time_taken": 0.0, "games_won": 0}
        
        stats = self.user_stats[user_id]
        stats["name"] = user_name
        stats["total_score"] += total_reward
        stats["total_time_taken"] += time_taken
        stats["games_won"] += 1
        await self._save_stats()

        # --- 调用新的统一奖励函数 ---
        reward_msg = ""
        if self.economy_api:
            awarded_amount, reward_msg_part = await self._award_coins(user_id, total_reward, "24点计时赛胜利")
            reward_msg = reward_msg_part
            # 只有实际获得奖励时才显示得分详情
            if awarded_amount > 0:
                reward_msg += f"\n📜 得分详情: {details}"

        success_text = (
            f"🎉 恭喜 @{user_name} 回答正确！\n"
            f"⏱️ 答题耗时: {time_taken:.2f} 秒\n"
            f"表达式: {user_answer}\n"
            f"{reward_msg}"
        )
        await event.send(event.plain_result(success_text))

    async def _handle_score_mode_submit(self, event: AstrMessageEvent, state: GameState, user_answer: str, processed_expr: str):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        # --- 新增：防抄袭检查 ---
        for participant_id, p_data in state.participants.items():
            # 使用处理过的表达式进行比较，可以忽略空格、全角/半角符号等差异
            if p_data['processed_expr'] == processed_expr:
                # 如果是自己重复提交，则不提示
                if participant_id == user_id:
                    return
                submitter_name = p_data.get('name', '另一位玩家')
                await event.send(event.plain_result(f"@{user_name}，这个解法已经被 @{submitter_name} 提交过了哦，换个思路吧！"))
                return
        # --- 防抄袭检查结束 ---
        
        score, details = self._calculate_solution_score(processed_expr)
        
        # 检查是否是更高分的解法 (对自己而言)
        current_best = state.participants.get(user_id)
        if current_best and score <= current_best["score"]:
            await event.send(event.plain_result(f"@{user_name}，这个解法得分({score})没有你之前的解法({current_best['score']})高哦，再想想有没有更秀的！"))
            return

        state.participants[user_id] = {"name": user_name, "score": score, "expr": user_answer, "processed_expr": processed_expr}
        
        reply_msg = (f"💡 @{user_name} 回答正确！\n"
                     f"解法得分: {score} 分 ({details})\n"
                     f"这是你的新纪录！继续努力，还有更高分的可能！")
        await event.send(event.plain_result(reply_msg))
        
    async def _finalize_score_game(self, state: GameState, origin: Dict, title: str):
        """结算比分模式游戏"""
        # --- 检查是否无人参与 ---
        if not state.participants:
            # 从预先生成的答案列表中随机选一个
            raw_solution = random.choice(state.solutions)
            solution_to_show = self._format_expression_for_display(raw_solution)
            timeout_message = (
                f"{title}\n\n"
                f"很遗憾，本次比赛无人提交答案。\n"
                f"一个可能的解法是: {solution_to_show}"
            )
            await self.context.send_message(origin, MessageChain().message(timeout_message))
            return # 结束函数
            
        # 更新解法排行榜
        new_entries = []
        for user_id, data in state.participants.items():
            new_entries.append({
                "score": data['score'],
                "expression": data['expr'],
                "user_id": user_id,
                "user_name": data['name'],
                "numbers": state.numbers
            })
        self.solution_leaderboard.extend(new_entries)
        await self._save_solution_leaderboard()

        # 计算奖励
        sorted_participants = sorted(state.participants.items(), key=lambda item: item[1]['score'], reverse=True)
        total_score = sum(p_data['score'] for user_id, p_data in sorted_participants)
        
        result_lines = [f"🏆 {title} 结算中... 🏆", "--------------------"]
        
        awarded_coins_info = []
        notes = [] # 用于存放额外提示，如奖励已达上限
        if self.economy_api and total_score > 0:
            for user_id, p_data in sorted_participants:
                potential_reward = math.ceil(self.SCORE_MODE_PRIZE_POOL * (p_data['score'] / total_score))
                awarded_amount, reward_msg_part = await self._award_coins(user_id, potential_reward, "24点比分赛奖励")
                
                awarded_coins_info.append((p_data['name'], p_data['score'], awarded_amount))
                
                if potential_reward > awarded_amount:
                    notes.append(f"提示: @{p_data['name']} 的每日奖励已达上限。")

        if awarded_coins_info:
            for i, (name, score, reward) in enumerate(awarded_coins_info):
                reward_text = f" - 获得 {reward} 金币" if reward > 0 else ""
                result_lines.append(f"第 {i+1} 名: @{name} ({score}分){reward_text}")
        elif not self.economy_api and sorted_participants:
            result_lines.append("（经济系统未启用，本次无金币奖励）")
            for i, (user_id, p_data) in enumerate(sorted_participants):
                 result_lines.append(f"第 {i+1} 名: @{p_data['name']} ({p_data['score']}分)")

        if notes:
            result_lines.append("--------------------")
            result_lines.extend(notes)

        final_msg = "\n".join(result_lines)
        await self.context.send_message(origin, MessageChain().message(final_msg))

    # #############################################################################
    # ## 核心修改：重写阶乘解析逻辑 (这部分逻辑很棒，予以保留)
    # #############################################################################
    def _transform_factorials(self, expression: str) -> str:
        """从右到左手动解析阶乘，支持嵌套括号"""
        while '!' in expression:
            bang_index = expression.rfind('!')
            if bang_index == 0: raise ValueError("阶乘符号'!'前缺少操作数")
            
            prev_char = expression[bang_index - 1]
            
            # 情况1: 阶乘作用于括号表达式，如 (...)!
            if prev_char == ')':
                end_paren_index = bang_index - 1
                level = 0
                start_paren_index = -1
                for i in range(end_paren_index, -1, -1):
                    if expression[i] == ')': level += 1
                    elif expression[i] == '(': level -= 1
                    if level == 0:
                        start_paren_index = i
                        break
                
                if start_paren_index != -1:
                    operand = expression[start_paren_index : end_paren_index + 1]
                    expression = f"{expression[:start_paren_index]}factorial{operand}{expression[bang_index + 1:]}"
                    continue
                else:
                    raise ValueError("表达式中存在不匹配的括号")
            
            # 情况2: 阶乘作用于数字, 如 4!
            elif prev_char.isdigit():
                end_num_index = bang_index - 1
                start_num_index = end_num_index
                while start_num_index > 0 and expression[start_num_index - 1].isdigit():
                    start_num_index -= 1
                
                operand = expression[start_num_index : end_num_index + 1]
                expression = f"{expression[:start_num_index]}factorial({operand}){expression[bang_index + 1:]}"
                continue
            
            # 其他情况，如 ' !' 或 '+!' 均视为非法
            else:
                raise ValueError(f"阶乘符号'!'前有无效字符: '{prev_char}'")
        return expression

    def _preprocess_for_eval(self, expression: str) -> str:
        replacements = {'（': '(', '）': ')', '，': ',', '＋': '+', '－': '-', '×': '*', 'x': '*', 'X': '*', '÷': '/', '•': '*', '／': '/', '＊': '*', '＾': '**', '！': '!'}
        expression = expression.replace(' ', '') # 移除所有空格
        for old, new in replacements.items(): expression = expression.replace(old, new)
        
        # 先检查非法字符，但不包括 '!'
        if re.search(r"[^0-9\+\-\*\/\^\(\)\.e!]", expression): 
            raise ValueError("表达式中包含了不支持的符号。")
        
        # 调用新的、更可靠的阶乘转换函数
        processed_expr = self._transform_factorials(expression)
            
        return processed_expr
    # endregion