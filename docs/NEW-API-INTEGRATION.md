# new-api 渠道对接指南 — OneSearch

> 本文档面向 new-api（`lza6/new-api-Max`）对接 OneSearch 上游的完整方案。
> 与已有 2API 渠道（duck / aivm / openchat142 等）同构，可按团队既有习惯二选一。

---

## 方案选择（先看这个）

| 方案 | 适用 | 工作量 | 推荐度 |
|------|------|--------|--------|
| **A. OpenAI 兼容直连** | 已跑本仓库 `server.py` 网关 | 极低（新建一个 OpenAI 渠道指向本地网关） | ⭐ 首选，先跑通 |
| **B. 原生 Go 渠道适配器** | 要并入 new-api 主程序、免外部进程 | 中（写 adaptor + 注册 channel type） | 长期/生产 |

**建议**：先用 **方案 A** 跑通端到端（10 分钟），验证无误后再决定是否投入方案 B 做成原生渠道。

---

## 方案 A：OpenAI 兼容直连（推荐先做）

本仓库 `server.py` 已把 OneSearch 上游包装成标准 OpenAI 接口，因此 new-api 直接用**内置 OpenAI 渠道**指向它即可，**零改代码**。

### A.1 部署网关

```bash
pip install fastapi uvicorn aiohttp requests pydantic
# 建议设一个网关鉴权键，防公网裸奔
export ONESEARCH_API_KEY="sk-onesearch-local-xxxx"    # 可选
# 若需代理出口规避限流
export HTTP_PROXY="http://127.0.0.1:10808"            # 可选
python server.py --host 0.0.0.0 --port 8920
```

验证：`curl http://127.0.0.1:8920/v1/models` 应返回 61 个模型。

### A.2 new-api 新建渠道

在 new-api 后台 → 渠道 → 新建：

| 字段 | 值 |
|------|-----|
| 类型 | **OpenAI** |
| 名称 | OneSearch |
| 代理/Base URL | `http://127.0.0.1:8920` （填到 base_url；末尾不带 `/v1`，new-api 会自动补） |
| 密钥 | 若设了 `ONESEARCH_API_KEY` 就填它；否则随便填（如 `sk-any`） |
| 模型 | 见下方"模型映射" |
| 分组 | 按需 |

> 关键点：网关已实现 `/v1/chat/completions`（流式+非流式）、`/v1/models`、`/health`、`/stats`，new-api 的 OpenAI 渠道完全兼容。

### A.3 模型映射（重要）

OneSearch 上游模型名与主流 OpenAI 名有差异，new-api 渠道里配置"模型重定向"：

```
# new-api 模型名(对外) -> 上游真实名
gpt-4o          -> gpt-4o
gpt-5           -> gpt-5
o3              -> o3
claude-opus-4.5 -> claude-opus-4-5
claude-sonnet-4.5 -> claude-sonnet-4.5
gemini-3-pro    -> gemini-3-pro          # 注意: 不是 gemini-3-pro-preview
gemini-3-flash  -> gemini-3-flash        # 注意: 不是 gemini-3-flash-preview
grok-4          -> grok-4
deepseek-r1     -> deepseek-r1
```

> ⚠️ `gemini-3-pro-preview` / `gemini-3-flash-preview` 是 App UI 假名，上游 Vertex 会 404（code 1002）。
> 一定映射到 `gemini-3-pro` / `gemini-3-flash`。
> 完整 68 模型清单见仓库 `models_full.txt`。

### A.4 验证

new-api 渠道里点"测试"，或直接调 new-api 的 `/v1/chat/completions` 走该渠道一次。成功即可。

---

## 方案 B：原生 Go 渠道适配器（并入 new-api 主程序）

若不想跑外部 Python 进程，可把协议内嵌进 new-api（与 duck/aivm 同构）。以下是落地清单。

### B.1 新增渠道类型常量

`constant/channel.go`：
```go
ChannelTypeOneSearch = 74   // 顺延现有最大值(73=Duck)之后
```

对应 `relay/relay_adaptor.go` 的 `APIType` → `ChannelType` 映射，以及 `GetAdaptor()` 里加：
```go
case constant.APITypeOneSearch:
    return &onesearch.Adaptor{}
```

### B.2 新建适配器包 `relay/channel/onesearch/`

参照 `relay/channel/aivm/adaptor.go` 结构。核心要点：

```go
package onesearch

const (
    ChannelName = "OneSearch"
    baseURL     = "https://chat-ai.begamob.com"
    secretKey   = "stteam-ikameglobal-chatapiopenai"
    bundleID    = "com.chat.chatai.chatbot.aichatbot"
    version     = "1.5.5"
    chatPath    = "/api/v3/chat/stream"
    userAgent   = "okhttp/4.12.0"
)

var ModelList = []string{
    "gpt-4o", "gpt-5", "o1", "o3", "o4-mini",
    "claude-sonnet-4.5", "claude-opus-4-5", "claude-haiku-4.5",
    "gemini-2.5-flash", "gemini-2.5-pro", "gemini-3-pro", "gemini-3-flash",
    "grok-4", "grok-4.1-fast", "llama-4-maverick", "deepseek-r1", "deepseek-chat",
}
```

**自签 JWT（Go 版）**（对应 APK `ApiKeyFactoryImpl`，注意 iat=毫秒、exp=秒）：
```go
import ("github.com/golang-jwt/jwt/v5"; "time")

func makeJWT() string {
    now := time.Now()
    claims := jwt.MapClaims{
        "iat":        now.UnixMilli(),
        "exp":        now.Add(24*time.Hour).Unix(),
        "bundleId":   bundleID,
        "os":         "Android",
        "versionApp": version,
        "timezone":   "Asia/Shanghai",
    }
    tok := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
    s, _ := tok.SignedString([]byte(secretKey))
    return s
}
```

**请求头**：
```go
req.Header.Set("Authorization", "Bearer "+makeJWT())
req.Header.Set("user-header", "bundleId:"+bundleID+"/versionApp:"+version+"/OS:Android/osVersion:34/userId:/deviceId:"+deviceID)
req.Header.Set("x-app-version", version)
req.Header.Set("x-app-bundle-id", bundleID)
req.Header.Set("x-os", "android")
req.Header.Set("Content-Type", "application/json")
req.Header.Set("User-Agent", userAgent)
```

**请求体转换**（OpenAI messages → OneSearch）：
```go
// content 需转成 []{type,text} 或 []{type,text},{type:"image_url",image_url:{url,detail}}
// tools: 若客户端带联网意图则 ["web_search"]，否则 []
type UpReq struct {
    MaxTokens        int          `json:"max_tokens"`
    Model            string       `json:"model"`
    Messages         []UpMessage  `json:"messages"`
    ResponseLength   string       `json:"response_length"`
    ResponseTone     string       `json:"response_tone"`
    TopicType        string       `json:"topic_type"`
    ImageAndAnalytic bool         `json:"image_and_analytic"`
    Tools            []string     `json:"tools"`
    IsImageVip       bool         `json:"is_image_vip"`
}
```

**SSE 解析**（上游 chunk → OpenAI chunk）：
上游每个 `data: {...}` 里：`data.contentText` 是文字增量，`data.finishReason` 是结束，`data.webs` 是联网来源，`error` 字段存在即错误。

### B.3 限流处理（必做，方案B尤其重要）

上游 Cloudflare 会 429（HTML 拦页）。适配器必须：
- 按出口 IP 限速（**单出口并发 ≤ 20**）
- 429 时指数退避 + 换出口（若接代理池）
- 把 429 映射成 new-api 的可重试错误，触发渠道重试/换渠道

### B.4 注册与前端

- `constant/channel.go` 加类型 + 名称映射
- `web/` 前端渠道类型下拉加一项
- 渠道配置里 `BaseURL` 允许留空（走内置 baseURL）

---

## 通用注意事项

1. **限流是最大风险**：见 README 第五节。生产必须出口池 + 限速，否则 429 封 IP。
2. **模型名映射**：`gemini-3-*-preview` 必须改成 `gemini-3-*`。
3. **非法模型兜底**：上游对未知模型可能返回默认模型 200（不一定报错），网关侧建议做白名单校验。
4. **非标准响应**：上游 chunk 是 `{statusCode,message,data:{content,contentText,finishReason,webs}}`，没有 `id/usage/created`，网关已补齐成标准 OpenAI 格式。
5. **鉴权**：聊天零号池（自签 JWT），无需管理任何账号/设备池。

---

## 附：端点/密钥速查

```
上游 Base:   https://chat-ai.begamob.com
聊天:         POST /api/v3/chat/stream     (自签JWT, SSE)
模型列表:     GET  /api/v1/chat-setting/option  (游客登录取accessToken)
游客登录:     POST /api/v1/auth/login  {deviceId, type:"guest"}
SECRET_KEY:  stteam-ikameglobal-chatapiopenai
BUNDLE_ID:   com.chat.chatai.chatbot.aichatbot
VERSION:     1.5.5
```
