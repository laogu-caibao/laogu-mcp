"""_config 热更新 loader 的离线单测（全部 mock，不碰真实网络）。

运行：.venv/bin/python -m pytest tests/ -q
或：.venv/bin/python tests/test_config.py
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from laogu_mcp import _config as C

GOOD = {"skill": "laogu-morning", "config_version": "9.9.9",
        "updated": "2026-09-29", "endpoints": {"sina_batch": "https://x/{symbols}"}}


def _resp(payload: bytes, status=200):
    m = mock.MagicMock()
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    m.status = status
    m.read.return_value = payload
    return m


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"LAOGU_MCP_CACHE_DIR": self.tmp,
                    "LAOGU_MCP_NO_CONTRACT_SYNC": "1"}
        self._p = mock.patch.dict(os.environ, self.env)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_live_fetch(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=_resp(json.dumps(GOOD).encode())):
            cfg, src, ver = C.get_config("laogu-morning")
        self.assertEqual(src, "live")
        self.assertEqual(ver, "9.9.9")
        self.assertEqual(cfg["endpoints"]["sina_batch"], "https://x/{symbols}")
        # 缓存文件已落盘
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "laogu-morning.json")))

    def test_cache_hit_when_live_fails(self):
        # 先写一份新鲜缓存
        with open(os.path.join(self.tmp, "laogu-morning.json"), "w") as f:
            json.dump({"cached_at": time.time(), "config": GOOD}, f)
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("boom")):
            cfg, src, ver = C.get_config("laogu-morning")
        self.assertEqual(src, "cache")
        self.assertEqual(ver, "9.9.9")

    def test_bundled_fallback(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            cfg, src, ver = C.get_config("laogu-morning")
        self.assertEqual(src, "bundled")
        self.assertEqual(cfg["skill"], "laogu-morning")
        self.assertEqual(ver, "1.0.0")

    def test_invalid_json_falls_back(self):
        with mock.patch("urllib.request.urlopen",
                         return_value=_resp(b"not json")):
            cfg, src, ver = C.get_config("laogu-morning")
        self.assertEqual(src, "bundled")  # 非法 → 跳过 live，走 bundled

    def test_invalid_schema_falls_back(self):
        bad = {"nope": 1}
        with mock.patch("urllib.request.urlopen",
                         return_value=_resp(json.dumps(bad).encode())):
            cfg, src, ver = C.get_config("laogu-ipo")
        self.assertEqual(src, "bundled")

    def test_no_sync_env_uses_bundled(self):
        env = dict(self.env, LAOGU_MCP_NO_CONFIG_SYNC="1")
        with mock.patch.dict(os.environ, env):
            with mock.patch("urllib.request.urlopen",
                             side_effect=AssertionError("不应请求网络")):
                cfg, src, ver = C.get_config("laogu-close")
        self.assertEqual(src, "bundled")
        self.assertEqual(ver, "1.0.0")

    def test_unknown_slug_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            cfg, src, ver = C.get_config("laogu-not-exist")
        self.assertEqual((cfg, src, ver), ({}, "unavailable", "none"))

    def test_all_16_bundled_valid(self):
        slugs = ["laogu-morning", "laogu-close", "laogu-fundamentals", "laogu-announcements",
                 "laogu-earnings", "laogu-news", "laogu-notes", "laogu-report", "laogu-research",
                 "laogu-risk", "laogu-value", "laogu-moneyflow", "laogu-lhb", "laogu-ipo",
                 "laogu-unlock", "laogu-macro"]
        for slug in slugs:
            b = C._bundled(slug)
            self.assertIsNotNone(b, slug)
            self.assertTrue(C._valid(b), slug)


if __name__ == "__main__":
    unittest.main(verbosity=1)
