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


# ---------------- 行情 ----------------

def sina_quote(codes: list[str]) -> dict:
    """新浪行情：A 股逐笔快照。返回 {code: {...}}。"""
    mk, _ = _code_prefix(codes[0])
    q = ",".join(_code_prefix(c)[0] + c for c in codes)
    body = _get(f"https://hq.sinajs.cn/list={q}", referer=True, gbk=True)
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
                "名称": f[0], "今开": float(f[1]), "昨收": float(f[2]),
                "现价": float(f[3]), "最高": float(f[4]), "最低": float(f[5]),
                "成交量_股": int(float(f[8])), "成交额_元": float(f[9]),
                "日期": f[30], "时间": f[31],
            }
            out[code]["涨跌幅_%"] = round((out[code]["现价"] - out[code]["昨收"]) / out[code]["昨收"] * 100, 2) if out[code]["昨收"] else None
        except (ValueError, IndexError):
            continue
    return out


def tencent_quote(codes: list[str]) -> dict:
    """腾讯行情（本沙箱常超时，仅作降级）。~ 分隔：1=名称,3=现价,4=昨收,32=涨跌幅%,38=换手率%,39=PE(TTM),46=PB。"""
    q = ",".join(_code_prefix(c)[0] + c for c in codes)
    body = _get(f"https://qt.gtimg.cn/q={q}", gbk=True)
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
                "名称": f[1], "现价": float(f[3]), "昨收": float(f[4]),
                "涨跌幅_%": float(f[32]) if f[32] else None,
                "换手率_%": float(f[38]) if f[38] else None,
                "PE_TTM": float(f[39]) if f[39] else None,
                "PB": float(f[46]) if f[46] else None,
            }
        except (ValueError, IndexError):
            continue
    return out


def push2_stock(code: str) -> dict:
    """东财 push2：市值/PE/PB（本沙箱常 502，仅作降级）。"""
    _, secid = _code_prefix(code)
    url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=" + secid +
           "&fields=f43,f44,f45,f46,f57,f58,f60,f116,f117,f168,f169,f170")
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


def stock_quote(code: str) -> tuple[dict, list[str]]:
    """行情快照：腾讯 → 新浪 → push2 三级降级。返回 (数据, warnings)。"""
    warnings = []
    for name, fn in (("腾讯行情", lambda: tencent_quote([code])),
                     ("新浪行情", lambda: sina_quote([code]))):
        try:
            r = fn()
            if r.get(code):
                if name != "腾讯行情":
                    warnings.append(f"腾讯行情不可用，已降级到{name}")
                return r[code] | {"_来源": name}, warnings
        except Exception as e:
            warnings.append(f"{name}失败：{type(e).__name__}，继续降级")
    try:
        return push2_stock(code) | {"_来源": "东财push2"}, warnings + ["腾讯/新浪均不可用，已降级到东财push2"]
    except Exception as e:
        raise RuntimeError(f"行情三级接口全部失败：{e}")


# ---------------- 指数 / 外盘 / 大宗 / 汇率 ----------------

def sina_batch(symbols: list[str]) -> dict:
    """新浪批量：s_ 指数 / gb_ 美股 / hf_ 期货 / fx_ 外汇。"""
    body = _get("https://hq.sinajs.cn/list=" + ",".join(symbols), referer=True, gbk=True)
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

def eastmoney_ann_list(code: str, page_size: int = 20, page_index: int = 1) -> list[dict]:
    """东财公告列表。stock_list 用纯数字代码。"""
    url = ("https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1"
           f"&page_size={page_size}&page_index={page_index}&ann_type=A"
           f"&client_source=web&stock_list={code}")
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


def eastmoney_ann_content(art_code: str, max_pages: int = 5) -> dict:
    """东财公告正文（多页）。返回 {正文, 页数, 附件}。"""
    parts, page_size, attach = [], 1, []
    for p in range(1, max_pages + 1):
        url = (f"https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={art_code}"
               f"&client_source=web&page_index={p}")
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

def _dc(report: str, columns: str, extra: str, page_size: int = 200) -> list[dict]:
    params = {"reportName": report, "columns": columns, "source": "WEB", "client": "WEB",
              "pageSize": str(page_size), "pageNumber": "1"}
    params.update(dict(urllib.parse.parse_qsl(extra)))
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(params)
    d = json.loads(_get(url))
    return (d.get("result") or {}).get("data", [])


def lhb_board(trade_date: str, page_size: int = 200) -> list[dict]:
    """龙虎榜明细。trade_date=YYYY-MM-DD。"""
    extra = urllib.parse.urlencode({"filter": f"(TRADE_DATE='{trade_date}')"})
    rows = _dc("RPT_DAILYBILLBOARD_DETAILSNEW", "ALL", extra, page_size)
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


def margin_stock(code: str, days: int = 5) -> list[dict]:
    """个股两融（T+1）。filter 用 URL 编码双引号，实测生效。"""
    extra = urllib.parse.urlencode({
        "filter": f'(SCODE="{code}")', "sortColumns": "DATE", "sortTypes": "-1"})
    rows = _dc("RPTA_WEB_RZRQ_GGMX",
               "DATE,SCODE,RZYE,RZMRE,RZCHE,RQYE,RQCHL,RZRQYE", extra, days)
    out = []
    for r in rows:
        if str(r.get("SCODE")) != code:
            continue  # filter 失效时的兜底校验
        m, c = r.get("RZMRE") or 0, r.get("RZCHE") or 0
        out.append({"日期": (r.get("DATE") or "")[:10], "代码": code,
                    "融资余额_元": r.get("RZYE"), "融资买入额_元": m, "融资偿还额_元": c,
                    "融资净买入_元": m - c, "融券余额_元": r.get("RQYE")})
    return out
