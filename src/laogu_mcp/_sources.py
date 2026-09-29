"""共享数据源抓取层：所有 tool 的程序化接口集中在这里。

实测基线（2026-09-29 沙箱）：
- 新浪行情 ✅ ｜ 东财公告列表/正文 ✅ ｜ 东财龙虎榜 ✅ ｜ 新浪美股/指数 ✅
- 腾讯行情 ❌ 本沙箱 TCP 超时（降级用） ｜ 东财 push2 ❌ 502（降级用）
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
SINA_REFERER = {"Referer": "https://finance.sina.com.cn/"}
BJ = timezone(timedelta(hours=8))


def now_bj() -> str:
    return datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")


def _get(url: str, referer: bool = False, timeout: int = 15, gbk: bool = False) -> str:
    headers = dict(UA)
    if referer:
        headers.update(SINA_REFERER)
    req = urllib.request.Request(url, headers=headers)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if gbk:
        try:
            return raw.decode("gbk", errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("gbk", errors="replace")


def _code_prefix(code: str) -> tuple[str, str]:
    """返回 (sina/tx 前缀, push2 secid 前缀)。"""
    c = code.strip()
    if c[0] in ("6", "9"):
        return "sh", f"1.{c}"
    if c[0] in ("4", "8"):
        return "bj", f"0.{c}"
    return "sz", f"0.{c}"


# ---------------- 配置热更新辅助 ----------------
# cfg 来自 _config.get_config(slug)，缺失/非法时全部回退到代码默认值，
# 行为与旧版完全一致。

def _ep(cfg: dict | None, name: str, default: str) -> str:
    try:
        return (cfg.get("endpoints") or {}).get(name) or default
    except Exception:
        return default


def _fm(cfg: dict | None, parser: str, field: str, default: int) -> int:
    try:
        return (cfg.get("field_map") or {}).get(parser, {}).get(field, default)
    except Exception:
        return default


def _fallback_order(cfg: dict | None) -> list[str]:
    try:
        fo = cfg.get("fallback_order")
        if isinstance(fo, list) and fo:
            return [str(x) for x in fo]
    except Exception:
        pass
    return ["tencent", "sina", "push2"]


def _report(cfg: dict | None, name: str, key: str, default: str) -> str:
    try:
        return (cfg.get("reports") or {}).get(name, {}).get(key) or default
    except Exception:
        return default


def _keywords(cfg: dict | None, name: str, default: list[str]) -> list[str]:
    try:
        kw = (cfg.get("keywords") or {}).get(name)
        if isinstance(kw, list) and kw:
            return [str(x) for x in kw]
    except Exception:
        pass
    return default


# ---------------- 行情 ----------------

def sina_quote(codes: list[str], cfg: dict | None = None) -> dict:
    """新浪行情：A 股逐笔快照。返回 {code: {...}}。字段索引可被配置覆盖。"""
    mk, _ = _code_prefix(codes[0])
    q = ",".join(_code_prefix(c)[0] + c for c in codes)
    url = _ep(cfg, "sina_quote", "https://hq.sinajs.cn/list={codes}").format(codes=q)
    body = _get(url, referer=True, gbk=True)
    fm = lambda f, d: _fm(cfg, "sina_quote", f, d)
    out = {}
    for line in body.strip().splitlines():
        m = re.match(r'var hq_str_([a-z]{2})(\d+)="([^"]*)";', line)
        if not m:
            continue
        code, f = m.group(2), m.group(3).split(",")
        if len(f) < 32:
            continue
        try:
            out[code] = {
                "名称": f[fm("name", 0)], "今开": float(f[fm("open", 1)]),
                "昨收": float(f[fm("prev_close", 2)]), "现价": float(f[fm("price", 3)]),
                "最高": float(f[fm("high", 4)]), "最低": float(f[fm("low", 5)]),
                "成交量_股": int(float(f[fm("volume", 8)])),
                "成交额_元": float(f[fm("amount", 9)]),
                "日期": f[fm("date", 30)], "时间": f[fm("time", 31)],
            }
            out[code]["涨跌幅_%"] = round((out[code]["现价"] - out[code]["昨收"]) / out[code]["昨收"] * 100, 2) if out[code]["昨收"] else None
        except (ValueError, IndexError):
            continue
    return out


def tencent_quote(codes: list[str], cfg: dict | None = None) -> dict:
    """腾讯行情（本沙箱常超时，仅作降级）。字段索引可被配置覆盖。"""
    q = ",".join(_code_prefix(c)[0] + c for c in codes)
    url = _ep(cfg, "tencent_quote", "https://qt.gtimg.cn/q={codes}").format(codes=q)
    body = _get(url, gbk=True)
    fm = lambda f, d: _fm(cfg, "tencent_quote", f, d)
    out = {}
    for line in body.strip().splitlines():
        m = re.match(r'v_[a-z]{2}(\d+)="([^"]*)";', line)
        if not m:
            continue
        code, f = m.group(1), m.group(2).split("~")
        if len(f) < 47:
            continue
        try:
            out[code] = {
                "名称": f[fm("name", 1)], "现价": float(f[fm("price", 3)]),
                "昨收": float(f[fm("prev_close", 4)]),
                "涨跌幅_%": float(f[fm("change_pct", 32)]) if f[fm("change_pct", 32)] else None,
                "换手率_%": float(f[fm("turnover", 38)]) if f[fm("turnover", 38)] else None,
                "PE_TTM": float(f[fm("pe_ttm", 39)]) if f[fm("pe_ttm", 39)] else None,
                "PB": float(f[fm("pb", 46)]) if f[fm("pb", 46)] else None,
            }
        except (ValueError, IndexError):
            continue
    return out


def push2_stock(code: str, cfg: dict | None = None) -> dict:
    """东财 push2：市值/PE/PB（本沙箱常 502，仅作降级）。"""
    _, secid = _code_prefix(code)
    fields = "f43,f44,f45,f46,f57,f58,f60,f116,f117,f168,f169,f170"
    url = _ep(cfg, "push2_stock",
              "https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields={fields}"
              ).format(secid=secid, fields=fields)
    d = json.loads(_get(url))
    f = d.get("data") or {}
    if d.get("rc") != 0 or not f:
        raise RuntimeError("push2 无数据")
    px = (f.get("f43") or 0) / 100
    return {
        "现价": round(px, 2), "昨收": round((f.get("f60") or 0) / 100, 2),
        "总市值_元": f.get("f116"), "流通市值_元": f.get("f117"),
        "PE_TTM": f.get("f169"), "PB": f.get("f170"),
        "每股收益": f.get("f168"),
    }


def stock_quote(code: str, cfg: dict | None = None) -> tuple[dict, list[str]]:
    """行情快照：按配置的 fallback_order 降级（默认 腾讯 → 新浪 → push2）。返回 (数据, warnings)。"""
    warnings = []
    impls = {"tencent": ("腾讯行情", lambda: tencent_quote([code], cfg)),
             "sina": ("新浪行情", lambda: sina_quote([code], cfg)),
             "push2": ("东财push2", lambda: push2_stock(code, cfg))}
    order = _fallback_order(cfg)
    tried = []
    for key in order:
        if key not in impls:
            continue
        name, fn = impls[key]
        tried.append(name)
        try:
            r = fn()
            hit = r.get(code) if key != "push2" else r
            if hit:
                if tried[0] != name:
                    warnings.append(f"{tried[0]}不可用，已降级到{name}")
                data = hit if key == "push2" else r[code]
                return data | {"_来源": name}, warnings
        except Exception as e:
            warnings.append(f"{name}失败：{type(e).__name__}，继续降级")
    raise RuntimeError(f"行情接口全部失败（已试：{'/'.join(tried)}）")


# ---------------- 指数 / 外盘 / 大宗 / 汇率 ----------------

def sina_batch(symbols: list[str], cfg: dict | None = None) -> dict:
    """新浪批量：s_ 指数 / gb_ 美股 / hf_ 期货 / fx_ 外汇。"""
    url = _ep(cfg, "sina_batch", "https://hq.sinajs.cn/list={symbols}").format(
        symbols=",".join(symbols))
    body = _get(url, referer=True, gbk=True)
    out = {}
    for line in body.strip().splitlines():
        m = re.match(r'var hq_str_([A-Za-z0-9_]+)="([^"]*)";', line)
        if not m:
            continue
        sym, f = m.group(1), m.group(2).split(",")
        try:
            if sym.startswith("s_"):  # A股指数：名,现价,涨跌额,涨跌幅,量,额
                out[sym] = {"名称": f[0], "现价": float(f[1]), "涨跌额": float(f[2]),
                            "涨跌幅_%": float(f[3]), "成交量_手": f[4], "成交额_元": f[5]}
            elif sym.startswith("hf_"):  # 期货：现价,,买,卖,最高,最低,时间,昨收,今开,…,日期,名称
                px, zs = float(f[0]), float(f[7])
                out[sym] = {"名称": f[13], "现价": px, "时间": f[6],
                            "涨跌幅_%": round((px - zs) / zs * 100, 2) if zs else None,
                            "涨跌额": round(px - zs, 3) if zs else None}
            elif sym.startswith("fx_"):  # 外汇：时间,现价,…,名称,涨跌幅,涨跌额
                out[sym] = {"名称": f[9], "现价": float(f[1]), "涨跌幅_%": float(f[10]),
                            "时间": f[0], "涨跌额": float(f[11])}
            else:  # gb_ 美股：名,现价,涨跌幅,时间,涨跌额
                out[sym] = {"名称": f[0], "现价": float(f[1]), "涨跌幅_%": float(f[2]),
                            "时间": f[3], "涨跌额": float(f[4])}
        except (ValueError, IndexError):
            continue
    return out


# ---------------- 东财公告 ----------------

def eastmoney_ann_list(code: str, page_size: int = 20, page_index: int = 1,
                       cfg: dict | None = None) -> list[dict]:
    """东财公告列表。stock_list 用纯数字代码。"""
    url = _ep(cfg, "eastmoney_ann_list",
              "https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1"
              "&page_size={page_size}&page_index={page_index}&ann_type=A"
              "&client_source=web&stock_list={code}").format(
                  page_size=page_size, page_index=page_index, code=code)
    d = json.loads(_get(url))
    items = []
    for it in (d.get("data") or {}).get("list", []):
        items.append({
            "art_code": it.get("art_code"), "标题": it.get("title"),
            "发布时间": it.get("display_time"), "公告日期": (it.get("notice_date") or "")[:10],
            "栏目": [c.get("column_name") for c in it.get("columns", [])],
        })
    return items


def _strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(re.sub(r"\n{3,}", "\n\n", s)).strip()


def eastmoney_ann_content(art_code: str, max_pages: int = 5,
                           cfg: dict | None = None) -> dict:
    """东财公告正文（多页）。返回 {正文, 页数, 附件}。"""
    parts, page_size, attach = [], 1, []
    tmpl = _ep(cfg, "eastmoney_ann_content",
               "https://np-cnotice-stock.eastmoney.com/api/content/ann"
               "?art_code={art_code}&client_source=web&page_index={page}")
    for p in range(1, max_pages + 1):
        url = tmpl.format(art_code=art_code, page=p)
        d = json.loads(_get(url)).get("data") or {}
        page_size = int(d.get("page_size") or 1)
        c = d.get("notice_content") or ""
        if c:
            parts.append(_strip_html(c))
        if p == 1:
            attach = [{"文件名": a.get("attach_name"), "链接": a.get("attach_url")}
                      for a in d.get("attach_list") or []]
        if p >= page_size:
            break
    return {"art_code": art_code, "正文": "\n\n".join(parts), "页数": page_size, "附件": attach}


# ---------------- 龙虎榜 / 两融 ----------------

def _dc(report: str, columns: str, extra: str, page_size: int = 200,
        cfg: dict | None = None) -> list[dict]:
    params = {"reportName": report, "columns": columns, "source": "WEB", "client": "WEB",
              "pageSize": str(page_size), "pageNumber": "1"}
    params.update(dict(urllib.parse.parse_qsl(extra)))
    base = _ep(cfg, "eastmoney_datacenter",
               "https://datacenter-web.eastmoney.com/api/data/v1/get?{params}")
    url = base.format(params=urllib.parse.urlencode(params))
    d = json.loads(_get(url))
    return (d.get("result") or {}).get("data", [])


def lhb_board(trade_date: str, page_size: int = 200, cfg: dict | None = None) -> list[dict]:
    """龙虎榜明细。trade_date=YYYY-MM-DD。报表名/列可被配置覆盖。"""
    extra = urllib.parse.urlencode({"filter": f"(TRADE_DATE='{trade_date}')"})
    report = _report(cfg, "lhb", "report", "RPT_DAILYBILLBOARD_DETAILSNEW")
    columns = _report(cfg, "lhb", "columns", "ALL")
    rows = _dc(report, columns, extra, page_size, cfg)
    out = []
    for r in rows:
        out.append({
            "代码": (r.get("SECUCODE") or "").split(".")[0], "名称": r.get("SECURITY_NAME_ABBR"),
            "涨跌幅_%": r.get("CHANGE_RATE"), "上榜原因": r.get("EXPLANATION"),
            "席位标签": r.get("EXPLAIN"),
            "龙虎榜净买额_元": r.get("BILLBOARD_NET_AMT"),
            "龙虎榜买入额_元": r.get("BILLBOARD_BUY_AMT"),
            "龙虎榜卖出额_元": r.get("BILLBOARD_SELL_AMT"),
        })
    return out


def margin_stock(code: str, days: int = 5, cfg: dict | None = None) -> list[dict]:
    """个股两融（T+1）。filter 用 URL 编码双引号，实测生效。"""
    extra = urllib.parse.urlencode({
        "filter": f'(SCODE="{code}")', "sortColumns": "DATE", "sortTypes": "-1"})
    report = _report(cfg, "margin", "report", "RPTA_WEB_RZRQ_GGMX")
    columns = _report(cfg, "margin", "columns",
                      "DATE,SCODE,RZYE,RZMRE,RZCHE,RQYE,RQCHL,RZRQYE")
    rows = _dc(report, columns, extra, days, cfg)
    out = []
    for r in rows:
        if str(r.get("SCODE")) != code:
            continue  # filter 失效时的兜底校验
        m, c = r.get("RZMRE") or 0, r.get("RZCHE") or 0
        out.append({"日期": (r.get("DATE") or "")[:10], "代码": code,
                    "融资余额_元": r.get("RZYE"), "融资买入额_元": m, "融资偿还额_元": c,
                    "融资净买入_元": m - c, "融券余额_元": r.get("RQYE")})
    return out


# ---------------- 多策略选股 ----------------

def _epf(cfg: dict | None, name: str, default: str, **kw) -> str:
    """用命名参数格式化 endpoint 模板（热更新可覆盖）。"""
    return _ep(cfg, name, default).format(**kw)


def screener_query(report: str, columns: str, filter_str: str = "",
                   page_size: int = 50, sort_columns: str = "",
                   sort_types: str = "", cfg: dict | None = None) -> list[dict]:
    """东财 datacenter 条件选股。filter 语法见 skill 文档；filter/sort 可能超时，失败抛异常由 tool 层降级。"""
    url = _epf(cfg, "eastmoney_datacenter",
               "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName={report}&columns={columns}&source=WEB&client=WEB&filter={filter}&pageSize={page_size}&pageNumber={page_number}&sortColumns={sort_columns}&sortTypes={sort_types}",
               report=report, columns=columns,
               filter=urllib.parse.quote(filter_str or ""),
               page_size=page_size, page_number=1,
               sort_columns=sort_columns, sort_types=sort_types)
    d = json.loads(_get(url))
    return (d.get("result") or {}).get("data", [])


# ---------------- 基金 ----------------

def fund_profile(code: str, cfg: dict | None = None) -> dict:
    """天天基金 mobile 接口：基本信息 + 详情。返回 Data 原样，由宿主按 skill 契约解读。"""
    out, warnings = {}, []
    for ep_name in ("fund_basic", "fund_detail"):
        tmpl = _ep(cfg, ep_name, "")
        if not tmpl:
            warnings.append(f"{ep_name} 未配置")
            continue
        try:
            d = json.loads(_get(tmpl.format(code=code.strip())))
            out[ep_name] = d.get("Data") or {}
        except Exception as e:
            warnings.append(f"{ep_name}失败：{type(e).__name__}")
    if not out:
        raise RuntimeError("天天基金 mobile 接口全部失败（" + "；".join(warnings) + "）")
    out["_warnings"] = warnings
    return out


# ---------------- 情绪周期 ----------------

SENTIMENT_SEARCH_DIMS = [
    ("涨跌家数比/涨跌停数", "{date} A股 涨跌家 涨停 跌停 数据宝"),
    ("连板高度/炸板率", "{date} 连板高度 炸板率 复盘"),
    ("两市成交额", "{date} 两市成交额 万亿"),
    ("北向成交占比", "{date} 北向资金 成交额 占比 东方财富"),
]

def sentiment_inputs(cfg: dict | None = None) -> dict:
    """市场测温输入：指数行情程序化直取；其余维度诚实走搜索模板（见 skill 文档 D1-D7）。"""
    date_s = datetime.now(BJ).strftime("%Y-%m-%d")
    idx = {}
    try:
        idx = sina_batch(["s_sh000001", "s_sz399001", "s_sz399006"], cfg)
    except Exception as e:
        idx = {"_error": f"指数行情失败：{type(e).__name__}"}
    return {"日期": date_s,
            "指数_程序化": idx,
            "需网页搜索补齐的维度": [
                {"维度": name, "搜索模板": tpl.format(date=date_s)}
                for name, tpl in SENTIMENT_SEARCH_DIMS],
            "口径说明": "D1-D5/D7 主力为网页搜索模板（2026-09-29 实测：push2 在本沙箱不可用）；阈值见 skill 文档 v1.0 初版"}


# ---------------- 可转债 ----------------

_CB_DEFAULT_COLS = ("SECURITY_CODE,SECURITY_NAME_ABBR,TRANSFER_VALUE,"
                    "EXPIRE_DATE,LISTING_DATE,CONVERT_STOCK_CODE,RATING")
# 注（2026-09-29 实测）：CONVERT_PRICE / PREMIUM_RATIO / CURR_ISSUE_AMT /
# SELLBACK_PRICE 等字段在 RPT_BOND_CB_LIST 中非法（含之则整单返回 0 条）；
# TRANSFER_PRICE / CURRENT_BOND_PRICE 合法但全空。转股价/溢价率走公告+腾讯现算。

def _pick(row: dict, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None

def cb_bond_list(page: int = 1, page_size: int = 100,
                 cfg: dict | None = None) -> list[dict]:
    """可转债名单（东财 RPT_BOND_CB_LIST）。注意：该报表转股价/现价字段常为空，
    现价与转股价值需走腾讯行情加公式现算（见 skill 文档）。"""
    url = _epf(cfg, "eastmoney_datacenter",
               "https://datacenter-web.eastmoney.com/api/data/v1/get?pageSize={page_size}&pageNumber={page}&reportName=RPT_BOND_CB_LIST&columns={columns}&source=WEB&client=WEB",
               page=page, page_size=page_size,
               columns=urllib.parse.quote(_CB_DEFAULT_COLS))
    d = json.loads(_get(url))
    rows = (d.get("result") or {}).get("data", [])
    out = []
    for r in rows:
        out.append({
            "代码": _pick(r, "SECURITY_CODE"),
            "名称": _pick(r, "SECURITY_NAME_ABBR"),
            "转股价值": _pick(r, "TRANSFER_VALUE"),
            "正股代码": _pick(r, "CONVERT_STOCK_CODE"),
            "评级": _pick(r, "RATING"),
            "上市日": (_pick(r, "LISTING_DATE") or "")[:10],
            "到期日": (_pick(r, "EXPIRE_DATE") or "")[:10],
            "口径说明": "转股价/溢价率/现价本报表无，走公告+腾讯行情现算",
        })
    return out


# ---------------- 观点追踪 ----------------

def thesis_grounding(code: str, cfg: dict | None = None) -> dict:
    """观点回检取数：行情快照 + 近期公告（复用 fundamentals/news 已验证接口）。"""
    quote, warnings = stock_quote(code.strip(), cfg)
    anns = []
    try:
        anns = eastmoney_ann_list(code.strip(), page_size=5, cfg=cfg)
    except Exception as e:
        warnings.append(f"公告列表失败：{type(e).__name__}")
    return {"行情": quote, "近期公告": anns, "_warnings": warnings}
