import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from wzj import extract, watch
from wzj.data import load
from wzj.match import CHOICE, INFERRED, NO, REPLACED, YES, search
from wzj.parser import parse, parse_budget

CAT = load()
TODAY = date(2026, 9, 28)


class ParserTest(unittest.TestCase):
    def test_budget(self):
        self.assertEqual(parse_budget("4 萬元內"), 40000)
        self.assertEqual(parse_budget("預算3萬5"), 35000)
        self.assertEqual(parse_budget("35,000 以內"), 35000)
        self.assertEqual(parse_budget("預算 32000"), 32000)
        self.assertIsNone(parse_budget("越便宜越好"))

    def test_proposal_example(self):
        q = parse("台北、4 萬元內、想做無痛胃鏡加大腸鏡，女性。", CAT)
        self.assertEqual((q.city, q.budget, q.gender), ("台北", 40000, "F"))
        self.assertEqual(q.required, ["sedation", "egd", "colonoscopy"])

    def test_need_keyword(self):
        q = parse("台北 男 腸胃 最便宜的", CAT)
        self.assertEqual(set(q.required), {"egd", "colonoscopy"})

    def test_longest_synonym_wins(self):
        q = parse("要做全大腸鏡", CAT)
        self.assertEqual(q.required, ["colonoscopy"])


class MatchTest(unittest.TestCase):
    def run_q(self, text, n=10):
        return search(parse(text, CAT), CAT, TODAY, n)

    def test_base_plus_addon(self):
        res, _, _ = self.run_q("台北、4 萬元內、想做無痛胃鏡加大腸鏡，女性。")
        top = res[0]
        self.assertEqual(top.inst_id, "ntuh")
        self.assertEqual(top.price, 25000 + 13700)
        self.assertTrue(all(s == YES for s, _ in top.coverage.values()))

    def test_budget_filter(self):
        res, more, dropped = self.run_q("台北 3 萬以內 男生 胃鏡大腸鏡都要")
        self.assertTrue(all(r.price is None or r.price <= 30000 for r in res + more))
        self.assertIn("ntuh", {r.inst_id for r in dropped})

    def test_gender_filter(self):
        res, more, _ = self.run_q("台北 男 胃鏡 大腸鏡")
        names = {p.id for r in res + more for p in r.parts}
        self.assertNotIn("skh-women", names)
        self.assertNotIn("ntuh-base-f", names)

    def test_replaces(self):
        res, _, _ = self.run_q("台北 男 胃鏡 大腸鏡 胸部X光")
        skh = next(r for r in res if r.parts[0].id == "skh-fine-m-ldct")
        self.assertEqual(skh.coverage["cxr"][0], REPLACED)

    def test_choice_only_one(self):
        res, more, dropped = self.run_q("台北 女 胃鏡大腸鏡 乳房超音波和乳房攝影都要")
        lianan = next(r for r in dropped if r.inst_id == "lianan")
        states = sorted(lianan.coverage[i][0] for i in ("breast_us", "mammo"))
        self.assertEqual(states, sorted([CHOICE, NO]))

    def test_explicit_no_drops(self):
        _, _, dropped = self.run_q("台北 男 胃鏡 大腸鏡 PSA")
        self.assertIn("mj", {r.inst_id for r in dropped})

    def test_inferred_ranks_below_confirmed(self):
        res, _, _ = self.run_q("台北 5 萬內 女 胃鏡 大腸鏡")
        self.assertEqual(res[0].inst_id, "ntuh")
        self.assertEqual(res[1].coverage["egd"][0], INFERRED)

    def test_unpublished_price_kept(self):
        res, more, _ = self.run_q("台北 胃鏡 大腸鏡")
        self.assertIn("tpech", {r.inst_id for r in res + more})

    def test_cheapest(self):
        res, _, _ = self.run_q("台北 男 腸胃 最便宜的")
        self.assertEqual(res[0].inst_id, "skh")
        self.assertEqual(res[0].price, 27800)

    def test_stale(self):
        res, _, _ = search(parse("台北 胃鏡", CAT), CAT, date(2026, 12, 1))
        self.assertTrue(all(r.stale for r in res))


class ExtractTest(unittest.TestCase):
    def test_score(self):
        # 模擬的 LLM 輸出，只用來驗證評分邏輯
        fake = {
            "mj-gi": {"price": 28900, "items": {"hp": "yes", "pepsinogen": "yes", "ca199": "yes", "psa": "no", "ca125": "no"}},
            "ntuh-gi": {"price": 13700, "items": {"egd": "yes", "colonoscopy": "yes", "sedation": "yes"}},
            "lianan-adv-b-gi": {"price": 38600, "items": {"breast_us": "choice", "mammo": "no"}},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(fake, f)
        res = extract.evaluate(CAT, extract.FileExtractor(Path(f.name)))
        self.assertEqual(res["plans"], 3)
        self.assertAlmostEqual(res["plan_error_rate"], 2 / 3)
        bad = {d["plan"]: d for d in res["details"]}
        self.assertEqual(bad["mj-gi"]["wrong_items"], ["CA19-9"])
        self.assertFalse(bad["lianan-adv-b-gi"]["price_ok"])

    def test_not_configured(self):
        with self.assertRaises(RuntimeError):
            extract.NotConfigured().extract("x", "")


class WatchTest(unittest.TestCase):
    def test_price_change_vs_noise(self):
        runs = [
            "<p>基礎 23,000 元</p><script>x</script><span>訪客 101</span>",
            "<p>基礎 23,000 元</p><span>訪客 102</span>",
            "<p>基礎 24,000 元</p><span>訪客 102</span>",
        ]
        with tempfile.TemporaryDirectory() as d:
            got = []
            for page in runs:
                got.append(watch.check(CAT, Path(d), fetcher=lambda url, page=page: page)[0])
        self.assertEqual([r["changed"] for r in got], [False, False, True])
        self.assertEqual([r["text_changed"] for r in got], [False, True, True])

    def test_fetch_error_recorded(self):
        def boom(url):
            raise OSError("reset")
        with tempfile.TemporaryDirectory() as d:
            rows = watch.check(CAT, Path(d), fetcher=boom)
        self.assertTrue(all(not r["ok"] for r in rows))


if __name__ == "__main__":
    unittest.main()
