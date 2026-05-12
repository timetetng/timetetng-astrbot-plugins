### 插件API开发文档 (经济, 商店, 昵称 & 好感度)

#### 概述

本文档旨在为其他插件的开发者提供与 **经济系统 (`EconomyAPI`)**、 **商店系统 (`ShopAPI`)**、 **昵称系统 (`NicknameAPI`)** 和 **好感度系统 (`FavourProAPI`)** 进行交互的指南。

**核心要点：**

  * 所有API方法均为**异步函数 (`async def`)**。
  * 在调用任何API方法时，**必须**使用 `await` 关键字。
  * API实例通过 `shared_services` 全局服务字典获取。

### 1\. 获取API实例

在您的插件代码中，您需要先从 `shared_services` 中获取API的实例。建议在需要使用时再获取，以确保获取到的是最新的服务实例。

```python
from astrbot.core.services import shared_services

# 获取经济系统API
economy_api = shared_services.get("economy_api")

# 获取商店系统API
shop_api = shared_services.get("shop_api")

# 获取昵称系统API
nickname_api = shared_services.get("nickname_api")

# --- 新增: 获取好感度系统API ---
favour_pro_api = shared_services.get("favour_pro_api")


# 使用前最好进行判断
if not economy_api:
    logger.error("未能获取经济系统API！")
    return

if not shop_api:
    logger.error("未能获取商店系统API！")
    return

# 对昵称API的调用是可选的
if not nickname_api:
    logger.warning("未能获取昵称系统API，将使用默认昵称。")

# --- 新增: 对好感度API的判断 ---
if not favour_pro_api:
    logger.warning("未能获取好感度系统API。")

```

-----

### 2\. 经济系统 (EconomyAPI)

`EconomyAPI` 由 `astrbot_plugin_sign` 插件提供，负责管理用户的金币余额和相关数据。

#### `async def get_coins(self, user_id: str) -> int`

查询指定用户的金币余额。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
  * **返回:** `int` - 用户的金币数量。如果用户不存在，返回 `0`。

#### `async def add_coins(self, user_id: str, amount: int, reason: str) -> bool`

为指定用户增加或减少金币。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `amount (int)`: 要变动的数量。正数为增加，负数为减少。
      * `reason (str)`: 本次金币变动的原因，将用于记录日志。
  * **返回:** `bool` - 操作是否成功。如果因余额不足导致扣款失败，将返回 `False`。

#### `async def set_coins(self, user_id: str, amount: int, reason: str) -> bool`

**[慎用]** 直接将用户的金币设置为一个特定值。通常仅用于管理员指令。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `amount (int)`: 要设定的目标金额，必须大于等于 `0`。
      * `reason (str)`: 操作原因。
  * **返回:** `bool` - 操作是否成功。

#### `async def get_user_profile(self, user_id: str) -> Optional[dict]`

获取用户的公开签到信息。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
  * **返回:** `dict` 或 `None`。成功时返回包含用户信息的字典（如 `user_id`, `nickname`, `coins`, `total_days` 等），用户不存在则返回 `None`。

#### `async def get_ranking(self, limit: int = 10) -> list`

获取金币排行榜。

  * **参数:**
      * `limit (int)`: (可选) 希望获取的榜单长度，默认为 `10`。
  * **返回:** `list` - 一个由字典组成的列表，每个字典代表一位榜上的用户。

#### `async def get_coin_history(self, user_id: str, limit: int = 5) -> list`

获取指定用户最近的金币变动历史。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `limit (int)`: (可选) 希望获取的记录条数，默认为 `5`。
  * **返回:** `list` - 一个由字典组成的列表，每个字典代表一条金币变动记录。

-----

### 3\. 商店系统 (ShopAPI)

`ShopAPI` 由 `shop_plugin` 插件提供，负责管理商品定义、用户库存以及物品的消耗。

#### `async def register_item(self, owner_plugin: str, item_id: str, name: str, description: str, price: int, daily_limit: int = 0)`

用于插件向商店注册一个可供出售的商品。**强烈建议**将此逻辑封装在管理员命令中，以避免热重载带来的时序问题。

  * **参数:**
      * `owner_plugin (str)`: 注册该物品的插件名称。
      * `item_id (str)`: 物品的唯一英文ID。
      * `name (str)`: 物品的显示名称。
      * `description (str)`: 物品的功能描述。
      * `price (int)`: 物品的售价。
      * `daily_limit (int)`: (可选) 每日限购数量，`0` 表示不限制。默认为 `0`。
  * **返回:** 无。

#### `async def get_item_details(self, identifier: str) -> Optional[Dict[str, Any]]`

根据物品的ID或名称获取其详细信息。这是让其他插件了解商品属性的核心API。

  * **参数:**
      * `identifier (str)`: 物品的唯一英文ID或中文名称。
  * **返回:** `dict` 或 `None` - 成功时返回包含商品所有属性的字典，如果找不到则返回 `None`。

#### `async def get_user_inventory(self, user_id: str) -> list`

获取指定用户的整个背包（物品列表）。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
  * **返回:** `list` - 一个由字典组成的列表，每个字典代表用户拥有的一个物品，包含 `item_id`, `name`, `description`, `quantity` 等键。

#### `async def has_item(self, user_id: str, item_id: str) -> bool`

检查用户是否拥有至少一个指定的物品。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `item_id (str)`: 要检查的物品的唯一ID。
  * **返回:** `bool` - 如果用户拥有该物品，返回 `True`，否则返回 `False`。

#### `async def consume_item(self, user_id: str, item_id: str, quantity: int = 1) -> bool`

消耗（移除）用户背包中的指定物品。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `item_id (str)`: 要消耗的物品的唯一ID。
      * `quantity (int)`: (可选) 要消耗的数量，默认为 `1`。
  * **返回:** `bool` - 如果用户拥有足够数量的物品并成功消耗，返回 `True`。如果物品数量不足，返回 `False`。

-----

### 4\. 昵称系统 (NicknameAPI)

`NicknameAPI` 由 `astrbot_plugin_nickname` 插件提供，用于获取用户设置的自定义昵称。

#### `async def get_nickname(self, user_id: str) -> Optional[str]`

获取单个用户的自定义昵称。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
  * **返回:** `str` 或 `None` - 如果用户设置了自定义昵称，则返回该昵称字符串；否则返回 `None`。
  * **示例:**
    ```python
    nickname_api = shared_services.get("nickname_api")
    display_name = event.get_sender_name() # 默认名
    if nickname_api:
        custom_name = await nickname_api.get_nickname(event.get_sender_id())
        if custom_name:
            display_name = custom_name

    yield event.plain_result(f"你好，{display_name}！")
    ```

#### `async def get_nicknames_batch(self, user_ids: List[str]) -> Dict[str, str]`

批量获取多个用户的自定义昵称，适用于排行榜等需要一次性查询多个用户的场景以提高效率。

  * **参数:**
      * `user_ids (List[str])`: 一个包含多个用户ID的列表。
  * **返回:** `Dict[str, str]` - 一个字典，键是用户ID，值是对应的自定义昵称。只包含在列表中找到了自定义昵称的用户。
  * **示例:**
    ```python
    # ranking_data 是从 EconomyAPI 获取的排行榜列表
    user_ids = [user['user_id'] for user in ranking_data]

    nickname_api = shared_services.get("nickname_api")
    custom_names = {}
    if nickname_api:
        custom_names = await nickname_api.get_nicknames_batch(user_ids)

    # 循环显示时，优先从 custom_names 中取值
    for user in ranking_data:
        display_name = custom_names.get(user['user_id']) or user['nickname']
        print(f"玩家: {display_name}, 分数: {user['coins']}")

-----

### 5\. 好感度系统 (FavourProAPI)

`FavourProAPI` 由 `FavourPro` 插件提供，负责管理机器人对用户的多维度好感度状态。

#### `async def get_user_state(self, user_id: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]`

获取用户的完整好感度状态。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `session_id (Optional[str])`: (可选) 会话ID，用于区分不同群聊/私聊场景下的好感度。
  * **返回:** `dict` 或 `None`。成功时返回包含 `favour`, `attitude`, `relationship` 的字典，如果用户无记录则返回 `None`。

#### `async def add_favour(self, user_id: str, amount: int, session_id: Optional[str] = None)`

为指定用户增加或减少好感度。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `amount (int)`: 要变动的数量。正数为增加，负数为减少。
      * `session_id (Optional[str])`: (可选) 会话ID。
  * **返回:** 无。

#### `async def set_favour(self, user_id: str, amount: int, session_id: Optional[str] = None)`

**[慎用]** 直接将用户的好感度设置为一个特定值。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `amount (int)`: 要设定的目标好感度值。
      * `session_id (Optional[str])`: (可选) 会话ID。
  * **返回:** 无。

#### `async def set_attitude(self, user_id: str, attitude: str, session_id: Optional[str] = None)`

设置机器人对用户的印象描述。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `attitude (str)`: 新的印象描述文本。
      * `session_id (Optional[str])`: (可选) 会话ID。
  * **返回:** 无。

#### `async def set_relationship(self, user_id: str, relationship: str, session_id: Optional[str] = None)`

设置机器人与用户的关系描述。

  * **参数:**
      * `user_id (str)`: 用户的唯一ID。
      * `relationship (str)`: 新的关系描述文本。
      * `session_id (Optional[str])`: (可选) 会话ID。
  * **返回:** 无。

#### `async def get_favour_ranking(self, limit: int = 10) -> List[Dict[str, Any]]`

获取好感度排行榜。

  * **参数:**
      * `limit (int)`: (可选) 希望获取的榜单长度，默认为 `10`。
  * **返回:** `list` - 一个由字典组成的列表，每个字典包含 `user_id` 和 `favour`。