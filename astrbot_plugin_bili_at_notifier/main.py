import asyncio
import os
import time
from datetime import datetime

from astrbot.api import AstrBotConfig, logger
from astrbot.api.all import *
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Plain, Video

# 尝试从当前插件目录导入复制过来的文件
try:
    from .bili_get import process_bili_video
    from .file_send_server import send_file as nap_send_file  # 导入NAP发送函数
    VIDEO_ANALYSIS_ENABLED = True
    logger.info("成功从本地导入视频解析功能。")
except ImportError as e:
    logger.warning(f"无法从本地导入视频解析模块 (bili_get.py, file_send_server.py)，视频推送功能将受限: {e}")
    process_bili_video = None
    nap_send_file = None
    VIDEO_ANALYSIS_ENABLED = False

from .bili_api import BiliApiClient


# --- 辅助函数 ---
def get_account_key(account_config: dict) -> str:
    """生成唯一的账号标识符，用于字典键"""
    # 优先使用标签，若无标签或重复，则结合 SESSDATA 前几位
    label = account_config.get("account_label", "default")
    sess_prefix = account_config.get("SESSDATA", "")[:8]
    return f"{label}_{sess_prefix}"

# --- 插件主类 ---
@register(
    "astrbot_plugin_bili_at_notifier",
    "timetetng",
    "定时检查多个 Bilibili 账号的 @ 消息，并将相关视频推送到指定群聊。让群友陪你享受那个喜欢 @ 拉屎给你的朋友吧",
    "1.1.0",
    "https://github.com/your_username/astrbot_plugin_bili_at_notifier"
)
class BiliAtNotifierPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._polling_task: asyncio.Task | None = None

        # --- 多账号管理 ---
        # 从扁平化的列表中读取账号信息
        labels = self.config.get("account_labels", [])
        sessdata_list = self.config.get("account_SESSDATA", [])
        bili_jct_list = self.config.get("account_bili_jct", [])
        user_agents = self.config.get("account_user_agents", [])

        self.accounts_config_internal: list[dict] = [] # 内部使用的组合后的配置列表
        self.api_clients: dict[str, BiliApiClient] = {} # key: account_key
        self.last_at_ids: dict[str, int] = {} # key: account_key
        self.is_first_run: dict[str, bool] = {} # key: account_key
        # --- ------------ ---

        # 从配置中读取全局参数
        self.global_user_agent = self.config.get("global_user_agent")
        self.target_umos = self.config.get("target_umos", [])
        self.polling_interval = self.config.get("polling_interval", 60)
        self.bili_quality = self.config.get("bili_quality", 32)
        self.bili_use_login = self.config.get("bili_use_login", False)
        self.max_video_size = self.config.get("max_video_size", 100) * 1024 * 1024 # 转为字节
        self.send_delay = self.config.get("send_delay", 1.0)

        # 检查关键列表长度是否一致
        if not sessdata_list or not bili_jct_list:
             logger.error("账号 SESSDATA 或 bili_jct 列表为空，插件无法启动。")
             return
        if len(sessdata_list) != len(bili_jct_list):
            logger.error("账号 SESSDATA 和 bili_jct 列表长度不一致，请检查配置。")
            return

        # 组合账号配置
        num_accounts = len(sessdata_list)
        for i in range(num_accounts):
            account_cfg = {
                "account_label": labels[i] if i < len(labels) else f"账号{i+1}",
                "SESSDATA": sessdata_list[i],
                "bili_jct": bili_jct_list[i],
                "user_agent": user_agents[i] if i < len(user_agents) and user_agents[i] else self.global_user_agent
            }
            # 基础验证
            if not account_cfg["SESSDATA"] or not account_cfg["bili_jct"]:
                 logger.warning(f"跳过索引 {i} 的账号配置，因为 SESSDATA 或 bili_jct 为空。")
                 continue
            if not account_cfg["user_agent"]:
                 logger.warning(f"跳过索引 {i} (标签: {account_cfg['account_label']}) 的账号配置，因为 User-Agent 为空（请检查账号配置或全局配置）。")
                 continue

            self.accounts_config_internal.append(account_cfg) # 加入内部列表

        if not self.accounts_config_internal:
            logger.error("没有有效的 Bilibili 账号配置，插件无法启动。")
            return
        if not self.target_umos:
            logger.warning("未配置目标推送 UMO (target_umos)，@ 消息将不会被推送。")

        valid_accounts = 0
        # 初始化每个账号的 Bilibili API 客户端和状态 (使用内部组合的配置)
        for account_cfg in self.accounts_config_internal:
            account_label = account_cfg["account_label"] # 使用组合后的标签
            user_agent = account_cfg["user_agent"]
            account_key = get_account_key(account_cfg) # 获取唯一键

            try:
                client = BiliApiClient(
                    sessdata=account_cfg["SESSDATA"],
                    bili_jct=account_cfg["bili_jct"],
                    user_agent=user_agent,
                )
                self.api_clients[account_key] = client
                self.last_at_ids[account_key] = 0
                self.is_first_run[account_key] = True
                logger.info(f"成功初始化 Bilibili 账号: '{account_label}' (Key: {account_key})")
                valid_accounts += 1
            except ValueError as e:
                logger.error(f"初始化账号 '{account_label}' 的 BiliApiClient 失败: {e}")
            except Exception as e:
                logger.error(f"初始化账号 '{account_label}' 时发生未知错误: {e}", exc_info=True)

        if valid_accounts == 0:
            logger.error("没有成功初始化的 Bilibili 账号，插件无法运行。")
            return

        # 启动轮询任务
        try:
            self._polling_task = asyncio.create_task(self.poll_at_mentions())
            logger.info(f"Bilibili @ 消息监听插件已启动 ({valid_accounts}个账号)，开始轮询...")
        except Exception as e:
            logger.error(f"启动轮询任务时发生错误: {e}", exc_info=True)

    async def poll_at_mentions(self):
        """定时轮询所有配置账号的 @ 消息 API"""
        logger.info("轮询任务启动，初始延迟5秒...")
        await asyncio.sleep(5) # 初始延迟

        while True:
            logger.info(f"开始新一轮 @ 消息检查 (共 {len(self.api_clients)} 个账号)...")
            account_keys = list(self.api_clients.keys()) # 获取当前所有有效账号的 key

            for account_key in account_keys:
                api_client = self.api_clients.get(account_key)

                # 修复：使用 self.accounts_config_internal 获取正确的标签
                account_label = "未知标签"
                for acc in self.accounts_config_internal:
                     if get_account_key(acc) == account_key:
                           account_label = acc.get("account_label", "未知标签")
                           break

                if not api_client:
                    logger.warning(f"账号 '{account_label}' (Key: {account_key}) 的客户端实例丢失，跳过本次检查。")
                    continue

                last_id = self.last_at_ids.get(account_key, 0)
                is_first = self.is_first_run.get(account_key, True)

                new_messages = []
                has_more = True
                next_cursor_id = None
                next_cursor_time = None
                current_max_id_this_poll = 0 # 记录本轮此账号获取到的最大ID
                page_count = 1

                try:
                    while has_more:
                        at_data = await api_client.get_at_mentions(cursor_id=next_cursor_id, cursor_time=next_cursor_time)

                        if at_data is None:
                            logger.warning(f"账号 '{account_label}': 获取 @ 消息失败 (API返回None)，跳过此账号本轮检查。")
                            has_more = False
                            break # 停止此账号的本轮检查

                        items = at_data.get("items", [])
                        cursor = at_data.get("cursor", {})
                        is_end = cursor.get("is_end", True)

                        if not items:
                            has_more = False
                        else:
                            page_max_id = max(item["id"] for item in items)
                            current_max_id_this_poll = max(current_max_id_this_poll, page_max_id)

                            if is_first:
                                self.last_at_ids[account_key] = current_max_id_this_poll # 首次运行，记录最新 ID
                                logger.info(f"账号 '{account_label}': 首次运行，记录最新消息 ID: {self.last_at_ids[account_key]} (来自第 {page_count} 页)，本次不推送。")
                                self.is_first_run[account_key] = False
                                has_more = False # 首次运行只获取第一页
                            else:
                                batch_new_messages = []
                                should_stop_paging = False
                                for item in items:
                                    if item["id"] > last_id:
                                        batch_new_messages.append(item)
                                    else:
                                        should_stop_paging = True
                                        break

                                new_messages.extend(reversed(batch_new_messages))

                                if should_stop_paging or is_end:
                                    has_more = False
                                else:
                                    next_cursor_id = cursor.get("id")
                                    next_cursor_time = cursor.get("time")
                                    page_count += 1
                                    if next_cursor_id is None or next_cursor_time is None:
                                        logger.warning(f"账号 '{account_label}': 分页 cursor 信息不完整，停止分页。Cursor: {cursor}")
                                        has_more = False

                    # 循环结束 (while has_more)

                    if new_messages:
                        logger.info(f"账号 '{account_label}': 共发现 {len(new_messages)} 条新 @ 消息，准备推送...")
                        await self.process_and_send_messages(new_messages, account_label) # 传入账号标签

                        self.last_at_ids[account_key] = max(last_id, current_max_id_this_poll) # 更新最新处理的消息ID
                        logger.info(f"账号 '{account_label}': 推送完成，更新 last_at_id 为 {self.last_at_ids[account_key]}")

                    elif not is_first:
                         if current_max_id_this_poll > last_id:
                             logger.info(f"账号 '{account_label}': 检查到本轮最大ID ({current_max_id_this_poll}) 大于旧ID ({last_id})，但无消息推送（可能全部被过滤）。更新 last_at_id。")
                             self.last_at_ids[account_key] = current_max_id_this_poll

                except asyncio.CancelledError:
                    logger.info("轮询任务被取消。")
                    return # 直接退出任务
                except Exception as e:
                    logger.error(f"检查账号 '{account_label}' 时发生错误: {e}", exc_info=True)
                    await asyncio.sleep(5)

                await asyncio.sleep(1) # 每个账号检查完后稍微等待一下

            # 所有账号检查完毕后，等待下一个轮询周期
            logger.info(f"所有账号检查完毕，将在 {self.polling_interval} 秒后进行下一轮检查。")
            await asyncio.sleep(self.polling_interval)


    async def process_and_send_messages(self, messages: list, account_label: str): # 增加 account_label 参数
        """处理 @ 消息，先发送文本通知，再发送视频文件到目标 UMO"""
        if not self.target_umos:
            return

        for msg_data in messages:
            try:
                item_info = msg_data.get("item", {})
                user_info = msg_data.get("user", {})
                video_url = item_info.get("uri")
                source_content = item_info.get("source_content", "无评论内容")
                sender_name = user_info.get("nickname", "未知用户")
                at_time_ts = msg_data.get("at_time", int(time.time()))
                at_time_str = datetime.fromtimestamp(at_time_ts).strftime("%Y-%m-%d %H:%M:%S")

                if not video_url or not video_url.startswith("http"):
                    logger.warning(f"账号 '{account_label}': 消息 ID {msg_data.get('id')} 不包含有效的视频链接，跳过。")
                    continue

                logger.info(f"账号 '{account_label}': 正在处理来自 {sender_name} 的 @ 消息 (ID: {msg_data.get('id')}), 视频链接: {video_url}")

                # 1. 构造并发送文本通知消息 (加入账号标签)
                notification_text = f"📢 账号「{account_label}」收到一坨屎！\n" \
                                    f"👤 谁拉的: {sender_name}"
                notification_chain = MessageChain([Plain(notification_text)])

                for target_umo in self.target_umos:
                    try:
                        logger.info(f"账号 '{account_label}': 准备推送 @ 文本通知 (ID: {msg_data.get('id')}) 到 {target_umo}")
                        await self.context.send_message(target_umo, notification_chain)
                        logger.info(f"账号 '{account_label}': 成功推送 @ 文本通知 (ID: {msg_data.get('id')}) 到 {target_umo}")
                        await asyncio.sleep(0.5)
                    except Exception as send_e:
                        logger.error(f"推送 @ 文本通知到 {target_umo} 失败: {send_e}", exc_info=True)

                await asyncio.sleep(self.send_delay)

                # 2. 解析并准备发送视频/文件
                media_component = None

                if VIDEO_ANALYSIS_ENABLED and process_bili_video:
                    logger.info(f"账号 '{account_label}': 开始使用 video_analysis 插件解析: {video_url}")

                    analysis_result = await process_bili_video(
                        video_url,
                        download_flag=True,
                        quality=self.bili_quality,
                        use_login=self.bili_use_login,
                        event=None
                    )

                    if analysis_result and analysis_result.get("video_path"):
                        video_path = analysis_result["video_path"]
                        video_title = analysis_result.get("title", "未知标题")
                        logger.info(f"账号 '{account_label}': 视频解析成功: {video_title}, 路径: {video_path}")

                        nap_file_path = video_path
                        nap_server_address = self.config.get("nap_server_address", "localhost") # NAP配置现在是全局的
                        nap_server_port = self.config.get("nap_server_port")

                        if nap_server_address != "localhost" and nap_send_file and nap_server_port:
                            try:
                                logger.info(f"尝试通过NAP发送文件: {video_path} 到 {nap_server_address}:{nap_server_port}")
                                nap_file_path = await nap_send_file(video_path, HOST=nap_server_address, PORT=nap_server_port)
                                logger.info(f"NAP文件路径: {nap_file_path}")
                            except Exception as nap_e:
                                logger.error(f"通过NAP发送文件失败: {nap_e}, 将尝试使用本地路径。")
                                nap_file_path = video_path
                        elif nap_server_address != "localhost":
                            logger.warning("配置了NAP服务器地址但端口未配置或发送函数未导入，使用本地路径。")

                        try:
                            file_size = os.path.getsize(video_path)

                            if file_size > self.max_video_size:
                                logger.warning(f"视频文件过大 ({file_size / 1024 / 1024:.2f} MB > {self.max_video_size / 1024 / 1024:.2f} MB)，尝试作为文件发送。")
                                media_component = File(file=nap_file_path, name=os.path.basename(video_path))
                            else:
                                media_component = Video.fromFileSystem(path=nap_file_path)
                            logger.info(f"视频组件创建成功: {media_component}")

                        except FileNotFoundError:
                            logger.error(f"视频文件未找到: {video_path} (用于检查大小) 或 {nap_file_path} (用于创建组件)，无法发送视频。")
                            media_component = Plain(f"❌ 视频文件丢失: {video_title}\n🔗 原始链接: {video_url}")
                        except Exception as comp_e:
                            logger.error(f"创建媒体组件时出错: {comp_e}", exc_info=True)
                            media_component = Plain(f"❌ 处理视频失败: {video_title}\n🔗 原始链接: {video_url}")

                    else:
                        logger.warning(f"账号 '{account_label}': 视频解析失败或未找到 video_path: {video_url}")
                        media_component = Plain(f"⚠️ 视频解析失败，请手动查看: {video_url}")

                else:
                    if not VIDEO_ANALYSIS_ENABLED:
                         logger.warning(f"账号 '{account_label}': 视频解析功能未启用 (VIDEO_ANALYSIS_ENABLED=False)，发送链接。")
                    else:
                         logger.warning(f"账号 '{account_label}': process_bili_video 函数未导入 (bili_get.py 可能缺失或导入失败)，发送链接。")
                    media_component = Plain(f"🔗 相关视频 (解析功能未启用): {video_url}")

                # 3. 发送视频/文件或提示信息
                if media_component:
                    message_chain_to_send = MessageChain([media_component])
                    for target_umo in self.target_umos:
                        try:
                            logger.info(f"账号 '{account_label}': 准备推送视频/文件 (来自 @ 消息 ID: {msg_data.get('id')}) 到 {target_umo}")
                            await self.context.send_message(target_umo, message_chain_to_send)
                            logger.info(f"账号 '{account_label}': 成功推送视频/文件 (来自 @ 消息 ID: {msg_data.get('id')}) 到 {target_umo}")
                            await asyncio.sleep(self.send_delay)
                        except Exception as send_e:
                            logger.error(f"推送视频/文件到 {target_umo} 失败: {send_e}", exc_info=True)
                else:
                    logger.warning(f"账号 '{account_label}': 没有可发送的媒体组件 (来自 @ 消息 ID: {msg_data.get('id')})，跳过发送。")

            except Exception as outer_e:
                logger.error(f"账号 '{account_label}': 处理 @ 消息 (ID: {msg_data.get('id')}) 时发生外部错误: {outer_e}", exc_info=True)

            await asyncio.sleep(1.0)

    async def terminate(self):
        """插件终止时取消任务并关闭所有客户端"""
        logger.info("Bilibili @ 消息监听插件正在停止...")
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                logger.info("轮询任务已取消。")

        # 关闭所有 API 客户端
        clients_to_close = list(self.api_clients.values())
        self.api_clients.clear() # 清空引用
        for client in clients_to_close:
            try:
                await client.close()
            except Exception as e:
                logger.error(f"关闭 BiliApiClient 时出错: {e}")

        logger.info("Bilibili @ 消息监听插件已停止。")
