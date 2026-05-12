# astrbot_plugin_bili_at_notifier

**版本:** 1.1.0

Astrbot 插件——定时检查多个 Bilibili 账号的 @ 消息，并将相关视频推送到指定群聊。让群友陪你享受那个喜欢 @ 拉屎给你的朋友吧。

## ✨ 功能特性

* **多账号支持**：可同时监控多个 Bilibili 账号的 @ 消息。
* **定时轮询**：自动定时检查新消息。
* **视频推送**：自动解析 @ 消息中的 B 站视频，并将其作为视频或文件推送到指定群聊。
* **文本通知**：在推送视频前，会先发送一条包含 @ 者和评论内容的文本通知。

## ⚙️ 配置说明

安装插件后，请在 AstrBot 的配置文件（或 WebUI 的插件配置页面）中找到 `astrbot_plugin_bili_at_notifier` 的配置项。

### 如何获取 Bilibili Cookie

本插件需要 Bilibili 账号的 `SESSDATA` 和 `bili_jct` (CSRF Token) 才能正常获取 @ 消息。

请按照以下步骤**手动**获取：

1.  在您的电脑浏览器上，登录 `www.bilibili.com`。
2.  登录成功后，按 `F12` 键打开“开发者工具”。
3.  切换到 **Application** (Chrome/Edge 浏览器) 或 **Storage** (Firefox 浏览器) 标签页。
4.  在左侧的菜单中，找到 `Cookies`（或“存储”->“Cookie”）选项，并点击 `https://www.bilibili.com`。
5.  在右侧会显示一个列表，您需要找到以下两项：
    * `SESSDATA`
    * `bili_jct`
6.  分别**双击**这两项的 `Value`（值）列，将其内容完整复制出来，填入插件配置中对应的 `account_SESSDATA` 和 `account_bili_jct` 列表的相应位置。

**注意**：
* Cookie 具有时效性，如果插件提示 Cookie 失效（日志中可能出现 -101 错误），您需要按照上述步骤重新获取并更新配置。
* 请妥善保管您的 Cookie，不要泄露给他人。

### 配置项列表

以下是 `_conf_schema.json` 中定义的所有配置项：

| 配置项 | 类型 | 描述 | 默认值 |
| :--- | :--- | :--- | :--- |
| `account_labels` | list | 账号标签列表。用于日志和通知中区分是哪个账号收到的消息。**列表顺序必须**与其他账号列表（SESSDATA, bili_jct, User-Agent）一一对应。 | `["默认账号"]` |
| `account_SESSDATA` | list | **(必填)** 账号 SESSDATA 列表。按顺序填入每个账号的 SESSDATA。 | `[]` |
| `account_bili_jct` | list | **(必填)** 账号 bili_jct 列表。按顺序填入每个账号的 bili_jct。 | `[]` |
| `account_user_agents` | list | (可选) 账号 User-Agent 列表。如果某项留空，则该账号使用下方的“全局 User-Agent”。 | `[""]` |
| `global_user_agent` | string | 全局 User-Agent。建议保持默认或使用您自己浏览器的 UA。 | (默认 UA 字符串) |
| `target_umos` | list | **(必填)** 目标推送 UMO 列表。需要推送通知的目标会话 UMO (Unified Message Origin) 列表。 | `[]` |
| `polling_interval` | int | 轮询间隔（秒）。检查所有账号 @ 消息的总周期频率。 | `60` |
| `bili_quality` | int | B 站视频清晰度 (视频解析用)。16: 360P, 32: 480P, 64: 720P, 80: 1080P, 112: 1080P+, 120: 4K。 | `32` |
| `bili_use_login` | bool | 是否使用登录状态下载 B 站视频。启用后将**尝试自动扫码登录**（在 Bot 后台）以下载高清视频。建议**禁用**以避免频繁登录。 | `false` |
| `max_video_size` | int | 最大视频大小（MB）。超过此大小的视频将尝试以文件形式发送。 | `100` |
| `send_delay` | float | 推送间隔（秒）。推送多条消息时，每条消息之间的发送延迟。 | `1.0` |
| `nap_server_address` | string | Napcat 服务地址 (视频解析用)。若视频解析插件与 AstrBot 在同一服务器，请填写 `localhost`。 | `localhost` |
| `nap_server_port` | int | Napcat 接收文件端口 (视频解析用)。如果 Napcat 服务在同一服务器，此项通常无需修改。 | `3658` |

## 😊 致谢

### 参考项目:

* **AstrBot 视频解析插件**: https://github.com/miaoxutao123/astrbot_plugin_videos_analysis

* **astrbot_plugin_bilibili_adapter**: https://github.com/Hina-Chat/astrbot_plugin_bilibili_adapter


## ⚠️ 依赖项

* **FFmpeg**: 视频解析和下载功能（由 `bili_get.py` 提供）依赖 `ffmpeg` 进行音视频合成。请确保您的服务器上已安装 `ffmpeg` 并将其添加到了系统环境变量 (PATH) 中。

## 📄 许可证

本插件使用 [GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007](LICENSE) 许可证。