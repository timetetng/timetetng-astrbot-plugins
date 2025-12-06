### **兑换码插件 Web API 使用文档**

#### **概述**

本 API 允许您通过编程方式，从外部服务（如网站后端、游戏服务器、管理脚本等）安全地创建兑-换码。所有通过此 API 创建的兑-换码都将实时同步到 AstrBot 插件中，可供用户立即兑换。

#### **前置条件**

在使用本 API 之前，请确保您已经完成了以下配置：

1.  **插件已正确安装并运行**：确保 `astrbot_plugin_redeem` 插件已在 AstrBot 中成功加载。
2.  **API 服务已启用**：在 AstrBot WebUI 的插件管理页面，找到本插件并进入配置，确保 **`enable_api`** 选项已打开。
3.  **配置 API 密钥**：在同一配置页面，为 **`api_key`** 设置一个长且随机的安全字符串。这是您调用 API 的唯一凭证。
4.  **确认主机和端口**：记下您在配置中设置的 **`host`** 和 **`port`**。
5.  **防火墙/安全组放行**：确保您的服务器防火墙或云服务商的安全组策略已放行您配置的端口（例如`30007`）。

-----

### **接口详情：创建兑换码**

#### `POST /api/redeem/create`

此接口用于创建一个新的兑换码。

#### **认证 (Authentication)**

所有请求都必须在请求头 (Header) 中包含有效的 API 密钥。

  - **Header 名称**: `X-API-Key`
  - **Header 值**: 您在插件配置中设置的安全字符串。

#### **请求头 (Headers)**

| Header          | 值                      | 描述           |
| --------------- | ----------------------- | -------------- |
| `Content-Type`  | `application/json`      | 请求体格式。   |
| `X-API-Key`     | `您设置的安全字符串`     | 用于身份验证。 |

#### **请求体 (Request Body)**

请求体必须是一个包含以下字段的 JSON 对象：

| 参数名      | 类型   | 是否必须 | 描述                                                         |
| ----------- | ------ | -------- | ------------------------------------------------------------ |
| `code_type` | string | 是       | 兑换码类型。必须是 `universal` (通用码，每人一次) 或 `single` (一次性码，仅限一人)。 |
| `amount`    | integer| 是       | 奖励的金币数量。必须是大于 0 的整数。                        |
| `duration`  | string | 是       | 有效期。格式为 `数字` + `单位`，单位可以是 `d` (天), `h` (小时), `m` (分钟)。例如：`"7d"`, `"24h"`, `"30m"`。 |

**请求体示例:**

```json
{
    "code_type": "single",
    "amount": 5000,
    "duration": "7d"
}
```

#### **响应 (Responses)**

**✅ 成功响应 (`200 OK`)**

请求成功后，API 会返回一个包含新生成的兑换码详细信息的 JSON 对象。

| 参数名          | 类型   | 描述                                     |
| --------------- | ------ | ---------------------------------------- |
| `status`        | string | 固定为 `"success"`。                       |
| `code`          | string | 新生成的唯一兑换码字符串。               |
| `type`          | string | 您请求的兑换码类型 (`universal` 或 `single`)。 |
| `reward_amount` | integer| 奖励的金币数量。                           |
| `expires_at_str`| string | 兑换码的过期时间 (格式: `YYYY-MM-DD HH:MM:SS`)。 |
| `expires_at_ts` | float  | 兑换码的过期时间戳。                     |

**成功响应示例:**

```json
{
    "status": "success",
    "code": "A1B2C3D4E5F6",
    "type": "single",
    "reward_amount": 5000,
    "expires_at_str": "2025-10-15 20:30:00",
    "expires_at_ts": 1729024200.12345
}
```

**❌ 失败响应**

  - **`401 Unauthorized`**: API 密钥缺失或错误。
    ```json
    {
        "detail": "Invalid or missing API Key"
    }
    ```
  - **`422 Unprocessable Entity`**: 请求体验证失败（例如 `code_type` 不是指定值，`amount` 小于等于0，`duration` 格式错误等）。
    ```json
    {
        "detail": [
            {
                "loc": [ "body", "amount" ],
                "msg": "ensure this value is greater than 0",
                "type": "value_error.number.not_gt"
            }
        ]
    }
    ```

-----

### **使用示例 (curl)**

请将以下命令中的 `{your_server_ip}`、`{port}` 和 `{your_api_key}` 替换为您的实际配置。

```bash
curl -X POST "http://{your_server_ip}:{port}/api/redeem/create" \
-H "Content-Type: application/json" \
-H "X-API-Key: {your_api_key}" \
-d '{
    "code_type": "universal",
    "amount": 100,
    "duration": "30m"
}'
```