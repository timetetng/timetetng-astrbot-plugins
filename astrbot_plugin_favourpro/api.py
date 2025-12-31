from typing import Dict, Any, Optional, List
from .database import DatabaseManager
from .const import DEFAULT_STATE

class FavourProAPI:
    """
    好感度插件API (FavourProAPI)
    提供给其他插件调用的好感度相关接口。
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_user_state(self, user_id: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        state = await self.db.get_user_state(user_id, session_id)
        return state if state != DEFAULT_STATE else None

    async def add_favour(self, user_id: str, amount: int, session_id: Optional[str] = None):
        current_state = await self.db.get_user_state(user_id, session_id)
        current_state["favour"] += amount
        await self.db.update_user_state(user_id, current_state, session_id)

    async def set_favour(self, user_id: str, amount: int, session_id: Optional[str] = None):
        current_state = await self.db.get_user_state(user_id, session_id)
        current_state["favour"] = amount
        await self.db.update_user_state(user_id, current_state, session_id)

    async def set_attitude(self, user_id: str, attitude: str, session_id: Optional[str] = None):
        current_state = await self.db.get_user_state(user_id, session_id)
        current_state["attitude"] = attitude
        await self.db.update_user_state(user_id, current_state, session_id)

    async def set_relationship(self, user_id: str, relationship: str, session_id: Optional[str] = None):
        current_state = await self.db.get_user_state(user_id, session_id)
        current_state["relationship"] = relationship
        await self.db.update_user_state(user_id, current_state, session_id)

    async def get_favour_ranking(self, limit: int = 10) -> List[Dict[str, Any]]:
        return await self.db.get_favour_ranking(limit)

    async def get_dislike_ranking(self, limit: int = 10) -> List[Dict[str, Any]]:
        return await self.db.get_dislike_ranking(limit)
