"""老谷拆财报 MCP Server：21 个财经 Skill 的程序化数据层。

- 每个 tool 对应一个 laogu- skill 的数据抓取部分；解读逻辑由宿主 LLM 按各 skill 的
  Output Contract 执行（见 tool 描述）。
- 返回统一 JSON 信封：{"ok","data","meta","warnings"}；失败时 ok=false + 搜索模板，绝不编数字。
- 合规：只陈述事实和逻辑，不做买卖推荐；每条数据带日期时点和来源。
- 契约动态同步（_contracts）：tool 描述在静态快照基础上，自动拼接 GitHub 最新版 skill 的
  Output Contract；skill 优化后 MCP 下次启动即生效，无需发版。
- 配置热更新（_config）：取数参数（URL 模板/标的表/降级顺序/字段映射/关键词）每次调用时
  从各 skill 仓库的 mcp-config.json 读取；skill 优化后下次调用即生效，零发版。
  meta 中 config_source 标注 live/cache/bundled/unavailable，config_version 为配置版本。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastmcp import FastMCP

from . import _sources as S
from . import _config as CFG

mcp = FastMCP("老谷拆财报 laogu-mcp")

from . import _contracts as _C

# tool 描述 = 静态快照 + GitHub 最新 skill 的 Output Contract（启动时同步）。
# 同步失败自动降级为静态快照；LAOGU_MCP_NO_CONTRACT_SYNC=1 可关闭网络同步。
_D = _C.resolve_all()

_DISCLAIMER = "本工具只提供公开数据抓取，不做买卖推荐；解读时请遵守 laogu- skill 的 Output Contract。"


def _cfg(slug: str) -> tuple[dict, str, str]:
    """取 skill 配置 → (cfg, source, version)。永不抛异常。"""
    try:
        return CFG.get_config(slug)
    except Exception:
        return {}, "unavailable", "none"


def _meta_extra(ci: tuple[dict, str, str] | None) -> dict:
    if not ci:
        return {}
    _, src, ver = ci
    return {"config_version": ver, "config_source": src}


def _ok(data, source: str, warnings: list[str] | None = None,
        ci: tuple[dict, str, str] | None = None) -> dict:
    return {"ok": True, "data": data,
            "meta": {"source": source, "fetched_at": S.now_bj(), "timezone": "北京时间",
                     **_meta_extra(ci)},
            "warnings": warnings or []}


def _fail(reason: str, search_templates: list[str] | None = None,
          warnings: list[str] | None = None,
          ci: tuple[dict, str, str] | None = None) -> dict:
    return {"ok": False, "reason": reason, "search_templates": search_templates or [],
            "meta": {"fetched_at": S.now_bj(), "timezone": "北京时间",
                     **_meta_extra(ci)},
            "warnings": warnings or []}


def _latest_lhb_date(max_back: int = 10) -> str | None:
    """找到最近一个有龙虎榜数据的交易日（YYYY-MM-DD），找不到返回 None。"""
    cfg, _, _ = _cfg("laogu-lhb")
    d = datetime.now(S.BJ).date()
    for _ in range(max_back):
        if d.weekday() < 5:  # 跳过周末
            ds = d.strftime("%Y-%m-%d")
            try:
                if S.lhb_board(ds, page_size=1, cfg=cfg):
                    return ds
            except Exception:
                pass
        d -= timedelta(days=1)
    return None


# ---------------- 1. laogu-fundamentals ----------------

@mcp.tool(description=_D["quote"])
def quote(code: str) -> dict:
    """个股行情快照（对应 skill：laogu-fundamentals 基本面速查）。"""
    ci = _cfg("laogu-fundamentals")
    try:
        data, warnings = S.stock_quote(code.strip(), ci[0])
        return _ok(data, data.pop("_来源", "未知"), warnings, ci)
    except Exception as e:
        return _fail(f"行情接口全部失败：{e}",
                     [f"{code} 股价 site:finance.sina.com.cn", f"{code} 行情 东方财富"], ci=ci)


# ---------------- 2. laogu-announcements ----------------

@mcp.tool(description=_D["ann_list"])
def ann_list(code: str, page_size: int = 20) -> dict:
    """个股公告列表（对应 skill：laogu-announcements 公告盯梢）。"""
    ci = _cfg("laogu-announcements")
    try:
        items = S.eastmoney_ann_list(code.strip(), page_size=min(page_size, 50), cfg=ci[0])
        return _ok({"code": code.strip(), "count": len(items), "items": items}, "东财公告接口", ci=ci)
    except Exception as e:
        return _fail(f"公告列表抓取失败：{e}", [f"{code} 公告 东方财富", f"{code} 最新公告"], ci=ci)


# ---------------- 3. laogu-news ----------------

@mcp.tool(description=_D["code_verify"])
def code_verify(keyword: str) -> dict:
    """新闻提及公司 → 股票代码核对（对应 skill：laogu-news 财经资讯解读）。"""
    ci = _cfg("laogu-news")
    kw = keyword.strip()
    if kw.isdigit() and len(kw) == 6:
        try:
            data, warnings = S.stock_quote(kw, ci[0])
            return _ok({"代码": kw, "官方简称": data.get("名称"),
                        "核对结论": "代码有效，名称以行情接口为准"}, data.pop("_来源", "未知"), warnings, ci)
        except Exception as e:
            return _fail(f"代码 {kw} 行情查询失败：{e}", [f"{kw} 股票简称"], ci=ci)
    return _fail(f"「{kw}」是中文简称，无稳定公开的简称→代码接口，不猜测。",
                 [f"{kw} 股票代码", f"{kw} 上市公司 股票代码 雪球"], ci=ci)


# ---------------- 4. laogu-morning ----------------

_DEFAULT_SYMS = {"s_sh000001": "上证指数", "s_sz399001": "深证成指", "s_sz399006": "创业板指",
                 "s_bj899050": "北证50", "gb_dji": "道琼斯", "gb_ixic": "纳斯达克",
                 "gb_inx": "标普500", "hf_CL": "WTI原油", "hf_GC": "COMEX黄金",
                 "fx_susdcnh": "离岸人民币"}


@mcp.tool(description=_D["market_snapshot"])
def market_snapshot() -> dict:
    """市场快照（对应 skill：laogu-morning 每日市场早报 / laogu-close 盘后复盘）。"""
    ci = _cfg("laogu-morning")
    syms = ci[0].get("symbols") or _DEFAULT_SYMS
    try:
        raw = S.sina_batch(list(syms), ci[0])
        items = [{"key": k, "label": v, **raw[k]} for k, v in syms.items() if k in raw]
        missing = [v for k, v in syms.items() if k not in raw]
        w = [f"未取到：{','.join(missing)}"] if missing else []
        return _ok({"items": items, "count": len(items)}, "新浪行情", w, ci)
    except Exception as e:
        return _fail(f"市场快照抓取失败：{e}", ["今日股市行情 新浪财经", "美股三大指数 新浪财经"], ci=ci)


# ---------------- 5. laogu-moneyflow ----------------

@mcp.tool(description=_D["fund_flow"])
def fund_flow(code: str = "", kind: str = "margin") -> dict:
    """资金流向（对应 skill：laogu-moneyflow 资金流向解读）。"""
    ci = _cfg("laogu-moneyflow")
    try:
        if kind == "margin":
            if not (code.strip().isdigit() and len(code.strip()) == 6):
                return _fail("kind=margin 需要 6 位股票代码", [], ci=ci)
            rows = S.margin_stock(code.strip(), cfg=ci[0])
            if not rows:
                return _fail(f"{code} 未取到两融数据（可能非两融标的）",
                             [f"{code} 融资融券 东方财富"], ci=ci)
            return _ok({"code": code.strip(), "rows": rows}, "东财datacenter两融", ci=ci)
        elif kind == "lhb":
            ds = _latest_lhb_date()
            if not ds:
                return _fail("近10天无龙虎榜数据", ["龙虎榜 东方财富"], ci=ci)
            rows = S.lhb_board(ds, page_size=200, cfg=ci[0])
            get = lambda r: (r["龙虎榜净买额_元"] or 0)
            buys = sorted([r for r in rows if get(r) > 0], key=get, reverse=True)[:5]
            sells = sorted([r for r in rows if get(r) < 0], key=get)[:5]
            return _ok({"trade_date": ds, "净买入Top5": buys, "净卖出Top5": sells,
                        "上榜总数": len(rows)}, "东财datacenter龙虎榜", ci=ci)
        return _fail("kind 参数只能是 margin 或 lhb", [], ci=ci)
    except Exception as e:
        return _fail(f"资金流向抓取失败：{e}", ["龙虎榜 东方财富", "融资融券 东方财富"], ci=ci)


# ---------------- 6. laogu-close ----------------

@mcp.tool(description=_D["close_recap"])
def close_recap() -> dict:
    """收盘指数快照（对应 skill：laogu-close 盘后复盘）。"""
    ci = _cfg("laogu-close")
    syms = list(ci[0].get("symbols") or ["s_sh000001", "s_sz399001", "s_sz399006", "s_bj899050"])
    try:
        raw = S.sina_batch(syms, ci[0])
        return _ok({"indices": raw}, "新浪行情",
                   ["涨跌家数/板块涨跌幅无稳定公开接口，标未核验"], ci)
    except Exception as e:
        return _fail(f"收盘快照抓取失败：{e}", ["今日A股收盘 新浪财经"], ci=ci)


# ---------------- 7. laogu-lhb ----------------

@mcp.tool(description=_D["lhb_board"])
def lhb_board(trade_date: str = "", top_n: int = 5) -> dict:
    """龙虎榜明细（对应 skill：laogu-lhb 龙虎榜夜报）。"""
    ci = _cfg("laogu-lhb")
    try:
        ds = trade_date.strip() or _latest_lhb_date()
        if not ds:
            return _fail("近10天无龙虎榜数据", ["龙虎榜 东方财富"], ci=ci)
        rows = S.lhb_board(ds, page_size=200, cfg=ci[0])
        get = lambda r: (r["龙虎榜净买额_元"] or 0)
        n = max(1, min(top_n, 20))
        return _ok({"trade_date": ds, "上榜总数": len(rows),
                    "净买入Top": sorted([r for r in rows if get(r) > 0], key=get, reverse=True)[:n],
                    "净卖出Top": sorted([r for r in rows if get(r) < 0], key=get)[:n]},
                   "东财datacenter龙虎榜", ci=ci)
    except Exception as e:
        return _fail(f"龙虎榜抓取失败：{e}", ["龙虎榜 东方财富数据中心"], ci=ci)


# ---------------- 8. laogu-report ----------------

@mcp.tool(description=_D["ann_content"])
def ann_content(art_code: str) -> dict:
    """公告正文抓取（对应 skill：laogu-report 定期报告深拆）。"""
    ci = _cfg("laogu-report")
    try:
        d = S.eastmoney_ann_content(art_code.strip(), cfg=ci[0])
        if not d["正文"]:
            return _fail(f"{art_code} 未取到正文（可能仅附件PDF）",
                         [f"{art_code} 公告全文"], ci=ci)
        d["正文长度"] = len(d["正文"])
        return _ok(d, "东财公告正文接口", ci=ci)
    except Exception as e:
        return _fail(f"公告正文抓取失败：{e}", [f"{art_code} 公告全文"], ci=ci)


# ---------------- 9. laogu-risk ----------------

@mcp.tool(description=_D["risk_inputs"])
def risk_inputs(code: str) -> dict:
    """财务风险体检输入（对应 skill：laogu-risk 财务风险预警）。"""
    ci = _cfg("laogu-risk")
    c = code.strip()
    try:
        qd, warnings = S.stock_quote(c, ci[0])
    except Exception as e:
        return _fail(f"行情抓取失败：{e}", [f"{c} 行情"], ci=ci)
    try:
        items = S.eastmoney_ann_list(c, page_size=30, cfg=ci[0])
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 年度报告"], ci=ci)
    kws = S._keywords(ci[0], "report", ["年度报告", "半年度报告", "季度报告"])
    report = next((i for i in items
                   if any(k in (i["标题"] or "") for k in kws)), None)
    if not report:
        warnings.append("近30条公告未找到定期报告，标未核验")
    src = qd.pop("_来源", "未知")
    return _ok({"code": c, "行情快照": qd, "最新定期报告": report}, f"{src}+东财公告", warnings, ci)


# ---------------- 10. laogu-earnings ----------------

@mcp.tool(description=_D["earnings_ann"])
def earnings_ann(code: str, page_size: int = 30) -> dict:
    """业绩预告/快报扫描（对应 skill：laogu-earnings 业绩预告解读）。"""
    ci = _cfg("laogu-earnings")
    c = code.strip()
    try:
        items = S.eastmoney_ann_list(c, page_size=min(page_size, 50), cfg=ci[0])
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 业绩预告"], ci=ci)
    kws = S._keywords(ci[0], "earnings", ["业绩预告", "业绩快报"])
    hit = [i for i in items if any(k in (i["标题"] or "") for k in kws)]
    return _ok({"code": c, "count": len(hit), "items": hit}, "东财公告接口", ci=ci)


# ---------------- 11. laogu-research ----------------

@mcp.tool(description=_D["research_grounding"])
def research_grounding(code: str) -> dict:
    """研报精读 grounding 数据（对应 skill：laogu-research 研报精读）。"""
    ci = _cfg("laogu-research")
    c = code.strip()
    try:
        qd, warnings = S.stock_quote(c, ci[0])
    except Exception as e:
        return _fail(f"行情抓取失败：{e}", [f"{c} 研报 目标价"], ci=ci)
    src = qd.pop("_来源", "未知")
    return _ok({"code": c, "官方简称": qd.get("名称"), "行情快照": qd,
                "研报正文缺口": "无公开研报API，请用户粘贴研报全文或链接后解读"},
               src, warnings, ci)


# ---------------- 12. laogu-notes ----------------

@mcp.tool(description=_D["ir_records"])
def ir_records(code: str, page_size: int = 10) -> dict:
    """投资者关系活动记录表搜索（对应 skill：laogu-notes 调研纪要解读）。"""
    ci = _cfg("laogu-notes")
    c = code.strip()
    try:
        items = S.eastmoney_ann_list(c, page_size=min(page_size, 50), cfg=ci[0])
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 投资者关系活动记录表"], ci=ci)
    kws = S._keywords(ci[0], "ir", ["投资者关系活动记录表"])
    hit = [i for i in items if any(k in (i["标题"] or "") for k in kws)]
    return _ok({"code": c, "count": len(hit), "items": hit}, "东财公告接口", ci=ci)


# ---------------- 13. laogu-unlock ----------------

@mcp.tool(description=_D["unlock_notices"])
def unlock_notices(code: str, page_size: int = 10) -> dict:
    """限售解禁公告搜索（对应 skill：laogu-unlock 解禁冲击评估）。"""
    ci = _cfg("laogu-unlock")
    c = code.strip()
    try:
        items = S.eastmoney_ann_list(c, page_size=min(page_size, 50), cfg=ci[0])
    except Exception as e:
        return _fail(f"公告列表失败：{e}", [f"{c} 限售股上市流通"], ci=ci)
    kws = S._keywords(ci[0], "unlock",
                      ["限售股份上市流通", "限售股上市流通", "解除限售", "限售股解禁"])
    hit = [i for i in items if any(k in (i["标题"] or "") for k in kws)]
    return _ok({"code": c, "count": len(hit), "items": hit}, "东财公告接口", ci=ci)


# ---------------- 14. laogu-ipo ----------------

@mcp.tool(description=_D["ipo_calendar"])
def ipo_calendar() -> dict:
    """打新日历（对应 skill：laogu-ipo）。"""
    ci = _cfg("laogu-ipo")
    return _fail("新股申购/上市无稳定公开程序化接口，不编造日历。",
                 ["本周新股申购一览 东方财富", "新股申购日历 证券时报",
                  "本周新股上市一览"], ci=ci)


# ---------------- 15. laogu-value ----------------

@mcp.tool(description=_D["valuation"])
def valuation(code: str) -> dict:
    """估值锚输入（对应 skill：laogu-value 估值定位）。"""
    ci = _cfg("laogu-value")
    c = code.strip()
    try:
        data, warnings = S.stock_quote(c, ci[0])
    except Exception as e:
        return _fail(f"行情抓取失败：{e}", [f"{c} 市盈率", f"{c} 估值"], ci=ci)
    src = data.pop("_来源", "未知")
    if data.get("PE_TTM") is None:
        warnings.append("PE(TTM)未取到（本网络腾讯行情超时），标未核验")
    if data.get("PB") is None:
        warnings.append("PB未取到（本网络腾讯行情超时），标未核验")
    warnings.append("历史估值分位/同行对比无程序化接口，需网页搜索补足")
    return _ok({"code": c, "估值快照": data}, src, warnings, ci)


# ---------------- 16. laogu-macro ----------------

@mcp.tool(description=_D["macro_helper"])
def macro_helper(date: str = "") -> dict:
    """宏观日历辅助（对应 skill：laogu-macro 宏观日历解读）。"""
    ci = _cfg("laogu-macro")
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
               ["宏观事件需网页搜索核验日期与北京时间换算"], ci)


# ---------------- 17. laogu-screener ----------------

@mcp.tool(description=_D["screener_scan"])
def screener_scan(report: str, columns: str, filter: str = "",
                  page_size: int = 50, sort_columns: str = "",
                  sort_types: str = "") -> dict:
    """条件选股（对应 skill：laogu-screener 多策略选股）。"""
    ci = _cfg("laogu-screener")
    try:
        rows = S.screener_query(report, columns, filter, page_size,
                                sort_columns, sort_types, ci[0])
        return _ok({"count": len(rows), "items": rows[:page_size]},
                   "东方财富 datacenter",
                   ["filter/sort 参数可能超时；失败时走搜索模板人工筛选"], ci)
    except Exception as e:
        return _fail(f"选股接口失败：{e}",
                     ["条件选股 东方财富", "A股 筛选 条件"], ci=ci)


# ---------------- 18. laogu-fund ----------------

@mcp.tool(description=_D["fund_check"])
def fund_check(code: str) -> dict:
    """基金诊断输入（对应 skill：laogu-fund 基金诊断）。"""
    ci = _cfg("laogu-fund")
    try:
        data = S.fund_profile(code.strip(), ci[0])
        warnings = data.pop("_warnings", [])
        return _ok(data, "天天基金 mobile 接口", warnings, ci)
    except Exception as e:
        return _fail(f"基金接口失败：{e}",
                     [f"{code} 基金 净值 天天基金", f"{code} 基金经理"],
                     ["可请用户手动提供净值表走降级链"], ci=ci)


# ---------------- 19. laogu-sentiment ----------------

@mcp.tool(description=_D["sentiment_gauge"])
def sentiment_gauge() -> dict:
    """市场测温输入（对应 skill：laogu-sentiment 情绪周期）。"""
    ci = _cfg("laogu-sentiment")
    try:
        data = S.sentiment_inputs(ci[0])
        return _ok(data, "新浪指数行情 + 网页搜索模板",
                   ["仅指数为程序化数据；其余维度按搜索模板补齐后由宿主按阈值打分"], ci)
    except Exception as e:
        return _fail(f"情绪输入失败：{e}",
                     ["今日 A股 涨跌家 涨停", "今日 连板高度 炸板率"], ci=ci)


# ---------------- 20. laogu-cb ----------------

@mcp.tool(description=_D["cb_scan"])
def cb_scan(page: int = 1, page_size: int = 100) -> dict:
    """可转债扫描（对应 skill：laogu-cb 可转债追踪）。"""
    ci = _cfg("laogu-cb")
    try:
        rows = S.cb_bond_list(page, page_size, ci[0])
        return _ok({"count": len(rows), "items": rows},
                   "东方财富 RPT_BOND_CB_LIST",
                   ["转股价/现价字段常为空，现价与转股价值需走腾讯行情加公式现算"], ci)
    except Exception as e:
        return _fail(f"可转债名单失败：{e}",
                     ["可转债 一览 转股价值 溢价率"], ci=ci)


# ---------------- 21. laogu-thesis ----------------

@mcp.tool(description=_D["thesis_check"])
def thesis_check(code: str) -> dict:
    """观点回检取数（对应 skill：laogu-thesis 观点追踪）。"""
    ci = _cfg("laogu-thesis")
    try:
        data = S.thesis_grounding(code.strip(), ci[0])
        warnings = data.pop("_warnings", [])
        return _ok(data, "行情+公告（复用已验证接口）", warnings, ci)
    except Exception as e:
        return _fail(f"回检取数失败：{e}",
                     [f"{code} 公告 东方财富", f"{code} 股价"], ci=ci)


# ---------------- 22. 配置状态 ----------------

@mcp.tool()
def config_status() -> dict:
    """21 个 skill 的 MCP 配置版本与来源（对应：自迭代协议 / 发行列车）。

    逐个读取各 skill 仓库的 mcp-config.json，返回 config_version（配置版本）与
    config_source（live=GitHub 实时 / cache=本地缓存 / bundled=包内快照 /
    unavailable=不可用走代码默认值）。skill 优化改配置后，这里应看到新版本。
    """
    rows = []
    for tool_name, slug in _C.TOOL_SKILLS.items():
        cfg, src, ver = _cfg(slug)
        rows.append({"tool": tool_name, "skill": slug,
                     "config_version": ver, "config_source": src,
                     "updated": cfg.get("updated", "") if cfg else ""})
    return _ok({"count": len(rows), "configs": rows},
               "GitHub laogu-caibao 各 skill 仓库 mcp-config.json")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
