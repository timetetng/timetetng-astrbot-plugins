import importlib
import os
from typing import Any

from astrbot.api import logger  # 导入 AstrBot 的 logger


class AchievementManager:
    def __init__(self):
        self.achievements: dict[str, dict[str, Any]] = {}
        self.rarity_list = [
            "common",
            "rare",
            "epic",
            "legendary",
            "mythic",
            "miracle",
            "flawless",
        ]
        self.RARITY_NAMES = {
            "common": "普通",
            "rare": "稀有",
            "epic": "史诗",
            "legendary": "传说",
            "mythic": "神话",
            "miracle": "奇迹",
            "flawless": "无瑕",
        }

    def load_achievements(self, directory: str) -> tuple[int, int]:
        """
        动态加载所有成就定义文件（包括所有子目录），并进行详细的错误检查和日志记录。
        返回 (成功加载的文件数, 失败的文件数)。
        """
        self.achievements = {}
        successful_files = 0
        failed_files = 0

        if not os.path.isdir(directory):
            logger.warning(f"成就定义目录不存在: {directory}")
            return 0, 0

        # --- 【核心修改点】 ---
        # 使用 os.walk() 来遍历所有子目录
        for root, _, files in os.walk(directory):
            for filename in files:
                if not (filename.endswith(".py") and not filename.startswith("__")):
                    continue

                # 动态构建模块的导入路径，使其支持子目录
                # 例如 root = 'achievements/subdir', directory = 'achievements'
                # relative_path = 'subdir'
                # module_prefix 将变为 '...achievements.subdir'
                relative_path = os.path.relpath(root, directory)

                base_module_path = directory.replace("/", ".")
                if relative_path == ".":
                    module_prefix = base_module_path
                else:
                    sub_path = relative_path.replace(os.sep, ".")
                    module_prefix = f"{base_module_path}.{sub_path}"

                module_name = f"{module_prefix}.{filename[:-3]}"

                try:
                    module = importlib.import_module(module_name)
                    importlib.reload(module)

                    if not hasattr(module, "ACHIEVEMENTS"):
                        logger.warning(
                            f"加载失败: 文件 '{filename}' 中未定义 'ACHIEVEMENTS' 列表。"
                        )
                        failed_files += 1
                        continue

                    ach_list = getattr(module, "ACHIEVEMENTS")

                    if not isinstance(ach_list, list):
                        logger.warning(
                            f"加载失败: 文件 '{filename}' 中的 'ACHIEVEMENTS' 不是一个列表 (list)。"
                        )
                        failed_files += 1
                        continue

                    loaded_count = 0
                    for ach_data in ach_list:
                        if not isinstance(ach_data, dict) or "id" not in ach_data:
                            logger.warning(
                                f"跳过加载: 文件 '{filename}' 中存在格式错误（非字典或无id）的成就项。"
                            )
                            continue

                        ach_id = ach_data["id"]
                        if ach_id in self.achievements:
                            logger.warning(
                                f"跳过加载: 文件 '{filename}' 中成就ID '{ach_id}' 与已加载的成就重复。"
                            )
                            continue

                        self.achievements[ach_id] = ach_data
                        loaded_count += 1

                    if loaded_count > 0:
                        logger.info(
                            f"成功加载成就文件: '{filename}' (共 {loaded_count} 个成就)。"
                        )
                        successful_files += 1

                except Exception as e:
                    logger.error(
                        f"加载成就文件 '{filename}' 时发生严重错误，该文件被跳过。错误: {e}",
                        exc_info=True,
                    )
                    failed_files += 1
        # --- 修改结束 ---

        return successful_files, failed_files

    def get_all_achievements(self) -> list[dict[str, Any]]:
        return list(self.achievements.values())

    def get_achievement_by_id(self, ach_id: str) -> dict[str, Any] | None:
        return self.achievements.get(ach_id)

    def register_achievement(self, ach_data: dict) -> (bool, str):
        ach_id = ach_data.get("id")
        if not ach_id:
            return False, "成就数据必须包含 'id' 字段。"
        if ach_id in self.achievements:
            # 在运行时注册，如果已存在，可以选择更新或忽略
            # logger.warning(f"注册成就失败：成就ID '{ach_id}' 已存在。")
            return False, f"成就ID '{ach_id}' 已存在。"

        self.achievements[ach_id] = ach_data
        logger.info(f"通过API成功注册新成就: {ach_id}")
        return True, f"成就 '{ach_id}' 注册成功。"
