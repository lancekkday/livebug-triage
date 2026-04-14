---
name: livebug-triage
description: >-
  Use when triaging production/staging/SIT bugs, analyzing Kibana logs for root cause,
  or investigating microservice call chain failures. Triggers on any Jira ticket ID
  (LIVEBUG-xxx, QA-xxx, etc.) when the user wants to diagnose an issue.
  Covers: Kibana log query, Jaeger distributed trace lookup, request.uuid call chain
  correlation, EXCEPTION/OUTBOUND log analysis, root cause report generation,
  interactive UML sequence diagram with duration annotations, and optional Slack/Jira push.
  Usage: /livebug-triage TICKET-ID [--env prod|stage|sit] [--send]
allowed-tools: Read, Glob, Grep, Bash, WebFetch, mcp__claude_ai_Atlassian__getJiraIssue, mcp__claude_ai_Atlassian__addCommentToJiraIssue, mcp__claude_ai_Slack__slack_send_message
---

# livebug-triage

針對 production/staging/SIT 問題，自動從 Jira 抓 ticket、查 Kibana log、分析 call chain root cause，並輸出結構化報告 + 互動式流程圖。

## 設定

### 環境對照
| `--env` | Kibana | Jaeger |
|---------|--------|--------|
| `prod`（預設）| https://kibana.kkday.com/ | https://jaeger-query.kkday.com/ |
| `stage` | https://kibana.stage.kkday.com/ | https://jaeger-query.stage.kkday.com/ |
| `sit` | https://kibana.sit.kkday.com/ | https://jaeger-query.sit.kkday.com/ |

### Kibana Auth（選填）
若 Kibana 需要 API Key，使用者可設定環境變數：
```
export KIBANA_API_KEY="your-api-key-here"
```
未設定時嘗試無 auth 連線（VPN 內可能直接通）。Jaeger 通常在 VPN 內無需 auth。

### Index Pattern
預設使用 `kkday-api-*`。若需調整，在查詢時指定，例如 `kkday-api-scm-*`。

---

## 工作流程

### Step 1 — 解析輸入
從使用者指令中抽取：
- `TICKET-ID`（必填）
- `--env`（預設 `prod`）
- `--send`（預設關閉，dry-run 模式）

### Step 2 — 讀取 Jira Ticket
使用 `mcp__claude_ai_Atlassian__getJiraIssue` 拿 ticket。

從 description/title 抽取以下欄位（LLM 判斷）：
- **服務名稱**：`service`（e.g. `kkday-api-scm`）
- **相關 ID**：`supplier_id`, `prod_oid`, `package_oid`, `product_oid` 等任何可作為 log filter 的 ID
- **回報時間**：轉為 `@timestamp` range（`±4h`，Asia/Taipei → UTC）
- **環境提示**：若 ticket 明確說明環境則覆蓋 `--env`

### Step 3 — 查詢 Kibana（需 VPN）

**查詢 A：找 ERROR log，取 request.uuid**

```
POST {kibana_url}/elasticsearch/kkday-api-*/_search
Headers:
  kbn-xsrf: true
  Content-Type: application/json
  Authorization: ApiKey {KIBANA_API_KEY}   # 若有設定
```

Query：
```json
{
  "query": {
    "bool": {
      "must": [
        {"match": {"service": "<service>"}},
        {"match": {"level": "ERROR"}},
        {"range": {"@timestamp": {"gte": "<t-4h>", "lte": "<t+4h>", "format": "strict_date_optional_time"}}}
      ],
      "should": [
        {"match": {"<id_field>": "<id_value>"}}
      ],
      "minimum_should_match": 0
    }
  },
  "sort": [{"@timestamp": "asc"}],
  "size": 200,
  "_source": ["@timestamp", "service", "level", "log_label", "request.uuid", "message", "status", "http_method", "url", "response_status"]
}
```

從結果中收集：
- 所有不重複的 `request.uuid`
- 所有不重複的 `trace` 欄位值（Jaeger trace ID，16 進位字串）

**查詢 B：用 request.uuid 拉完整 call chain**

```json
{
  "query": {
    "bool": {
      "must": [
        {"terms": {"request.uuid": ["<uuid1>", "<uuid2>"]}},
        {"range": {"@timestamp": {"gte": "<t-4h>", "lte": "<t+4h>"}}}
      ]
    }
  },
  "sort": [{"@timestamp": "asc"}],
  "size": 500
}
```

**查詢 C：用 Jaeger trace ID 取完整 distributed trace**

若 Kibana log 有 `trace` 欄位，同時查 Jaeger — 兩者資料互補，交互比對：

```
GET {jaeger_url}/api/traces/{traceID}
Headers:
  Accept: application/json
```

Jaeger response 關鍵欄位：
```json
{
  "data": [{
    "spans": [{
      "spanID": "...",
      "operationName": "PUT /api/v1/drafts/packages/...",
      "references": [{"refType": "CHILD_OF", "spanID": "<parentSpanID>"}],
      "startTime": 1713059093000000,
      "duration": 42000,
      "tags": [
        {"key": "http.method",      "value": "PUT"},
        {"key": "http.url",         "value": "https://api-product..."},
        {"key": "http.status_code", "value": 400},
        {"key": "error",            "value": true}
      ],
      "processID": "p1"
    }],
    "processes": {
      "p1": {"serviceName": "kkday-api-scm"}
    }
  }]
}
```

Jaeger span 轉換規則：
- `duration`（微秒）→ `durationMs = duration / 1000`（毫秒，顯示在圖上）
- `references[].refType === "CHILD_OF"` → request 方向（parent 呼叫 child）
- `tags[error=true]` → `statusClass: "error"`
- `tags[http.status_code]` → `status`
- `processID → processes` lookup → `service name`（對應 `services[]` 中的節點）
- 無 parent（root span）= 入口請求

**Fallback（VPN 不通 / 401 / 連線逾時）：**
停下來告訴使用者：
```
⚠️ 無法連線到 Kibana（可能需要 VPN 或 API Key）
請在 Kibana Discover 執行以下查詢，並將結果 JSON 貼回：

service: <service> AND level: ERROR AND @timestamp: [<t-4h> TO <t+4h>]

或直接貼入 raw log 內容，我繼續分析。
```

### Step 4 — 交互比對分析（Kibana + Jaeger）

兩個資料源各有優勢，合併後才是完整圖像：

| 資料源 | 擅長 | 對應用途 |
|--------|------|---------|
| **Kibana** (`request.uuid`) | 詳細 error body、application error code、EXCEPTION 完整訊息 | `errorCode`、`errorDesc`、root cause 文字說明 |
| **Jaeger** (`trace`) | 精確 timing、span parent-child 結構、跨服務拓撲 | `durationMs`、call chain 順序、服務依賴圖 |

**比對步驟：**
1. 從 Kibana ERROR log 同時取出 `request.uuid` 和 `trace`（兩者都收集）
2. 用 `request.uuid` 查 Kibana call chain（查詢 B）→ 取得 EXCEPTION 詳情
3. 用 `trace` 查 Jaeger API（查詢 C）→ 取得 span tree + duration
4. **合併**：以 Jaeger span 為骨架（結構/時序），用 Kibana EXCEPTION log 填入 `errorCode`/`errorDesc`
   - 比對鍵：`service name` + `timestamp` 接近（±2s）
   - 若 Jaeger span 有 `error=true` 但 `errorDesc` 為空 → 從對應的 Kibana EXCEPTION log 補入
5. 按 `request.uuid` groupBy → 跨多次 retry 聚合 unique error pattern
6. 區分「外層包裝錯誤」（e.g. `ProductSyncStepException`）vs「內層真因」

每個 `messages[]` 物件同時保留兩個 ID：
```json
{
  "requestUuid": "a1b2c3d4-...",
  "traceId":     "18a3f53a11c876..."
}
```
→ 側邊欄可直接點擊連到 Kibana Discover 或 Jaeger UI。

**Call Chain 節點類型（Kibana log_label）：**
| `log_label` | 對應圖中角色 |
|-------------|------------|
| `REQUEST` | 入口節點（最左/最上） |
| `OUTBOUND` | 對外呼叫邊 |
| `EXCEPTION` | 錯誤節點（標紅，補入 errorDesc） |
| `RESPONSE` | 出口節點 |

### Step 5 — 輸出分析報告

輸出格式：
```
{TICKET-ID} 初步研判 (AI dry-run)

Ticket: "{title}"
工單性質: {extracted description}

關鍵資訊（從 ticket description 抽取）:
• Supplier: {supplier_id}
• 商品: prod_oid {prod_oid}（內部 productOid {internal_id}）
• Platform: {platform}
• 回報時間: {reported_time}

後端 log 定位 ({service}, ±4h):
{prod/stage/sit} {id} 的 ERROR 共 N 筆，集中在 {time_start}–{time_end} +0800
之間，多次短間隔 retry。

🐛 Root Cause（內層錯誤）:
OUTBOUND: {METHOD} {host}{path}  →  {http_status}
EXCEPTION: status={error_code}
           desc="{error_message}"
→ {one-line human explanation}

⚠️ 觀察到的其他錯誤:（若有多個 error pattern）
• {pattern_2}: {http_status} / status={code} / {desc}

可能原因（需確認）:
• {hypothesis_1}
• {hypothesis_2}

建議下一步:
• {service_1}: {action}
• {service_2}: {action}
• 短期 workaround: {suggestion}

📊 互動式 Call Chain 圖已生成：{html_path}

ℹ️ 這是 livebug-triage skill 的分析結果（dry-run）
```

### Step 6 — 生成互動式 Call Chain 圖

1. 將 call chain 轉為 **Sequence Diagram** 格式的 graph JSON：
   ```json
   {
     "services": [
       {"id": "scm-frontend",  "label": "SCM Frontend",  "type": "client"},
       {"id": "kkday-api-scm", "label": "kkday-api-scm", "type": "service"},
       {"id": "api-product",   "label": "api-product",   "type": "service"}
     ],
     "messages": [
       {"id":"m1","from":"scm-frontend","to":"kkday-api-scm",
        "label":"POST /sync/product","type":"request",
        "method":"POST","path":"/sync/product","status":500,"statusClass":"error","timestamp":"09:01:12"},
       {"id":"m2","from":"kkday-api-scm","to":"api-product",
        "label":"PUT /drafts/packages/1967203/descriptions","type":"request",
        "method":"PUT","path":"/api/v1/drafts/packages/1967203/descriptions",
        "status":400,"statusClass":"error","errorCode":"129002",
        "errorDesc":"No query results for model [App\\Domains\\Package]",
        "retryCount":5,"requestUuid":"a1b2c3d4-...","traceId":"18a3f53a11c876...",
        "timestamp":"09:01:33","durationMs": 237},
       {"id":"m3","from":"api-product","to":"kkday-api-scm",
        "label":"400 Bad Request","type":"response",
        "status":400,"statusClass":"error","timestamp":"09:01:34"}
     ]
   }
   ```
   - `type: "request"` → 實線箭頭；`type: "response"` → 虛線箭頭
   - `statusClass: "ok"` / `"warn"` / `"error"` → 顏色
   - `retryCount > 1` → 顯示 retry badge（×N）
   - `durationMs` → 顯示在箭頭下方（來自 Jaeger span duration；<100ms 灰色、100-1000ms 橘色、>1000ms 紅色）

2. 讀取 template：`~/Documents/workspace/livebug-triage/templates/callchain.html.j2`
3. 將 graph JSON 填入 template 的 `GRAPH_DATA_PLACEHOLDER`（用 Python `json.dumps` 確保安全轉義）
4. 寫出到：`/tmp/livebug-{TICKET-ID}-callchain.html`
5. 執行 `open /tmp/livebug-{TICKET-ID}-callchain.html` 在瀏覽器開啟

### Step 7 — 推送（`--send` 模式）

若有 `--send` flag，先在 terminal 顯示完整報告，然後**詢問使用者確認**後再推送：
- **Slack**：使用 `mcp__claude_ai_Slack__slack_send_message`，頻道預設 `#livebug-triage`（或從 ticket 抽取）
- **Jira comment**：使用 `mcp__claude_ai_Atlassian__addCommentToJiraIssue`，格式化為 Jira markdown

---

## 常見問題排查

| 症狀 | 原因 | 解法 |
|------|------|------|
| `401 Unauthorized` | Kibana 需要 API Key | `export KIBANA_API_KEY=...` |
| `Connection refused` | 未連 VPN | 連 VPN 後重試，或貼 log JSON |
| `index_not_found_exception` | Index pattern 不符 | 改用 `kkday-api-scm-*` 或確認 index 名稱 |
| 找不到 `request.uuid` | log 格式不同 | 改用其他 correlation field（e.g. `trace_id`） |
| HTML 圖是空的 | Log 沒有 OUTBOUND 記錄 | 手動指定 service 或貼入 log |
