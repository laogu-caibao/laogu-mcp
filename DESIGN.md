# 设计方案对比（2026-09-29）

> 按任务要求：至少对比 2-3 种设计，写出结论和选择理由。

## 方案 A：16 个独立 tool（每个 skill 一个 tool）✅ 采用

- 形态：`quote` / `ann_list` / `code_verify` / `market_snapshot` / `fund_flow` / `close_recap` /
  `lhb_board` / `ann_content` / `risk_inputs` / `earnings_ann` / `research_grounding` /
  `ir_records` / `unlock_notices` / `ipo_calendar` / `valuation` / `macro_helper`
- 优点：
  1. 与 16 个 skill 1:1 对应，品牌一致（laogu- 前缀），tool 描述即场景，宿主 LLM 路由零歧义；
  2. Glama 评分 70% 看 Tool Definition Quality——场景化描述天然高分；
  3. 每个 tool 自带 Output Contract，LLM 按契约解读，不编造。
- 缺点：tool 数量 16 个（MCP 建议 ≤20，仍在安全线内）；底层接口有复用（公告列表/正文被 4 个 tool 用）。
- 缓解：底层抽 `_sources.py` 共享 fetcher，tool 层只做场景编排，代码不重复。

## 方案 B：聚合成 5-6 个大 tool（行情/公告/资金/日历/估值/综合）❌ 否决

- 形态：如 `cn_data(aspect="quote"|"lhb"|"margin"|...)`，一个 tool 吃所有场景。
- 优点：tool 数量少，Glama"数量适中"项好看。
- 缺点（致命）：
  1. `aspect` 枚举参数让 LLM 路由歧义大增，调错参数=取错数据，财经场景下这是不可接受的风险；
  2. 场景语义丢失，与 laogu- skill 品牌割裂，Smithery 搜索 "龙虎榜" 搜不到我们；
  3. 大 tool 的描述写不下 6 个场景的 Output Contract，LLM 解读质量下降。
- 结论：省 tool 数量的收益远小于路由错误的风险，否决。

## 方案 C：16 个 tool，但每个 tool 内联全部抓取逻辑（无共享层）❌ 否决

- 缺点：公告列表/正文/行情三段逻辑在 4-6 个 tool 里复制粘贴，修一个 bug 要改 5 处；
  冒烟测试已证明这些接口的坑位会持续演进（push2 的 502、巨潮的参数变更），必须单点维护。
- 结论：工程上不可持续，否决。

## 数据返回格式对比

| 格式 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 统一 JSON 信封 | 机器可解析；`meta` 里带来源/时点/fallback 链路，诚实可审计 | LLM 要读 key | ✅ 采用 |
| 纯 Markdown 文本 | LLM 直接读 | 客户端难解析；来源标注易丢 | ❌ |
| 混合（JSON + 解读文本） | — | tool 越俎代庖做解读，违背"数据抓取与解读分离" | ❌ |

统一信封：
```json
{
  "ok": true,
  "data": { ... },
  "meta": {"source": "新浪行情", "fetched_at": "2026-09-29T03:50:00+08:00", "fallback_used": false},
  "warnings": ["push2 返回 502，已降级到新浪"]
}
```
失败时：`{"ok": false, "reason": "...", "search_templates": [...], "meta": {...}}` ——
**永远返回结构，不抛异常**（参数错误除外），调用方不用 try/except 猜。

## 失败降级链（全 tool 统一）

1. 主力接口（见各 tool 文档，2026-09-29 沙箱实测状态）→
2. 备选接口 → 3. 诚实失败：`ok:false` + 网页搜索模板 + 缺口标注。
3. 超时 15s、重试 1 次；GBK 接口统一转码；新浪系强制 Referer；桌面端 UA。
4. 沙箱实测基线（2026-09-29）：新浪行情✅ / 东财公告列表✅ / 公告正文✅ / 龙虎榜✅ /
   美股指数✅ / 腾讯行情❌TCP超时 / push2❌502 / 两融 filter 待验证（冒烟中修）。

## 无程序化源的 skill 如何处理（诚实声明）

- laogu-research（研报）：无公开研报 API 是行业事实 → tool 只做 grounding（代码核对+行情快照），描述里写清"研报正文需用户粘贴"。
- laogu-notes（纪要）：用东财公告接口按标题筛"投资者关系活动记录表"——真实程序化路径。
- laogu-unlock（解禁）：用东财公告接口按标题筛"限售股份上市流通提示性公告"——真实程序化路径。
- laogu-ipo（打新）：先实测东财 datacenter 新股接口候选；不通则 `ok:false` + 搜索模板，绝不编日历。
- laogu-macro（宏观）：无统一 API 是事实 → tool 做交易日校验（周末判断 + 龙虎榜/指数交叉）+ 北京时间换算规则 + 搜索模板。
