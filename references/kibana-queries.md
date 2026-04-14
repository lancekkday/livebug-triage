# Kibana / Elasticsearch Query 參考

## Kibana URL 對照
| 環境 | URL |
|------|-----|
| Production | https://kibana.kkday.com/ |
| Stage | https://kibana.stage.kkday.com/ |
| SIT | https://kibana.sit.kkday.com/ |

## API Endpoint 格式

```
POST {kibana_url}/elasticsearch/{index}/_search
Headers:
  kbn-xsrf: true
  Content-Type: application/json
  Authorization: ApiKey {KIBANA_API_KEY}
```

常用 index：
- `kkday-api-*` — 所有 API 服務
- `kkday-api-scm-*` — SCM 後台
- `kkday-api-product-*` — 商品 API

---

## 查詢 A：找 ERROR log（取 request.uuid）

```json
{
  "query": {
    "bool": {
      "must": [
        {"match": {"service": "kkday-api-scm"}},
        {"match": {"level": "ERROR"}},
        {"range": {
          "@timestamp": {
            "gte": "2026-04-14T01:00:00Z",
            "lte": "2026-04-14T09:00:00Z",
            "format": "strict_date_optional_time"
          }
        }}
      ],
      "should": [
        {"match": {"prod_oid": "575109"}},
        {"match": {"message": "575109"}}
      ],
      "minimum_should_match": 0
    }
  },
  "sort": [{"@timestamp": "asc"}],
  "size": 200,
  "_source": [
    "@timestamp", "service", "level", "log_label",
    "request.uuid", "message", "status", "http_method",
    "url", "response_status", "exception"
  ]
}
```

---

## 查詢 B：用 request.uuid 拉完整 call chain

```json
{
  "query": {
    "bool": {
      "must": [
        {"terms": {
          "request.uuid": [
            "abc123-...",
            "def456-..."
          ]
        }},
        {"range": {
          "@timestamp": {
            "gte": "2026-04-14T01:00:00Z",
            "lte": "2026-04-14T09:00:00Z"
          }
        }}
      ]
    }
  },
  "sort": [{"@timestamp": "asc"}],
  "size": 500
}
```

---

## 查詢 C：只找 EXCEPTION log（快速定位內層錯誤）

```json
{
  "query": {
    "bool": {
      "must": [
        {"match": {"log_label": "EXCEPTION"}},
        {"match": {"service": "kkday-api-scm"}},
        {"range": {
          "@timestamp": {"gte": "2026-04-14T01:00:00Z", "lte": "2026-04-14T09:00:00Z"}
        }}
      ]
    }
  },
  "sort": [{"@timestamp": "asc"}],
  "size": 100
}
```

---

## Log 欄位對照

| 欄位 | 說明 | 範例 |
|------|------|------|
| `service` | 服務名稱 | `kkday-api-scm` |
| `level` | 日誌級別 | `ERROR`, `INFO`, `WARNING` |
| `log_label` | 日誌類型 | `REQUEST`, `RESPONSE`, `OUTBOUND`, `EXCEPTION`, `TRACE` |
| `request.uuid` | 請求追蹤 UUID | `xxxx-xxxx-xxxx-xxxx` |
| `@timestamp` | 時間（UTC） | `2026-04-14T01:01:33.000Z` |
| `message` | 錯誤訊息 | `ProductSyncStepException: Transferring failed` |
| `status` | 應用層錯誤碼 | `129002` |
| `http_method` | HTTP method | `PUT` |
| `url` | 完整 URL | `https://api-product.kkday.com/api/v1/...` |
| `response_status` | HTTP status code | `400`, `422` |

---

## Kibana Discover 手動查詢（KQL 語法）

當無法透過 API 查詢時，在 Kibana UI 輸入：

```
service: "kkday-api-scm" AND level: "ERROR" AND @timestamp: [2026-04-14T01:00:00 TO 2026-04-14T09:00:00]
```

加上 prod_oid 過濾：
```
service: "kkday-api-scm" AND level: "ERROR" AND message: "575109"
```

取得結果後選「Download as JSON」，將 JSON 貼回 Claude 繼續分析。

---

## Graph JSON 格式（供 callchain.html.j2 使用）

**Sequence Diagram 格式**（`services` + `messages` 陣列）：

```json
{
  "services": [
    {"id": "scm-frontend",  "label": "SCM Frontend",  "type": "client"},
    {"id": "kkday-api-scm", "label": "kkday-api-scm", "type": "service"},
    {"id": "api-product",   "label": "api-product",   "type": "service"},
    {"id": "db-cache",      "label": "DB / Cache",    "type": "db"}
  ],
  "messages": [
    {
      "id": "m1",
      "from": "scm-frontend", "to": "kkday-api-scm",
      "label": "POST /sync/product",
      "type": "request",
      "method": "POST", "path": "/sync/product",
      "status": 500, "statusClass": "error",
      "timestamp": "09:01:12"
    },
    {
      "id": "m2",
      "from": "kkday-api-scm", "to": "api-product",
      "label": "PUT /drafts/packages/1967203/descriptions",
      "type": "request",
      "method": "PUT", "path": "/api/v1/drafts/packages/1967203/descriptions",
      "status": 400, "statusClass": "error",
      "errorCode": "129002",
      "errorDesc": "No query results for model [App\\Domains\\Package]",
      "retryCount": 5,
      "requestUuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "timestamp": "09:01:33"
    },
    {
      "id": "m3",
      "from": "api-product", "to": "kkday-api-scm",
      "label": "400 Bad Request",
      "type": "response",
      "status": 400, "statusClass": "error",
      "timestamp": "09:01:34"
    }
  ]
}
```

**欄位說明：**

| 欄位 | 說明 | 值 |
|------|------|-----|
| `services[].type` | 服務類型（影響圖示和顏色） | `client` / `service` / `db` |
| `messages[].type` | 訊息方向 | `request`（實線）/ `response`（虛線）|
| `messages[].statusClass` | 狀態顏色 | `ok`（2xx）/ `warn`（4xx）/ `error`（5xx） |
| `messages[].retryCount` | Retry 次數（>1 顯示 badge） | 整數 |
| `messages[].errorCode` | 應用層錯誤碼 | `"129002"` |
| `messages[].errorDesc` | 錯誤描述 | `"No query results..."` |
| `messages[].requestUuid` | request.uuid（call chain key） | UUID string |
