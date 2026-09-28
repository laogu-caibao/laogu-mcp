"""老谷拆财报 MCP Server：16 个财经 Skill 的程序化数据层。

- 每个 tool 对应一个 laogu- skill 的数据抓取部分；解读逻辑由宿主 LLM 按各 skill 的
  Output Contract 执行（见 tool 描述）。
- 返回统一 JSON 信封：{"ok","data","meta","warnings"}；失败时 ok=false + 搜索模板，绝不编数字。
- 合规：只陈述事实和逻辑，不做买卖推荐；每条数据带日期时点和来源。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastmcp import FastMCP

from . import _sources as S

mcp = FastMCP("老谷拆财报 laogu-mcp")

_DISCLAIMER = "本工具只提供公开数据抓取，不做买卖推荐；解读时请遵守 laogu- skill 的 Output Contract。"


def _ok(data, source: str, warnings: list[str] | None = None) -> dict:
    return {"ok": True, "data": data,
            "meta": {"source": source, "fetched_at": S.now_bj(), "timezone": "北京时间"},
            "warnings": warnings or []}


def _fail(reason: str, search_templates: list[str] | None = None,
          warnings: list[str] | None = None) -> dict:
    return {"ok": False, "reason": reason, "search_templates": search_templates or [],
            "meta": {"fetched_at": S.now_bj(), "timezone": "北京时间"},
            "warnings": warnings or []}


def _latest_lhb_date(max_back: int = 10) -> str | None:
    """找到最近一个有龙虎榜数据的交易日（YYYY-MM-DD），找不到返回 None。"""
    d = datetime.now(S.BJ).date()
    for _ in range(max_back):
        if d.weekday() < 5:  # 跳过周末
            ds = d.strftime("%Y-%m-%d")
            try:
                if S.lhb_board(ds, page_size=1):
                    return ds
            except Exception:
                pass
        d -= timedelta(days=1)
    return None


# ---------------- 1. laogu-fundamentals ----------------

@mcp.tool()
def quote(code: str) -> dict:
    """个股行情快照（对应 skill：laogu-fundamentals 基本面速查）。

    输入 6 位股票代码（如 600519）。输出：名称/现价/涨跌幅/今开/昨收/最高/最低/
    成交量/成交额，含数据日期时点。
    输出契约：数字原样引用接口值并标注单位；取不到的字段标 null 并在 warnings 说明，
    不估算、不编造。只陈述事实，不做买卖推荐。
    数据源：腾讯行情 → 新浪行情 → 东财 push2（三级降级，见 warnings）。
    """
    try:
        data, warnings = S.stock_quote(code.strip())
        return _ok(data, data.pop("_来源", "未知"), warnings)
    except Exception as e:
        return _fail(f"行情接口全部失败：{e}",
                     [f"{code} 股价 site:finance.sina.com.cn", f"{code} 行情 东方财富"])


# ---------------- 2. laogu-announcements ----------------

@mcp.tool()
def ann_list(code: str, page_size: int = 20) -> dict:
    """个股公告列表（对应 skill：laogu-announcements 公告盯梢）。

    输入 6 位股票代码。输出最近公告：art_code（取正文用）/标题/发布时间/公告日期/栏目。
    输出契约：按发布时间倒序；只返回接口真实条目，不脑补"应有公告"。
    数据源：东财公告接口（2026-09-29 实测可用）。只陈述事实，不做买卖推荐。
    """
    try:
        items = S.eastmoney_ann_list(code.strip(), page_size=min(page_size, 50))
        return _ok({"code": code.strip(), "count": len(items), "items": items}, "东财公告接口")
    except Exception as e:
        return _fail(f"公告列表抓取失败：{e}", [f"{code} 公告 东方财富", f"{code} 最新公告"])


# ---------------- 3. laogu-news ----------------

@mcp.tool()
def code_verify(keyword: str) -> dict:
    """新闻提及公司 → 股票代码核对（对应 skill：laogu-news 财经资讯解读）。

    输入 6 位股票代码：返回官方简称核对结果（用行情接口反查名称）。
    输入中文简称：目前无稳定公开的"简称→代码"程序化接口，诚实返回 ok=false +
    网页搜索模板，不猜测代码（猜错代码=张冠李戴，比没有更糟）。
    输出契约：核对成功返回{代码, 官方简称}；失败必须走搜索模板人工确认。
    """
    kw = keyword.strip()
    if kw.isdigit() and len(kw) == 6:
        try:
            data, warnings = S.stock_quote(kw)
            return _ok({"代码": kw, "官方简称": data.get("名称"),
                        "核对结论": "代码有效，名称以行情接口为准"}, data.pop("_来源", "未知"), warnings)
        except Exception as e:
            return _fail(f"代码 {kw} 行情查询失败：{e}", [f"{kw} 股票简称"])
    return _fail(f"「{kw}」是中文简称，无稳定公开的简称→代码接口，不猜测。",
                 [f"{kw} 股票代码", f"{kw} 上市公司 股票代码 雪球"])


# ---------------- 4. laogu-morning ----------------

@mcp.tool()
def market_snapshot() -> dict:
    """市场快照（对应 skill：laogu-morning 每日市场早报 / laogu-close 盘后复盘）。

    一次返回：A股四大指数（上证/深证成指/创业板/北证50）、美股三大指数（道指/纳指/标普）、
    WTI原油、COMEX黄金、离岸人民币。
    输出契约：每项带名称/现价/涨跌幅/时间；美股标注"盘中价/已收盘"（北京时间凌晨4点前
    为盘中价）；A股成交额字段不可靠时标 null 不硬写全市场成交额。只陈述事实，不做买卖推荐。
    数据源：新浪行情（2026-09-29 实测可用）。
    """
    syms = {"s_sh000001": "上证指数", "s_sz399001": "深证成指", "s_sz399006": "创业板指",
            "s_bj899050": "北证50", "gb_dji": "道琼斯", "gb_ixic": "纳斯达克",
            "gb_inx": "标普500", "hf_CL": "WTI原油", "hf_GC": "COMEX黄金",
            "fx_susdcnh": "离岸人民币"}
    try:
        raw = S.sina_batch(list(syms))
        items = [{"key": k, "label": v, **raw[k]} for k, v in syms.items() if k in raw]
        missing = [v for k, v in syms.items() if k not in raw]
        w = [f"未取到：{','.join(missing)}"] if missing else []
        return _ok({"items": items, "count": len(items)}, "新浪行情", w)
    except Exception as e:
        return _fail(f"市场快照抓取失败：{e}", ["今日股市行情 新浪财经", "美股三大指数 新浪财经"])


# ---------------- 5. laogu-moneyflow ----------------

@mcp.tool()
def fund_flow(code: str = "", kind: str = "margin") -> dict:
    """资金流向（对应 skill：laogu-moneyflow 资金流向解读）。

    kind="margin"：个股两融（需传 code），返回最近5个交易日融资余额/融资买入额/
    融资偿还额/融资净买入（=买入-偿还）/融券余额，数据 T+1，日期以接口实际返回为准。
    kind="lhb"：全市场龙虎榜资金（code 可空），返回最近有数据交易日的净买入/净卖出
    Top5（代码/名称/涨跌幅/净买额/席位标签）。
    输出契约：数字原样引用并标注单位与数据日期；北向资金自2024-08-19起无日度净买入
    口径，本工具不输出任何"北向净流入"数字。只陈述事实，不做买卖推荐。
    数据源：东财 datacenter（2026-09-29 实测可用）。
    """
    try:
        if kind == "margin":
            if not (code.strip().isdigit() and len(code.strip()) == 6):
                return _fail("kind=margin 需要 6 位股票代码", [])
            rows = S.margin_stock(code.strip())
            if not rows:
                return _fail(f"{code} 未取到两融数据（可能非两融标的）",
                             [f"{code} 融资融券 东方财富"])
            return _ok({"code": code.strip(), "rows": rows}, "东财datacenter两融")
        elif kind == "lhb":
            ds = _latest_lhb_date()
            if not ds:
                return _fail("近10天无龙虎榜数据", ["龙虎榜 东方财富"])
            rows = S.lhb_board(ds, page_size=200)
            get = lambda r: (r["龙虎榜净买额_元"] or 0)
            buys = sorted([r for r in rows if get(r) > 0], key=get, reverse=True)[:5]
            sells = sorted([r for r in rows if get(r) < 0], key=get)[:5]
            return _ok({"trade_date": ds, "净买入Top5": buys, "净卖出Top5": sells,
                        "上榜总数": len(rows)}, "东财datacenter龙虎榜")
        return _fail("kind 参数只能是 margin 或 lhb", [])
    except Exception as e:
        return _fail(f"资金流向抓取失败：{e}", ["龙虎榜 东方财富", "融资融券 东方财富"])


# ---------------- 6. laogu-close ----------------

@mcp.tool()
def close_recap() -> dict:
    """收盘指数快照（对应 skill：laogu-close 盘后复盘）。

    返回 A股四大指数今日涨跌幅/现价/成交量/成交额。输出契约：数字标注日期；
    涨跌家数、板块涨跌幅无稳定公开接口，缺口在 warnings 标注"未核验"，不编造。
    只陈述事实，不做买卖推荐。数据源：新浪行情。
    """
    try:
        raw = S.sina_batch(["s_sh000001", "s_sz399001", "s_sz399006", "s_bj899050"])
        return _ok({"indices": raw}, "新浪行情",
                   ["涨跌家数/板块涨跌幅无稳定公开接口，标未核验"])
    except Exception as e:
        return _fail(f"收盘快照抓取失败：{e}", ["今日A股收盘 新浪财经"])


# ---------------- 7. laogu-lhb ----------------

@mcp.tool()
def lhb_board(trade_date: str = "", top_n: int = 5) -> dict:
    """龙虎榜明细（对应 skill：laogu-lhb 龙虎榜夜报）。

    trade_date 为空时自动回滚到最近有数据的交易日（最多回滚10天，跳过周末）。
    输出：上榜总数 + 净买入/净卖出 TopN（代码/名称/涨跌幅/净买额/买入额/卖出额/
    上榜原因/席位标签）。
    输出契约：分类只看席位类型（机构/游资地域资金/股通），不看标签里"买入/卖出"字样；
    公共接口不提供具体营业部，绝不编造；席位标签原样引用。只陈述事实，不做买卖推荐。
    数据源：东财 datacenter RPT_DAILYBILLBOARD_DETAILSNEW（2026-09-29 实测可用）。
    """
    try:
        ds = trade_date.strip() or _latest_lhb_date()
        if not ds:
            return _fail("近10天无龙虎榜数据", ["龙虎榜 东方财富"])
        rows = S.lhb_board(ds, page_size=200)
        get = lambda r: (r["龙虎榜净买额_元"] or 0)
        n = max(1, min(top_n, 20))
        return _ok({"trade_date": ds, "上榜总数": len(rows),
                    "净买入Top": sorted([r for r in rows if get(r) > 0], key=get, reverse=True)[:n],
                    "净卖出Top": sorted([r for r in rows if get(r) < 0], key=get)[:n]},
                   "东财datacenter龙虎榜")
    except Exception as e:
        return _fail(f"龙虎榜抓取失败：{e}", ["龙虎榜 东方财富数据中心"])


# ---------------- 8. laogu-report ----------------

@mcp.tool()
def ann_content(art_code: str) -> dict:
    """公告正文抓取（对应 skill：laogu-report 定期报告深拆）。

    输入 art_code（从 ann_list/ann 相关 tool 获取）。自动翻页抓取全文，去 HTML 标签，
    返回纯文本正文 + 附件(PDF)链接。
    输出契约：正文原样返回不改写；定期报告财务数字以正文为准，解读时双源交叉。
    数据源：东财公告正文接口（2026-09-29 实测可用）。
    """
    try:
        d = S.eastmoney_ann_content(art_code.strip())
        if not d["正文"]:
            return _fail(f"{art_code} 未取到正文（可能仅附件PDF）",
                         [f"{art_code} 公告全文"], )
        d["正文长度"] = len(d["正文"])
        return _ok(d, "东财公告正文接口")
    except Exception as e:
        return _fail(f"公告正文抓取失败：{e}", [f"{art_code} 公告全文"])


# ---------------- 9. laogu-risk ----------------

@mcp.tool()
def risk_inputs(code: str) -> dict:
    """财务风险体检输入（对应 skill：laogu-risk 财务风险预警）。

    返回：行情快照 + 最新一期定期报告定位（从公告列表找标题含"年度报告"/"半年度报告"/
    "季度报告"的最新一条：art_code/标题/公告日期）。
    输出契约：本 tool 只做输入准备，不打分；定期报告正文需再调 ann_content 获取；
    报告期以公告标题为准，不推测。只陈述事实，不做买卖推荐。
    """
    c = code.strip()
    try:
        qd, warnings = S.stock_quote(c)
    except Exception as e:
        return _fail(f"行情抓取失败：{e}", [f"{c} 行情"])
    try:
        items = S.eastmoney_ann_list(c, page_size=30)
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 年度报告"])
    report = next((i for i in items
                   if any(k in (i["标题"] or "") for k in ("年度报告", "半年度报告", "季度报告"))), None)
    if not report:
        warnings.append("近30条公告未找到定期报告，标未核验")
    src = qd.pop("_来源", "未知")
    return _ok({"code": c, "行情快照": qd, "最新定期报告": report}, f"{src}+东财公告", warnings)


# ---------------- 10. laogu-earnings ----------------

@mcp.tool()
def earnings_ann(code: str, page_size: int = 30) -> dict:
    """业绩预告/快报扫描（对应 skill：laogu-earnings 业绩预告解读）。

    从公告列表筛选标题含"业绩预告"/"业绩快报"/"业绩预告修正"的公告，返回 art_code/
    标题/公告日期（正文用 ann_content 另取，提炼利润区间与同比口径）。
    输出契约：只返回接口真实条目；利润区间数字以正文为准，本 tool 不提前解读；
    无预告时明确返回空列表，不编造。只陈述事实，不做买卖推荐。
    """
    c = code.strip()
    try:
        items = S.eastmoney_ann_list(c, page_size=min(page_size, 50))
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 业绩预告"])
    hit = [i for i in items if any(k in (i["标题"] or "")
                                   for k in ("业绩预告", "业绩快报"))]
    return _ok({"code": c, "count": len(hit), "items": hit}, "东财公告接口")


# ---------------- 11. laogu-research ----------------

@mcp.tool()
def research_grounding(code: str) -> dict:
    """研报精读 grounding 数据（对应 skill：laogu-research 研报精读）。

    返回代码核对（官方简称）+ 行情快照，供研报解读时交叉验证。
    诚实声明：研报正文无稳定公开程序化接口，本 tool 不提供研报内容；
    研报全文/链接需用户粘贴，宿主按 laogu-research 的 Output Contract 解读。
    只陈述事实，不做买卖推荐。
    """
    c = code.strip()
    try:
        qd, warnings = S.stock_quote(c)
    except Exception as e:
        return _fail(f"行情抓取失败：{e}", [f"{c} 研报 目标价"])
    src = qd.pop("_来源", "未知")
    return _ok({"code": c, "官方简称": qd.get("名称"), "行情快照": qd,
                "研报正文缺口": "无公开研报API，请用户粘贴研报全文或链接后解读"},
               src, warnings)


# ---------------- 12. laogu-notes ----------------

@mcp.tool()
def ir_records(code: str, page_size: int = 10) -> dict:
    """投资者关系活动记录表搜索（对应 skill：laogu-notes 调研纪要解读）。

    从公告列表筛选标题含"投资者关系活动记录表"的公告，返回 art_code/标题/公告日期
    （纪要正文用 ann_content 另取后结构化提炼）。
    输出契约：只返回真实条目；无记录表时返回空列表并标注，不编造调研内容。
    只陈述事实，不做买卖推荐。数据源：东财公告接口。
    """
    c = code.strip()
    try:
        items = S.eastmoney_ann_list(c, page_size=min(page_size, 50))
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 投资者关系活动记录表"])
    hit = [i for i in items if "投资者关系活动记录表" in (i["标题"] or "")]
    return _ok({"code": c, "count": len(hit), "items": hit}, "东财公告接口")


# ---------------- 13. laogu-unlock ----------------

@mcp.tool()
def unlock_notices(code: str, page_size: int = 10) -> dict:
    """限售解禁公告搜索（对应 skill：laogu-unlock 解禁冲击评估）。

    从公告列表筛选标题含"限售股份上市流通"/"限售股上市流通"/"解除限售"的公告，
    返回 art_code/标题/公告日期（解禁规模/占比/股东性质以正文为准，用 ann_content 另取）。
    输出契约：抛压定级需正文数据，本 tool 只做公告定位；无相关公告时返回空列表。
    只陈述事实，不做买卖推荐。数据源：东财公告接口。
    """
    c = code.strip()
    try:
        items = S.eastmoney_ann_list(c, page_size=min(page_size, 50))
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 限售股上市流通"])
    hit = [i for i in items if any(k in (i["标题"] or "")
                                   for k in ("限售股份上市流通", "限售股上市流通", "解除限售", "限售股解禁"))]
    return _ok({"code": c, "count": len(hit), "items": hit}, "东财公告接口")


# ---------------- 14. laogu-ipo ----------------

@mcp.tool()
def ipo_calendar() -> dict:
    """打新日历（对应 skill：laogu-ipo）。

    诚实声明：新股申购/上市日期无稳定公开程序化接口（2026-09-29 实测结论），
    本 tool 返回 ok=false + 本周打新搜索模板，不编造任何新股代码/日期/发行价。
    宿主应按 laogu-ipo 的 Output Contract 用搜索结果生成日历，无新股时明确说明。
    """
    return _fail("新股申购/上市无稳定公开程序化接口，不编造日历。",
                 ["本周新股申购一览 东方财富", "新股申购日历 证券时报",
                  "本周新股上市一览"])


# ---------------- 15. laogu-value ----------------

@mcp.tool()
def valuation(code: str) -> dict:
    """估值锚输入（对应 skill：laogu-value 估值定位）。

    返回当前 PE(TTM)/PB/市值/现价（能取到的才给）。输出契约：历史分位区间与同行
    对比无稳定公开接口，缺口在 warnings 标注"未核验"，由宿主用搜索补足；
    只描述分位位置，不做买卖推荐。
    数据源：腾讯行情（含PE/PB）→ 新浪 → 东财push2。
    """
    c = code.strip()
    try:
        data, warnings = S.stock_quote(c)
    except Exception as e:
        return _fail(f"行情抓取失败：{e}", [f"{c} 市盈率", f"{c} 估值"])
    src = data.pop("_来源", "未知")
    if data.get("PE_TTM") is None:
        warnings.append("PE(TTM)未取到（本网络腾讯行情超时），标未核验")
    if data.get("PB") is None:
        warnings.append("PB未取到（本网络腾讯行情超时），标未核验")
    warnings.append("历史估值分位/同行对比无程序化接口，需网页搜索补足")
    return _ok({"code": c, "估值快照": data}, src, warnings)


# ---------------- 16. laogu-macro ----------------

@mcp.tool()
def macro_helper(date: str = "") -> dict:
    """宏观日历辅助（对应 skill：laogu-macro 宏观日历解读）。

    返回：指定日期（默认今天）是否为 A 股交易日（周末直接判否；工作日用龙虎榜
    数据存在性交叉验证，标注为启发式）、北京时间、美股时段说明。
    诚实声明：议息会议/PMI/CPI 等事件无统一公开 API，本 tool 不编造事件日历，
    返回搜索模板由宿主按 laogu-macro 的 Output Contract 生成。
    只陈述事实，不做买卖推荐。
    """
    d = datetime.now(S.BJ).date() if not date.strip() else datetime.strptime(date.strip(), "%Y-%m-%d").date()
    ds = d.strftime("%Y-%m-%d")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()]
    if d.weekday() >= 5:
        trading, method = False, "周末休市（规则判断）"
    else:
        prev = d - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        prev_ds = prev.strftime("%Y-%m-%d")
        try:
            latest = _latest_lhb_date()
            if latest == ds:
                trading, method = True, "龙虎榜当日已披露（交叉验证）"
            elif latest == prev_ds:
                trading, method = True, f"工作日且上一交易日({latest})有数据→今日为交易日（龙虎榜未到18:00披露时间）"
            elif latest:
                trading, method = False, f"最近有龙虎榜数据的日期是{latest}，今日疑似节假日休市（启发式）"
            else:
                trading, method = None, "近10天无龙虎榜数据，未核验"
        except Exception:
            trading, method = None, "验证接口失败，未核验"
    bj_now = S.now_bj()
    us_note = ("美股夏令时(EDT)比北京慢12小时：北京时间凌晨4点前为美股盘中，"
               "之后为已收盘；冬令时(EST)慢13小时，凌晨5点为界。")
    return _ok({"日期": ds, "星期": weekday, "是否交易日": trading, "判断方法": method,
                "北京时间": bj_now, "美股时段说明": us_note,
                "事件日历缺口": "无统一程序化接口，用下方搜索模板补足"},
               "规则判断+东财龙虎榜交叉",
               ["宏观事件需网页搜索核验日期与北京时间换算"])


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
