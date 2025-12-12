from .minecraft_achievement import AchievementBoardGenerator, AchievementGenerator


class ImageGenerator:
    def __init__(self, font_path: str, icon_cache_manager):  # <-- 修改点
        self.ach_gen = AchievementGenerator(
            font_path=font_path, icon_cache_manager=icon_cache_manager
        )  # <-- 修改点
        self.board_gen = AchievementBoardGenerator(
            generator=self.ach_gen, font_path=font_path
        )

    async def create_achievement_image(
        self, title, description, icon_path, rarity, output_path
    ):  # <-- 修改点
        """生成单个成就解锁图"""
        await self.ach_gen.create(  # <-- 修改点
            title=title,
            description=description,
            icon_path=icon_path,
            theme=rarity,  # theme 参数对应稀有度
            output_format="file",
            output_path=output_path,
        )

    async def create_achievement_board(
        self,
        user_name,
        all_achievements_data,
        unlocked_ids,
        unlocked_count,
        total_count,
        output_path,
    ):  # <-- 修改点
        """生成成就看板"""
        # 注意：需要将我们的成就数据格式转换为你生成器所需的格式
        formatted_ach_data = {
            ach["id"]: {
                "title": ach["title"],
                "description": ach["description"],
                "icon_path": ach["icon_path"],
                "rarity": ach["rarity"],
            }
            for ach in all_achievements_data
        }

        await self.board_gen.create_board(  # <-- 修改点
            user_name=user_name,
            all_achievements=formatted_ach_data,
            unlocked_ids=unlocked_ids,
            unlocked_count=unlocked_count,
            total_count=total_count,
            output_path=output_path,
        )
