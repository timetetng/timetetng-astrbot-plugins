# 正确的 astrbot/data/plugins/common/forwarder.py 文件内容

from typing import List
from astrbot.api.message_components import Node, Nodes, Plain, BaseMessageComponent


class Forwarder:
    def __init__(self):
        """
        初始化Forwarder实例。
        机器人信息已在此处配置，无需外部传入。
        """
        # --- ↓↓↓ 请确保这里是您自己的机器人信息 ↓↓↓ ---
        self.bot_uin: str = "3847288780"  # 修改为您的机器人QQ号
        self.bot_name: str = "菲比"  # 修改为您的机器人昵称
        # --- ↑↑↑ 请确保这里是您自己的机器人信息 ↑↑↑ ---

    def create_from_text(self, text: str) -> Nodes:
        """
        【功能一】将一段长文本包装成单条合并转发消息。
        :param text: 要发送的长文本内容。
        :return: 一个可以直接发送的 Nodes 对象。
        """
        message = {
            "uin": self.bot_uin,
            "name": self.bot_name,
            "content": [Plain(text=text)],
        }
        return self._create_from_messages([message])

    def create_from_contents(self, contents: List[List[BaseMessageComponent]]) -> Nodes:
        """
        【功能二】将一个由消息组件列表构成的列表，包装成合并转发消息。
        :param contents: 消息内容列表。外层列表的每个元素是一条独立消息，
                         内层列表是这条消息的消息组件。
        :return: 一个可以直接发送的 Nodes 对象。
        """
        messages = []
        for content_list in contents:
            message = {
                "uin": self.bot_uin,
                "name": self.bot_name,
                "content": content_list,
            }
            messages.append(message)
        return self._create_from_messages(messages)

    def _create_from_messages(self, messages: List[dict]) -> Nodes:
        """
        【内部方法】将标准格式的消息字典列表，包装成合并转发消息容器。
        """
        node_list = []
        for msg in messages:
            node = Node(
                uin=msg.get("uin", 0),
                name=msg.get("name", "未知用户"),
                content=msg.get("content", []),
            )
            node_list.append(node)
        return Nodes(nodes=node_list)
