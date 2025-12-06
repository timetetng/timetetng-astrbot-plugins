# AstrBot 表情包管理器 Lite

Fork from [astrbot_plugin_meme_manager_lite](https://github.com/ctrlkk/astrbot_plugin_meme_manager_lite)

一个轻量级的 AstrBot 插件，用于管理和发送表情包。该插件允许 LLM
在回答中智能地插入表情包，使对话更加生动有趣。
允许LLM主动发送表情包，并知道自己可以发送表情包。

## 功能特点

- 📦 **轻量级设计**：简单易用，稳定可靠，不占用过多系统资源
- 🎭 **多格式支持**：支持 PNG、JPG、JPEG、GIF、WEBP 等多种图片格式
- 🎲 **随机选择**：支持每个表情包目录下多张图片随机选择，增加多样性
- ⚙️ **灵活配置**：可配置每条消息最大表情包数量，适应不同使用场景
- 🔄 **自动初始化**：首次使用时自动复制默认配置，无需手动设置
- 📚 **配置兼容**：对
  [anka-afk/astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager)
  的配置兼容，可轻松迁移
- 🚀 **即装即用**：安装后即可使用默认表情包，无需额外配置

## 安装

### 方法一：直接下载

1. 下载最新版本的 `astrbot_plugin_meme_manager_lite`
2. 将其解压到 AstrBot 的插件目录 `data/plugins` 下

### 方法二：Git 克隆

```bash
cd {AstrBot根目录}/data/plugins
git clone {插件仓库地址}
```

### 方法三：通过 AstrBot 插件市场

直接安装使用

## 使用方法

### 1. 自动初始化（推荐）

插件首次启动时会自动进行初始化：

- 自动创建数据目录 `data/plugin_data/astrbot_plugin_meme_manager_lite`
- 自动复制默认表情包配置文件 `memes_data.json`
- 自动复制默认表情包图片到 `memes/` 目录

初始化完成后，插件即可立即使用，包含多种默认表情包。

### 2. 手动添加表情包

如果您想添加自定义表情包，请按照以下步骤操作：

1. **创建表情包目录**：
   ```
   {AstrBot根目录}data/plugin_data/astrbot_plugin_meme_manager_lite/memes/{表情包名称}/
   ```

2. **添加表情包图片**： 将表情包图片文件放入对应的表情包目录中
   - 支持的格式：`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`
   - 每个表情包目录可以包含多张图片，插件会随机选择

3. **编辑表情包数据文件**： 编辑或创建 `memes_data.json` 文件：
   ```
   {AstrBot根目录}data/plugin_data/astrbot_plugin_meme_manager_lite/memes_data.json
   ```

4. **添加表情包描述**： 在 `memes_data.json` 中添加表情包描述，格式如下：
   ```json
   {
      "happy": "用于成功确认、积极反馈或庆祝场景",
      "sad": "表达伤心、歉意或遗憾的场景",
      "surprised": "响应超出预期的信息",
      "confused": "请求澄清或表达理解障碍时",
      "自定义表情包名称": "自定义表情包描述，告诉LLM何时使用此表情包"
   }
   ```

### 3. 配置选项

在 AstrBot 的插件配置文件中，您可以设置以下选项：

```json
{
   "max_memes_per_message": 1
}
```

- `max_memes_per_message`: 每条消息最多使用的表情包数量（默认为 1）

### 4. 在对话中使用

当您与 AstrBot 对话时，LLM
会根据对话内容自动选择合适的表情包插入到回复中。插件会通过系统提示词指导 LLM
在适当的时候使用表情包。

## 默认表情包

插件包含以下默认表情包：

| 表情包名称 | 使用场景                             |
| ---------- | ------------------------------------ |
| angry      | 当对话包含抱怨、批评或激烈反对时使用 |
| happy      | 用于成功确认、积极反馈或庆祝场景     |
| sad        | 表达伤心、歉意、遗憾或安慰场景       |
| surprised  | 响应超出预期的信息                   |
| confused   | 请求澄清或表达理解障碍时             |
| cpu        | 技术讨论中表示思维卡顿时             |
| fool       | 自嘲或缓和气氛的幽默场景             |
| like       | 表达对事物或观点的喜爱               |
| shy        | 涉及隐私话题或收到赞美时             |
| work       | 工作流程相关场景                     |
| reply      | 等待用户反馈时                       |
| meow       | 卖萌或萌系互动场景                   |
| baka       | 轻微责备或吐槽                       |
| morning    | 早安问候专用                         |
| sleep      | 涉及作息场景                         |
| sigh       | 表达无奈、无语或感慨                 |

## 常见问题

### Q: 如何禁用某个表情包？

A: 在 `memes_data.json` 文件中删除对应的表情包条目即可。

### Q: 如何修改表情包的使用频率？

A: 可以通过调整 `max_memes_per_message` 配置项来控制每条消息中表情包的最大数量。

### Q: 插件不工作怎么办？

A: 请检查以下几点：

1. 确保插件已正确安装并启用
2. 检查数据目录是否存在且可写
3. 查看 AstrBot 的日志文件，查找相关错误信息

### Q: 如何备份我的自定义表情包？

A: 备份整个 `data/plugin_data/astrbot_plugin_meme_manager_lite` 目录即可。

## 许可证

AGPL License
