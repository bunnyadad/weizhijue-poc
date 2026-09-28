import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("jev_probe", Path(__file__).resolve().parent.parent / "scripts/jev_probe.py")
jp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jp)
jp.wait_ready = lambda b, seconds=0, settle=0: None

SRC = {"id": "x", "name": "測試醫院", "city": "台北", "url": "https://h.example.tw/"}
PAGES = {
    "https://h.example.tw/": ("首頁 健檢專區", [["https://h.example.tw/plan", "健檢方案"], ["https://other.tw/plan", "健檢方案"],
                                           ["https://h.example.tw/news", "最新消息"]]),
    "https://h.example.tw/plan": ("無痛胃鏡＋大腸鏡 方案 18,000 元", []),
}


class FakeBrowser:
    closed = 0

    def __init__(self, url):
        self.url = url

    def call(self, method, **params):
        if method == "Page.navigate":
            self.url = params["url"]

    def evaluate(self, expr):
        text, links = PAGES.get(self.url, ("", []))
        if "innerText" in expr and "querySelectorAll" not in expr:
            return text
        import json
        return json.dumps(links)

    def close(self):
        FakeBrowser.closed += 1


class FakeAgent:
    def __init__(self, url, goal):
        self.browser = FakeBrowser("https://h.example.tw/plan")

    def run(self):
        yield {"page": {"url": self.browser.url}, "history": [1, 2], "status": "done"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class JevProbeTest(unittest.TestCase):
    def test_follows_same_host_subpage(self):
        FakeBrowser.closed = 0
        row, text = jp.probe_browser(SRC, FakeBrowser)
        self.assertEqual(row["method"], "browser_subpage")
        self.assertEqual(row["subpage"]["url"], "https://h.example.tw/plan")
        self.assertIn("18,000", text)
        self.assertEqual(FakeBrowser.closed, 1)

    def test_direct_hit(self):
        row, _ = jp.probe_browser({**SRC, "url": "https://h.example.tw/plan"}, FakeBrowser)
        self.assertEqual(row["method"], "browser")

    def test_blocked(self):
        PAGES["https://blk.tw/"] = ("Just a moment... Cloudflare", [])
        row, _ = jp.probe_browser({**SRC, "url": "https://blk.tw/"}, FakeBrowser)
        self.assertEqual(row["method"], "blocked")

    def test_browser_error(self):
        class Boom:
            def __init__(self, url):
                raise RuntimeError("Chrome 未開啟遠端除錯")
        row, _ = jp.probe_browser(SRC, Boom)
        self.assertEqual(row["method"], "error")
        self.assertIn("遠端除錯", row["error"])

    def test_agent(self):
        row, _ = jp.probe_agent(SRC, FakeAgent)
        self.assertEqual((row["method"], row["final_url"], row["steps"]), ("agent", "https://h.example.tw/plan", 2))


if __name__ == "__main__":
    unittest.main()
