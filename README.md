# livebug-triage

針對 production/staging/SIT 問題的自動化 triage skill。從 Jira ticket 抓關鍵資訊，查 Kibana log，分析 call chain root cause，輸出結構化報告 + 互動式流程圖。

## 使用方式

在 Claude Code 中執行：

```
/livebug-triage TICKET-123
/livebug-triage TICKET-123 --env stage
/livebug-triage TICKET-123 --env sit
/livebug-triage TICKET-123 --send
```

| 參數 | 說明 | 預設 |
|------|------|------|
| `TICKET-ID` | 任意 Jira ticket ID | 必填 |
| `--env prod/stage/sit` | 查哪個環境的 Kibana | `prod` |
| `--send` | 推送報告到 Slack + 加 Jira comment | 關（dry-run） |

## 環境需求

- **VPN**：連到 KKday 內部網路才能查 Kibana
- **Kibana API Key**（選填）：若 Kibana 需要驗證，設定環境變數：
  ```
  export KIBANA_API_KEY="your-api-key"
  ```

## 輸出

1. **Terminal 報告**：結構化的 root cause 分析，含建議下一步
2. **互動式 Call Chain 圖**：自動生成 `/tmp/livebug-{TICKET-ID}-callchain.html` 並在瀏覽器開啟

## 專案結構

```
livebug-triage/
├── .claude/skills/livebug-triage/
│   └── SKILL.md              # Skill 主定義（project-scoped）
├── templates/
│   └── callchain.html.j2     # SVG Sequence Diagram 互動圖 HTML template（無外部依賴）
├── references/
│   └── kibana-queries.md     # Elasticsearch Query DSL 模板與欄位對照
└── README.md
```

全域 skill（任何專案都可用）：
```
~/.claude/skills/livebug-triage/SKILL.md
```

## Kibana 環境

| 環境 | URL |
|------|-----|
| Production | https://kibana.kkday.com/ |
| Stage | https://kibana.stage.kkday.com/ |
| SIT | https://kibana.sit.kkday.com/ |

## 範例輸出

```
LIVEBUG-3266 初步研判 (AI dry-run)

Ticket: "1 error found alert appears, but no specific field is highlighted"
工單性質: SCM 後台問題（供應商編商品送出失敗）

關鍵資訊（從 ticket description 抽取）:
• Supplier: 15062
• 商品: prod_oid 575109（內部 productOid 13665）
• Platform: BE2
• 回報時間: 2026-04-14 10:30

後端 log 定位 (kkday-api-scm, ±4h):
prod 575109 的 ERROR 共 78 筆，集中在 2026-04-14 09:01–09:19 +0800...

🐛 Root Cause（內層 HTTP 400）:
OUTBOUND: PUT api-product.kkday.com /api/v1/drafts/packages/1967203/descriptions → 400
EXCEPTION: status=129002
           desc="No query results for model [App\Domains\...]"
→ package_oid 1967203 在 api-product 找不到（draft 版本不存在）

📊 互動式 Call Chain 圖已生成：/tmp/livebug-LIVEBUG-3266-callchain.html
```
