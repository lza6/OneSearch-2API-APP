# OneSearch 2API — APK 逆向与 OpenAI 兼容网关

> OneSearch（`com.chat.chatai.chatbot.aichatbot` v1.5.5）协议完整还原 + OpenAI 兼容网关。
> 上游实为 **Begamob** 白标统一多模型网关，逆向实测 **68 个模型全部可用**，支持**真 SSE 流式**、多模态读图、Google 联网搜索。

本仓库仅用于**协议研究与技术学习**（安全研究 / 互操作性研究）。所有密钥、签名逻辑均来自公开可下载 APK 的静态分析。请勿用于任何违反上游服务条款或法律法规的用途。

---

## 一、成果概览

| 项目 | 结论 |
|------|------|
| 应用包名 | `com.chat.chatai.chatbot.aichatbot`（OneSearch v1.5.5 / versionCode 15501） |
| 上游域名 | `https://chat-ai.begamob.com/`（Spring Boot） |
| 厂商生态 | 与 ChatAI（`chatapi.begamob.com`）同属 **Begamob / iKame** 白标体系，共用签名密钥 |
| 认证方式 | **自签 HS256 JWT**（密钥内嵌 APK），聊天**无需登录 / 无需号池 / 无需设备注册** |
| 聊天端点 | `POST /api/v3/chat/stream`（真 SSE 流式） |
| 模型数量 | **68 个可用**（GPT-5 系列 / Claude 4.x / Gemini 3 / Grok-4 / Llama 4 / DeepSeek 全系） |
| 流式 | ✅ 上游原生 SSE（非网关模拟），TTFT 约 3s |
| 多模态 | ✅ `image_url` 读图（服务端自行下载图片） |
| 联网搜索 | ✅ `tools:["web_search"]`（Google grounding，返回来源 site） |
| 限流 | ⚠️ 上游有 **Cloudflare IP 级限流**，高频/高并发触发 429（见下"限流情报"） |
| 上下文 | 至少 6 万中文字符实测通过（未精确测硬上限） |

---

## 二、协议详解

### 2.1 认证：自签 JWT（HS256）

APK 中 `ApiKeyFactoryImpl.generateToken()` 用硬编码密钥自签 JWT，**不依赖任何登录接口**：

```
SECRET_KEY   = "stteam-ikameglobal-chatapiopenai"   # BuildConfig.SECRET_KEY
BUNDLE_ID    = "com.chat.chatai.chatbot.aichatbot"   # BuildConfig.APPLICATION_ID
VERSION      = "1.5.5"
```

JWT claims（**注意 iat 是毫秒、exp 是秒**，与 jjwt `expiration` 冗余但服务端都校验）：

```json
{
  "iat": <当前毫秒时间戳>,
  "exp": <当前秒 + 86400>,
  "bundleId": "com.chat.chatai.chatbot.aichatbot",
  "os": "Android",
  "versionApp": "1.5.5",
  "timezone": "Asia/Shanghai"
}
```

签名：`HMAC-SHA256(base64url(header) + "." + base64url(payload), SECRET_KEY)`。

> 关联端点对比：普通设备接口（模型列表等）走**登录 accessToken**（`POST /api/v1/auth/login {deviceId, type:"guest"}`），
> 但**聊天流式 `/api/v3/chat/stream` 只认自签 JWT**（accessToken 会 401）。因此聊天零号池。

### 2.2 必需请求头

```
Authorization: Bearer <自签JWT>
user-header: bundleId:com.chat.chatai.chatbot.aichatbot/versionApp:1.5.5/OS:Android/osVersion:34/userId:/deviceId:<uuid>
x-app-version: 1.5.5
x-app-bundle-id: com.chat.chatai.chatbot.aichatbot
x-os: android
x-adjust-id:                       (空即可)
Content-Type: application/json
User-Agent: okhttp/4.12.0
```

### 2.3 聊天请求体

```json
{
  "max_tokens": 4096,
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "user", "content": [{"type": "text", "text": "你好"}]}
  ],
  "response_length": "medium",
  "response_tone": "default",
  "topic_type": "general",
  "image_and_analytic": false,
  "tools": [],
  "is_image_vip": false
}
```

- **多模态**：`content` 数组加 `{"type":"image_url","image_url":{"url":"https://...","detail":"auto"}}`
- **联网搜索**：`tools: ["web_search"]`（另有 `"deep_search"` 走 DeepSearch 产品线）

### 2.4 流式响应（SSE）

`Content-Type: text/event-stream; charset=utf-8`，每行 `data: {json}`：

```json
// 增量文字块
{"statusCode":200,"message":"ok","data":{"role":"assistant","contentType":"text","content":"你","contentText":"你"}}
// 结束块
{"statusCode":200,"message":"ok","data":{"role":"assistant","finishReason":"stop"}}
// 联网来源块（web_search 时）
{"statusCode":200,"data":{"webs":[{"uri":"...","title":"...","domain":"...","iconLink":"..."}]}}
// 错误块
{"statusCode":200,"data":{...}, "error":{"statusCode":1002,"message":"..."}}
```

> 客户端解析逻辑：按 `data:` 切分，若含 `"error":` 前缀按 ErrorResult 解析，否则按 ChatResult→data(ChatResponse) 解析。

### 2.5 关键端点速查

| 端点 | 方法 | 用途 | 认证 |
|------|------|------|------|
| `/api/v3/chat/stream` | POST | **聊天流式（主用）** | 自签 JWT |
| `/api/v2/chatStream` | POST | 聊天流式（旧版） | 自签 JWT |
| `/api/v1/auth/login` | POST | 游客登录（deviceId+type） | 无 |
| `/api/v1/chat-setting/option` | GET | 模型列表 + 回复长度/语气选项 | accessToken |
| `/api/v1/upload` | POST | 文件上传 | accessToken |
| `/api/v1/ai-avatar` `/ai-logo` `/ai-sticker` | POST | 生成头像/Logo/贴纸 | accessToken |
| `/api/v1/ai-docs/chat` `/ai-link/chat` | POST | 文档/链接对话 | accessToken |
| `/api/v2/deep-search/...` | POST | 深度搜索（人物/航班/酒店/电影/购物） | accessToken |
| `/api/v2/ggSTTAuthToken` | POST | Google TTS 语音令牌 | accessToken |

---

## 三、模型清单

UI 内置 11 个（`stringId` 为后端真实名），**另实测 57 个标准模型名全部 200**。共 **68 个**。

**UI 展示模型**：`no_id`(Smart Mode)、`Claude Opus 4.6`、`claude-sonnet-4.5`、`Llama 4 Maverick`、`gemini-3-flash-preview`→实际名`gemini-3-flash`、`gemini-3-pro-preview`→`gemini-3-pro`、`gemini-2.5-flash`、`gemini-2.5-pro`、`Grok-4.1 Fast`、`gpt-5-mini`、`gpt-4o-mini`

**实测额外可用**（节选，完整见 `models_full.txt`）：
`gpt-5` `gpt-5.1` `o3` `o4-mini` `o1` `claude-opus-4-6` `claude-haiku-4.5` `gemini-3-pro` `gemini-3-flash` `grok-4` `llama-4-scout` `deepseek-r1` `deepseek-reasoner` `qwen-max` `mistral-large` `kimi` …

> ⚠️ `gemini-3-pro-preview` / `gemini-3-flash-preview` 是 UI 里的**假名**，后端真实名去 `-preview`（否则 Vertex 报 404 code 1002）。

---

## 四、快速开始

```bash
# 依赖
pip install fastapi uvicorn aiohttp requests pydantic

# 启动网关 (默认 0.0.0.0:8920)
python server.py --port 8920

# 打开 demo 页
# 浏览器访问 http://127.0.0.1:8920/
```

网关暴露 OpenAI 兼容接口：

```bash
# 非流式
curl http://127.0.0.1:8920/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"你好"}]}'

# 流式
curl -N http://127.0.0.1:8920/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4.5","messages":[{"role":"user","content":"数到5"}],"stream":true}'

# 模型列表
curl http://127.0.0.1:8920/v1/models
```

**环境变量**：
- `ONESEARCH_API_KEY` — 网关自身鉴权键（设了则客户端需带 `Authorization: Bearer <key>`）
- `HTTP_PROXY` / `HTTPS_PROXY` — 上游代理（如 `http://127.0.0.1:10808`，规避 Cloudflare 限流用）

---

## 五、限流情报（对接必读）

上游 `chat-ai.begamob.com` 前置 **Cloudflare**，高频请求返回 **HTTP 429**（HTML 拦截页，非应用层 JSON）。
实测：并发 ≤20 稳定全 200；并发 30 基本全过；**并发 50 立即触发 429**，且触发后 IP 进入**数分钟级封禁窗口**（实测 >3min 未恢复）。

**对接建议**：
1. 单出口并发控制在 **≤ 20**，或用**代理池轮换出口 IP** 分摊。
2. 触发 429 后需退避（指数退避）+ 换 IP，短时间重试无效。
3. 生产环境推荐"多出口代理池 + 每出口限速"架构。

---

## 六、产物结构

```
.
├── server.py              # OpenAI 兼容网关 (FastAPI)
├── demo.html              # 中文聊天演示页 (流式/多模型/联网)
├── stress_test.py         # 压测脚本 (延迟/并发/流式TTFT/错误路径)
├── tools/
│   ├── probe_upstream.py  # 独立协议探针 (JWT自签+chatStream)
│   ├── test_matrix.py     # 模型矩阵测试
│   ├── enumerate_models.py# 大规模模型枚举
│   └── test_capabilities.py # 联网/读图/多轮/长上下文
├── docs/
│   └── NEW-API-INTEGRATION.md  # new-api 渠道对接指南
└── models_full.txt        # 68 个可用模型完整清单
```

---

## 七、免责声明

本项目为**安全研究 / 互操作性研究**产物，仅用于学习 Android 应用网络协议与 API 网关技术。
使用者须自行承担一切合规责任，不得用于商业滥用、绕过服务条款或任何违法用途。
作者不对因使用本项目产生的任何后果负责。
