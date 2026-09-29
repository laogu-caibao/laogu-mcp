"""Skill 数据源配置热更新（混合方案 C 的核心）。

- 每个 laogu-* skill 仓库根目录有一份 `mcp-config.json`（schema 见仓库根 `config-schema.md`），
  内容：endpoints（URL 模板）/ symbols（快照标的表）/ fallback_order（降级顺序）/
  field_map（解析字段索引）/ reports（datacenter 报表名）/ keywords（标题过滤词）。
- tool 每次调用时经 `get_config(slug)` 读取；skill 优化（换数据源、改字段口径、调降级顺序）
  只需改仓库里的 `mcp-config.json` 并升 `config_version`，MCP 下次调用即生效，零发版。
- 优先级：live（GitHub raw，TTL 1 小时）→ cache（过期缓存兜底）→ bundled（包内快照）
  → unavailable（空配置，_sources 全部走代码默认值，行为与旧版一致）。
- meta 如实标注 config_source + config_version；LAOGU_MCP_NO_CONFIG_SYNC=1 可关闭网络同步。
- 与 _contracts.py 的区别：_contracts 同步的是"输出契约文本"（给宿主 LLM 看的 tool 描述）；
  本模块同步的是"抓取参数"（给取数代码用的 URL/字段/顺序）。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.request
from importlib import resources

GITHUB_ORG = "laogu-caibao"
RAW_URL = "https://raw.githubusercontent.com/%s/{slug}/main/mcp-config.json" % GITHUB_ORG
FETCH_TIMEOUT = 10
DEFAULT_TTL_S = 3600  # 1 小时


def _cache_dir() -> str:
    d = os.environ.get("LAOGU_MCP_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "laogu-mcp", "configs")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        d = os.path.join(tempfile.gettempdir(), "laogu-mcp-configs")
        os.makedirs(d, exist_ok=True)
        return d


def _bundled(slug: str) -> dict | None:
    try:
        data = (resources.files("laogu_mcp") / "_bundled" / f"{slug}.json").read_bytes()
        cfg = json.loads(data.decode("utf-8"))
        return cfg if _valid(cfg) else None
    except Exception:
        return None


def _valid(cfg) -> bool:
    return (isinstance(cfg, dict) and isinstance(cfg.get("skill"), str)
            and isinstance(cfg.get("config_version"), str))


def _fetch_raw(slug: str) -> dict | None:
    req = urllib.request.Request(
        RAW_URL.format(slug=slug),
        headers={"User-Agent": "laogu-mcp/config-sync"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            if r.status != 200:
                return None
            cfg = json.loads(r.read().decode("utf-8"))
            return cfg if _valid(cfg) else None
    except Exception:
        return None


def _read_cache(slug: str) -> tuple[dict | None, bool]:
    """返回 (config, 是否新鲜)。"""
    try:
        ttl_s = float(os.environ.get("LAOGU_MCP_CONFIG_TTL", str(DEFAULT_TTL_S)))
    except ValueError:
        ttl_s = DEFAULT_TTL_S
    p = os.path.join(_cache_dir(), f"{slug}.json")
    if not os.path.exists(p):
        return None, False
    try:
        with open(p, encoding="utf-8") as f:
            wrap = json.load(f)
        cfg = wrap.get("config")
        if not _valid(cfg):
            return None, False
        fresh = (time.time() - wrap.get("cached_at", 0)) < ttl_s
        return cfg, fresh
    except Exception:
        return None, False


def _write_cache(slug: str, cfg: dict) -> None:
    try:
        with open(os.path.join(_cache_dir(), f"{slug}.json"), "w", encoding="utf-8") as f:
            json.dump({"cached_at": time.time(), "config": cfg}, f)
    except Exception:
        pass


def get_config(slug: str) -> tuple[dict, str, str]:
    """返回 (config, source, version)。

    source ∈ {"live", "cache", "bundled", "unavailable"}。
    任何异常都不抛：最差返回 ({}, "unavailable", "none")，调用方走代码默认值。
    """
    empty = ({}, "unavailable", "none")
    if os.environ.get("LAOGU_MCP_NO_CONFIG_SYNC") == "1":
        b = _bundled(slug)
        return (b, "bundled", b["config_version"]) if b else empty
    cached, fresh = _read_cache(slug)
    if fresh and cached is not None:
        return cached, "cache", cached["config_version"]
    live = _fetch_raw(slug)
    if live is not None:
        _write_cache(slug, live)
        return live, "live", live["config_version"]
    if cached is not None:  # live 失败，用过期缓存兜底
        return cached, "cache", cached["config_version"]
    b = _bundled(slug)
    if b is not None:
        return b, "bundled", b["config_version"]
    return empty
