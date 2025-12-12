import json
import os


class DataManager:
    def __init__(
        self,
        data_path="data/plugin_data/astrbot_plugin_achievement/achievement_progress.json",
        unique_data_path="data/plugin_data/astrbot_plugin_achievement/unique_achievements.json",
        pending_data_path="data/plugin_data/astrbot_plugin_achievement/pending_notifications.json",
    ):
        self.data_path = data_path
        self.pending_data_path = pending_data_path
        self.unique_data_path = unique_data_path  # 新增：唯一成就的数据文件路径
        self.data: dict[str, list[str]] = {}
        self.unique_data: dict[str, str] = {}
        self.pending_data: dict[str, list[str]] = {}
        self.load()

    def load(self):
        # 加载个人成就进度
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        try:
            with open(self.data_path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

        # 新增：加载唯一成就记录
        os.makedirs(os.path.dirname(self.unique_data_path), exist_ok=True)
        try:
            with open(self.unique_data_path, encoding="utf-8") as f:
                self.unique_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.unique_data = {}

        os.makedirs(os.path.dirname(self.pending_data_path), exist_ok=True)
        try:
            with open(self.pending_data_path, encoding="utf-8") as f:
                self.pending_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.pending_data = {}

    def save(self):
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)

    def save_unique(self):
        """新增：保存唯一成就数据"""
        with open(self.unique_data_path, "w", encoding="utf-8") as f:
            json.dump(self.unique_data, f, ensure_ascii=False, indent=4)

    def save_pending(self):
        """新增：保存待推送队列数据"""
        with open(self.pending_data_path, "w", encoding="utf-8") as f:
            json.dump(self.pending_data, f, ensure_ascii=False, indent=4)

    def get_unlocked_achievements(self, user_id: str) -> set[str]:
        return set(self.data.get(user_id, []))

    def add_achievement_to_user(self, user_id: str, achievement_id: str):
        if user_id not in self.data:
            self.data[user_id] = []
        if achievement_id not in self.data[user_id]:
            self.data[user_id].append(achievement_id)
            self.save()

    def reset_user_achievements(self, user_id: str) -> bool:
        if user_id in self.data:
            del self.data[user_id]
            self.save()
            # 注意：重置用户数据通常不应让唯一成就重新变为可用。
            # 这是为了防止管理员滥用命令来转移唯一成就的归属。
            return True
        return False

    def reset_all_data(self) -> int:
        """
        清空所有成就数据，包括所有用户的进度和唯一成就的记录。
        """
        num_users_affected = len(self.data)

        self.data = {}
        self.unique_data = {}
        self.pending_data = {}
        self.save()
        self.save_unique()
        self.save_pending()

        return num_users_affected

    # --- 新增：唯一成就相关方法 ---

    def is_unique_achievement_claimed(self, achievement_id: str) -> bool:
        """检查一个唯一成就是否已被任何人认领"""
        return achievement_id in self.unique_data

    def get_unique_achievement_owner(self, achievement_id: str) -> str | None:
        """获取唯一成就的拥有者用户ID"""
        return self.unique_data.get(achievement_id)

    def claim_unique_achievement(self, achievement_id: str, user_id: str) -> bool:
        """
        将一个唯一成就标记为已被认领。
        如果已被认领，返回 False；如果成功认领，返回 True。
        """
        if self.is_unique_achievement_claimed(achievement_id):
            return False

        self.unique_data[achievement_id] = user_id
        self.save_unique()
        return True

    def add_pending_notification(self, user_id: str, achievement_id: str):
        """为用户添加一个待推送的成就通知"""
        if user_id not in self.pending_data:
            self.pending_data[user_id] = []
        if achievement_id not in self.pending_data[user_id]:
            self.pending_data[user_id].append(achievement_id)
            self.save_pending()

    def get_and_clear_pending_notifications(self, user_id: str) -> list[str]:
        """获取并清空一个用户的所有待推送通知"""
        pending_list = self.pending_data.pop(user_id, [])
        if pending_list:  # 如果确实有数据被移除，才保存文件
            self.save_pending()
        return pending_list

    def has_achievement(self, user_id: str, achievement_id: str) -> bool:
        """检查用户是否已经拥有特定成就"""
        return user_id in self.data and achievement_id in self.data[user_id]
