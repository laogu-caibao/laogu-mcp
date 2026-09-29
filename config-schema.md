# mcp-config.json Schema（配置热更新）

每个 `laogu-*` skill 仓库根目录一份 `mcp-config.json`。MCP 每次调用 tool 时从
GitHub raw 读取（TTL 1 小时，失败逐级降级），日常优化只改此文件即生效，零发版。

## 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `skill` | ✅ | skill slug，如 `laogu-morning`（须与仓库名一致） |
| `config_version` | ✅ | 语义版本，每次改配置必须升级（如 `1.0.0` → `1.0.1`） |
| `updated` | ✅ | 最后修改日期 `YYYY-MM-DD` |
| `endpoints` | ✅（可空对象） | 命名 URL 模板，`{param}` 为占位符 |
| `symbols` | 可选 | 快照类标的表：`{"s_sh000001": "上证指数"}` |
| `fallback_order` | 可选 | 降级顺序，如 `["tencent", "sina", "push2"]` |
| `field_map` | 可选 | 解析字段索引覆盖，如 `{"sina_quote": {"price": 3}}`（未列出的走代码默认值） |
| `reports` | 可选 | datacenter 报表：`{"lhb": {"report": "...", "columns": "ALL"}}` |
| `keywords` | 可选 | 标题过滤词：`{"earnings": ["业绩预告", "业绩快报"]}` |
| `notes` | 可选 | 说明（无稳定接口时写清原因） |

## 已知 endpoint 名

`sina_quote` / `tencent_quote` / `push2_stock` / `sina_batch` /
`eastmoney_ann_list` / `eastmoney_ann_content` / `eastmoney_datacenter`

URL 模板占位符：
- `sina_quote`, `tencent_quote`: `{codes}`（逗号分隔的带前缀代码）
- `push2_stock`: `{secid}`, `{fields}`
- `sina_batch`: `{symbols}`
- `eastmoney_ann_list`: `{code}`, `{page_size}`, `{page_index}`
- `eastmoney_ann_content`: `{art_code}`, `{page}`
- `eastmoney_datacenter`: `{params}`（已 URL 编码的查询串）

## field_map 可覆盖的解析器

- `sina_quote`: `name/open/prev_close/price/high/low/volume/amount/date/time`
  （默认 `0/1/2/3/4/5/8/9/30/31`）
- `tencent_quote`: `name/price/prev_close/change_pct/turnover/pe_ttm/pb`
  （默认 `1/3/4/32/38/39/46`）

## 示例

```json
{
  "skill": "laogu-morning",
  "config_version": "1.0.1",
  "updated": "2026-10-08",
  "endpoints": {"sina_batch": "https://hq.sinajs.cn/list={symbols}"},
  "symbols": {"s_sh000001": "上证指数", "s_sz399001": "深证成指"}
}
```

## 生效链路

改仓库 `mcp-config.json` → 升 `config_version` → push main →
MCP 下次调用（缓存 ≤1 小时）自动读取 → tool 返回的 `meta.config_version` / `meta.config_source`
可验证（`config_source`: live / cache / bundled / unavailable）。
`config_status` tool 可一次性查看 16 个 skill 的配置版本与来源。
