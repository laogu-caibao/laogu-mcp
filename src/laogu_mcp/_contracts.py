"""Skill 输出契约动态同步：tool 描述 = 静态快照 + GitHub 最新 SKILL.md 的 Output Contract。

- server 启动时（import 时）从 raw.githubusercontent.com 拉取 16 个 skill 的
  `## Output Contract` 节，拼接到 tool 描述之后。skill 在 GitHub 上优化后，
  MCP 下次启动即自动使用新契约，无需发版。
- 本地缓存 ~/.cache/laogu-mcp/contracts/<slug>.md，TTL 默认 24 小时
  （可用 LAOGU_MCP_CONTRACT_TTL_HOURS 覆盖）。
- 任何失败（网络/解析/超时）→ 自动降级为 STATIC_DESCRIPTIONS（内置快照），
  行为与旧版完全一致。LAOGU_MCP_NO_CONTRACT_SYNC=1 可彻底关闭网络同步。
"""
from __future__ import annotations

import os
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

GITHUB_ORG = "laogu-caibao"
RAW_BASE = "https://raw.githubusercontent.com/" + GITHUB_ORG
FETCH_TIMEOUT = 10
DEFAULT_TTL_HOURS = 24

TOOL_SKILLS = {
    "quote": "laogu-fundamentals",
    "ann_list": "laogu-announcements",
    "code_verify": "laogu-news",
    "market_snapshot": "laogu-morning",
    "fund_flow": "laogu-moneyflow",
    "close_recap": "laogu-close",
    "lhb_board": "laogu-lhb",
    "ann_content": "laogu-report",
    "risk_inputs": "laogu-risk",
    "earnings_ann": "laogu-earnings",
    "research_grounding": "laogu-research",
    "ir_records": "laogu-notes",
    "unlock_notices": "laogu-unlock",
    "ipo_calendar": "laogu-ipo",
    "valuation": "laogu-value",
    "macro_helper": "laogu-macro",
}

# 内置快照：与各 tool 发布时的 docstring 一致；同步失败时的降级内容。
STATIC_DESCRIPTIONS = {
    "quote": """个股行情快照（对应 skill：laogu-fundamentals 基本面速查）。

    输入 6 位股票代码（如 600519）。输出：名称/现价/涨跌幅/今开/昨收/最高/最低/
    成交量/成交额，含数据日期时点。
    输出契约：数字原样引用接口值并标注单位；取不到的字段标 null 并在 warnings 说明，
    不估算、不编造。只陈述事实，不做买卖推荐。
    数据源：腾讯行情 → 新浪行情 → 东财 push2（三级降级，见 warnings）。
    """,
    "ann_list": """个股公告列表（对应 skill：laogu-announcements 公告盯梢）。

    输入 6 位股票代码。输出最近公告：art_code（取正文用）/标题/发布时间/公告日期/栏目。
    输出契约：按发布时间倒序；只返回接口真实条目，不脑补"应有公告"。
    数据源：东财公告接口（2026-09-29 实测可用）。只陈述事实，不做买卖推荐。
    """,
    "code_verify": """新闻提及公司 → 股票代码核对（对应 skill：laogu-news 财经资讯解读）。

    输入 6 位股票代码：返回官方简称核对结果（用行情接口反查名称）。
    输入中文简称：目前无稳定公开的"简称→代码"程序化接口，诚实返回 ok=false +
    网页搜索模板，不猜测代码（猜错代码=张冠李戴，比没有更糟）。
    输出契约：核对成功返回{代码, 官方简称}；失败必须走搜索模板人工确认。
    """,
    "market_snapshot": """市场快照（对应 skill：laogu-morning 每日市场早报 / laogu-close 盘后复盘）。

    一次返回：A股四大指数（上证/深证成指/创业板/北证50）、美股三大指数（道指/纳指/标普）、
    WTI原油、COMEX黄金、离岸人民币。
    输出契约：每项带名称/现价/涨跌幅/时间；美股标注"盘中价/已收盘"（北京时间凌晨4点前
    为盘中价）；A股成交额字段不可靠时标 null 不硬写全市场成交额。只陈述事实，不做买卖推荐。
    数据源：新浪行情（2026-09-29 实测可用）。
    """,
    "fund_flow": """资金流向（对应 skill：laogu-moneyflow 资金流向解读）。

    kind="margin"：个股两融（需传 code），返回最近5个交易日融资余额/融资买入额/
    融资偿还额/融资净买入（=买入-偿还）/融券余额，数据 T+1，日期以接口实际返回为准。
    kind="lhb"：全市场龙虎榜资金（code 可空），返回最近有数据交易日的净买入/净卖出
    Top5（代码/名称/涨跌幅/净买额/席位标签）。
    输出契约：数字原样引用并标注单位与数据日期；北向资金自2024-08-19起无日度净买入
    口径，本工具不输出任何"北向净流入"数字。只陈述事实，不做买卖推荐。
    数据源：东财 datacenter（2026-09-29 实测可用）。
    """,
    "close_recap": """收盘指数快照（对应 skill：laogu-close 盘后复盘）。

    返回 A股四大指数今日涨跌幅/现价/成交量/成交额。输出契约：数字标注日期；
    涨跌家数、板块涨跌幅无稳定公开接口，缺口在 warnings 标注"未核验"，不编造。
    只陈述事实，不做买卖推荐。数据源：新浪行情。
    """,
    "lhb_board": """龙虎榜明细（对应 skill：laogu-lhb 龙虎榜夜报）。

    trade_date 为空时自动回滚到最近有数据的交易日（最多回滚10天，跳过周末）。
    输出：上榜总数 + 净买入/净卖出 TopN（代码/名称/涨跌幅/净买额/买入额/卖出额/
    上榜原因/席位标签）。
    输出契约：分类只看席位类型（机构/游资地域资金/股通），不看标签里"买入/卖出"字样；
    公共接口不提供具体营业部，绝不编造；席位标签原样引用。只陈述事实，不做买卖推荐。
    数据源：东财 datacenter RPT_DAILYBILLBOARD_DETAILSNEW（2026-09-29 实测可用）。
    """,
    "ann_content": """公告正文抓取（对应 skill：laogu-report 定期报告深拆）。

    输入 art_code（从 ann_list/ann 相关 tool 获取）。自动翻页抓取全文，去 HTML 标签，
    返回纯文本正文 + 附件(PDF)链接。
    输出契约：正文原样返回不改写；定期报告财务数字以正文为准，解读时双源交叉。
    数据源：东财公告正文接口（2026-09-29 实测可用）。
    """,
    "risk_inputs": """财务风险体检输入（对应 skill：laogu-risk 财务风险预警）。

    返回：行情快照 + 最新一期定期报告定位（从公告列表找标题含"年度报告"/"半年度报告"/
    "季度报告"的最新一条：art_code/标题/公告日期）。
    输出契约：本 tool 只做输入准备，不打分；定期报告正文需再调 ann_content 获取；
    报告期以公告标题为准，不推测。只陈述事实，不做买卖推荐。
    """,
    "earnings_ann": """业绩预告/快报扫描（对应 skill：laogu-earnings 业绩预告解读）。

    从公告列表筛选标题含"业绩预告"/"业绩快报"/"业绩预告修正"的公告，返回 art_code/
    标题/公告日期（正文用 ann_content 另取，提炼利润区间与同比口径）。
    输出契约：只返回接口真实条目；利润区间数字以正文为准，本 tool 不提前解读；
    无预告时明确返回空列表，不编造。只陈述事实，不做买卖推荐。
    """,
    "research_grounding": """研报精读 grounding 数据（对应 skill：laogu-research 研报精读）。

    返回代码核对（官方简称）+ 行情快照，供研报解读时交叉验证。
    诚实声明：研报正文无稳定公开程序化接口，本 tool 不提供研报内容；
    研报全文/链接需用户粘贴，宿主按 laogu-research 的 Output Contract 解读。
    只陈述事实，不做买卖推荐。
    """,
    "ir_records": """投资者关系活动记录表搜索（对应 skill：laogu-notes 调研纪要解读）。

    从公告列表筛选标题含"投资者关系活动记录表"的公告，返回 art_code/标题/公告日期
    （纪要正文用 ann_content 另取后结构化提炼）。
    输出契约：只返回真实条目；无记录表时返回空列表并标注，不编造调研内容。
    只陈述事实，不做买卖推荐。数据源：东财公告接口。
    """,
    "unlock_notices": """限售解禁公告搜索（对应 skill：laogu-unlock 解禁冲击评估）。

    从公告列表筛选标题含"限售股份上市流通"/"限售股上市流通"/"解除限售"的公告，
    返回 art_code/标题/公告日期（解禁规模/占比/股东性质以正文为准，用 ann_content 另取）。
    输出契约：抛压定级需正文数据，本 tool 只做公告定位；无相关公告时返回空列表。
    只陈述事实，不做买卖推荐。数据源：东财公告接口。
    """,
    "ipo_calendar": """打新日历（对应 skill：laogu-ipo）。

    诚实声明：新股申购/上市日期无稳定公开程序化接口（2026-09-29 实测结论），
    本 tool 返回 ok=false + 本周打新搜索模板，不编造任何新股代码/日期/发行价。
    宿主应按 laogu-ipo 的 Output Contract 用搜索结果生成日历，无新股时明确说明。
    """,
    "valuation": """估值锚输入（对应 skill：laogu-value 估值定位）。

    返回当前 PE(TTM)/PB/市值/现价（能取到的才给）。输出契约：历史分位区间与同行
    对比无稳定公开接口，缺口在 warnings 标注"未核验"，由宿主用搜索补足；
    只描述分位位置，不做买卖推荐。
    数据源：腾讯行情（含PE/PB）→ 新浪 → 东财push2。
    """,
    "macro_helper": """宏观日历辅助（对应 skill：laogu-macro 宏观日历解读）。

    返回：指定日期（默认今天）是否为 A 股交易日（周末直接判否；工作日用龙虎榜
    数据存在性交叉验证，标注为启发式）、北京时间、美股时段说明。
    诚实声明：议息会议/PMI/CPI 等事件无统一公开 API，本 tool 不编造事件日历，
    返回搜索模板由宿主按 laogu-macro 的 Output Contract 生成。
    只陈述事实，不做买卖推荐。
    """,
}


def _cache_dir():
    d = os.environ.get("LAOGU_MCP_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "laogu-mcp", "contracts")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        d = os.path.join(tempfile.gettempdir(), "laogu-mcp-contracts")
        os.makedirs(d, exist_ok=True)
        return d


def _fetch_raw(slug):
    url = "%s/%s/main/SKILL.md" % (RAW_BASE, slug)
    req = urllib.request.Request(url, headers={"User-Agent": "laogu-mcp/contract-sync"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            if r.status != 200:
                return None
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _extract_contract(md):
    lines = md.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "## Output Contract":
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    text = "\n".join(lines[start:end]).strip()
    return text or None


def _get_contract(slug, ttl_s):
    cdir = _cache_dir()
    p = os.path.join(cdir, "%s.md" % slug)
    text = None
    if os.path.exists(p):
        try:
            if time.time() - os.path.getmtime(p) < ttl_s:
                with open(p, encoding="utf-8") as f:
                    text = f.read()
        except Exception:
            text = None
    if text is None:
        raw = _fetch_raw(slug)
        if raw is None:
            return None
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(raw)
        except Exception:
            pass
        text = raw
    contract = _extract_contract(text)
    if not contract:
        return None
    return contract, date.today().isoformat()


def resolve_all():
    out = dict(STATIC_DESCRIPTIONS)
    if os.environ.get("LAOGU_MCP_NO_CONTRACT_SYNC") == "1":
        return out
    try:
        ttl_s = float(os.environ.get("LAOGU_MCP_CONTRACT_TTL_HOURS",
                                     str(DEFAULT_TTL_HOURS))) * 3600
    except ValueError:
        ttl_s = DEFAULT_TTL_HOURS * 3600
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            fut_to_name = {ex.submit(_get_contract, slug, ttl_s): name
                           for name, slug in TOOL_SKILLS.items()}
            for fut, name in fut_to_name.items():
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res:
                    text, sync_date = res
                    slug = TOOL_SKILLS[name]
                    out[name] = (
                        STATIC_DESCRIPTIONS[name]
                        + "\n\n【以下输出契约同步自 GitHub 最新版 skill（"
                        + slug + "），同步于 " + sync_date + "】\n"
                        + text
                    )
    except Exception:
        pass
    return out
