"""冒烟测试：16 个 tool 真实数据全跑通。运行：.venv/bin/python smoke/run.py"""
import json
import sys
import traceback

sys.path.insert(0, "src")
from laogu_mcp import server

results = []


def run(name, fn, *args, **kw):
    try:
        r = fn(*args, **kw)
        assert isinstance(r, dict) and "ok" in r and "meta" in r, "信封格式错误"
        results.append((name, "PASS" if r["ok"] else "FAIL-OK-FALSE", r))
        print(f"[{name}] ok={r['ok']} meta={r['meta'].get('source', r['meta'].get('fetched_at'))}")
        if r.get("warnings"):
            print(f"   warnings: {r['warnings']}")
        if not r["ok"]:
            print(f"   reason: {r.get('reason')}")
    except Exception:
        results.append((name, "ERROR", traceback.format_exc()))
        print(f"[{name}] ERROR\n{traceback.format_exc()}")


run("quote", server.quote, "600519")
run("ann_list", server.ann_list, "600519", 5)
run("code_verify_code", server.code_verify, "600519")
run("code_verify_name", server.code_verify, "贵州茅台")
run("market_snapshot", server.market_snapshot)
run("fund_flow_margin", server.fund_flow, "600519", "margin")
run("fund_flow_lhb", server.fund_flow, "", "lhb")
run("close_recap", server.close_recap)
run("lhb_board", server.lhb_board, "", 3)
run("ann_content", server.ann_content, "AN202608141827994407")
run("risk_inputs", server.risk_inputs, "600519")
run("earnings_ann", server.earnings_ann, "600519")
run("research_grounding", server.research_grounding, "600519")
run("ir_records", server.ir_records, "600519")
run("unlock_notices", server.unlock_notices, "600519")
run("ipo_calendar", server.ipo_calendar)
run("valuation", server.valuation, "600519")
run("macro_helper", server.macro_helper)

print("\n==== 汇总 ====")
for name, st, _ in results:
    print(f"{st:15} {name}")

with open("smoke/results.json", "w") as f:
    json.dump({n: {"status": s, "result": (r if isinstance(r, dict) else r[:500])}
               for n, s, r in results}, f, ensure_ascii=False, indent=2, default=str)
