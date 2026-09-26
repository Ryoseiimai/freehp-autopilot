"""鍵もネットも使わない単体テスト。python3 -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import failures, fetch, gate, intake, kb, post, rebuild, report, sitecheck, xapi  # noqa: E402
from autopilot.common import load_config, redact  # noqa: E402

CFG = load_config()


class XApiTest(unittest.TestCase):
    def test_signature_matches_official_example(self):
        # https://developer.x.com の「Creating a signature」の例と同じ値になること
        header = xapi.sign(
            "POST", "https://api.twitter.com/1.1/statuses/update.json",
            {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
            ("xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
             "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"),
            "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", 1318622958,
        )
        self.assertIn('oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"', header)

    def test_credentials_accept_aliases(self):
        env = {"X_CONSUMER_KEY": "a", "X_CONSUMER_SECRET": "b", "X_ACCESS_TOKEN": "c", "X_ACCESS_TOKEN_SECRET": "d"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(xapi.credentials_present())
        with mock.patch.dict(os.environ, {"X_API_KEY": "a"}, clear=True):
            self.assertFalse(xapi.credentials_present())

    def test_weighted_length(self):
        self.assertEqual(xapi.weighted_length("abc"), 3)
        self.assertEqual(xapi.weighted_length("あいう"), 6)
        self.assertEqual(xapi.weighted_length("見て https://freehp.jp/mihon/parts/ です"), 4 + 1 + 23 + 1 + 4)

    def test_assert_handle_refuses_other_account(self):
        with mock.patch.object(xapi, "me", return_value={"id": "1", "username": "horiemonAI_biz"}):
            with self.assertRaises(xapi.AccountMismatch):
                xapi.assert_handle("freehp3000")
        with mock.patch.object(xapi, "me", return_value={"id": "1", "username": "FreeHP3000"}):
            self.assertEqual(xapi.assert_handle("freehp3000")["id"], "1")


class GateTest(unittest.TestCase):
    def test_banned_words_and_zero_yen(self):
        probs = gate.hard_problems("無料で作れます。0円です。DMください", "", CFG, url_allowed=False)
        joined = " ".join(probs)
        self.assertIn("無料", joined)
        self.assertIn("0円", joined)
        self.assertIn("DM", joined)
        self.assertEqual(gate.hard_problems("制作は3,000円（税込）だけです", "", CFG, url_allowed=False), [])

    def test_numbers_must_come_from_material(self):
        self.assertTrue(any("1234" in p for p in gate.hard_problems("電話は1234番", "材料", CFG, False)))
        self.assertEqual(gate.hard_problems("営業は18時半まで", "18時半に閉店", CFG, False), [])
        self.assertEqual(gate.hard_problems("3つだけ確かめる", "", CFG, False), [])

    def test_url_rules(self):
        self.assertTrue(gate.hard_problems("見る\nhttps://freehp.jp/", "", CFG, url_allowed=False))
        self.assertEqual(gate.hard_problems("見る\nhttps://freehp.jp/", "", CFG, url_allowed=True), [])
        self.assertTrue(any("行き先" in p for p in gate.hard_problems("https://example.org/", "", CFG, True)))
        self.assertTrue(gate.hard_problems("@someone こんにちは", "", CFG, False))

    def test_similarity(self):
        a = "お店のページに営業時間を書くと、電話の問い合わせが減ります。"
        self.assertGreater(gate.similarity(a, a), 0.99)
        self.assertLess(gate.similarity(a, "写真は明るい窓ぎわで撮ると、料理の色がきれいに出ます。"), 0.2)

    def test_evaluate_picks_candidate_that_passes_everything(self):
        cands = [{"text": "無料です"}, {"text": "営業時間はページの上のほうに書くと見つけてもらいやすくなります。"}]
        scores = [{"total": 95, "scores": [10, 10, 10, 9, 10, 9, 10, 9, 9, 9], "real_names": False, "problems": []}]
        with mock.patch.object(gate, "llm_scores", return_value=scores), \
             mock.patch.object(gate.judge, "yes_no", return_value={"benefit": {"yes": True, "prob": 0.8, "reason": None, "source": "jev"},
                                                                   "accurate": {"yes": True, "prob": 0.7, "reason": None, "source": "jev"}}):
            chosen, records = gate.evaluate(cands, "営業時間", [], CFG, url_allowed=False)
        self.assertEqual(chosen["text"], cands[1]["text"])
        self.assertTrue(records[0]["hard_problems"])

    def test_evaluate_rejects_low_item_and_low_judge(self):
        cands = [{"text": "営業時間はページの上のほうに書くと見つけてもらいやすくなります。"}]
        low_item = [{"total": 91, "scores": [10, 6, 10, 10, 10, 9, 10, 9, 9, 8], "real_names": False, "problems": []}]
        with mock.patch.object(gate, "llm_scores", return_value=low_item):
            chosen, _ = gate.evaluate(cands, "営業時間", [], CFG, url_allowed=False)
        self.assertIsNone(chosen)
        ok_score = [{"total": 95, "scores": [10, 10, 10, 9, 10, 9, 10, 9, 9, 9], "real_names": False, "problems": []}]
        with mock.patch.object(gate, "llm_scores", return_value=ok_score), \
             mock.patch.object(gate.judge, "yes_no", return_value={"benefit": {"yes": False, "prob": 0.3, "reason": None, "source": "jev"},
                                                                   "accurate": {"yes": True, "prob": 0.9, "reason": None, "source": "jev"}}):
            chosen, _ = gate.evaluate(cands, "営業時間", [], CFG, url_allowed=False)
        self.assertIsNone(chosen)


class KbTest(unittest.TestCase):
    def test_roundtrip_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(kb, "KB_DIR", Path(tmp)):
                e = kb.Entry(kb.unique_path("law", "Stealth Marketing!"), {"title": "t: 題", "category": "law", "published": "2024-01-01",
                                                                          "expires": "", "status": "current"}, "# 本文\n")
                kb.save(e)
                self.assertTrue(e.path.name.endswith("-stealth-marketing.md"))
                loaded = kb.load_all()
                self.assertEqual(loaded[0].meta["title"], "t: 題")
                self.assertEqual(kb.mark_stale(loaded, 365, today=date(2026, 9, 27)), 1)
                self.assertEqual(kb.load_all()[0].meta["status"], "stale")
                kb.write_index(kb.load_all())
                self.assertIn("【古い】", (Path(tmp) / "INDEX.md").read_text(encoding="utf-8"))


class FetchTest(unittest.TestCase):
    RSS1 = """<?xml version="1.0" encoding="utf-8"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns="http://purl.org/rss/1.0/">
      <item rdf:about="https://example.go.jp/a.html"><title>補助金の公募</title><link>https://example.go.jp/a.html</link>
      <dc:date>2026-09-10T10:00:00+09:00</dc:date></item></rdf:RDF>""".encode()

    def test_rss1_items(self):
        with mock.patch.object(fetch, "get", return_value=(self.RSS1, "application/xml", "")):
            items = fetch.feed_items("https://example.go.jp/rss")
        self.assertEqual(items[0]["title"], "補助金の公募")
        self.assertEqual(items[0]["published"].year, 2026)

    def test_page_text_prefers_main(self):
        html = ("<html><head><title>題</title></head><body><nav>メニュー</nav><main><p>" + "本文です。" * 100
                + "</p></main><footer>フッター</footer></body></html>").encode()
        with mock.patch.object(fetch, "get", return_value=(html, "text/html; charset=utf-8", "")):
            title, text = fetch.page_text("https://example.go.jp/")
        self.assertEqual(title, "題")
        self.assertNotIn("メニュー", text)
        self.assertIn("本文です。", text)


class IntakeTest(unittest.TestCase):
    def test_parse_issue_form(self):
        body = "### 店名\n\nパン工房こむぎ\n\n### 業種\n\nパン屋\n\n### 載せたいこと\n\n焼き上がり時間\n定休日\n\n### 雰囲気\n\n_No response_\n\n### 参考URL\n\n_No response_"
        got = intake.parse_issue_body(body)
        self.assertEqual(got["shop_name"], "パン工房こむぎ")
        self.assertEqual(got["wants"], "焼き上がり時間\n定休日")
        self.assertEqual(got["mood"], "")

    def test_closed_issue_drops_pending_request(self):
        requests = {"gh-5": {"id": "gh-5", "source": "issue", "issue": 5, "status": "new", "history": []}}
        with mock.patch.object(intake.github, "available", return_value=True), \
             mock.patch.object(intake.github, "list_issues", return_value=[]):
            self.assertEqual(intake.from_issues(requests, "request"), 0)
        self.assertEqual(requests["gh-5"]["status"], "closed")

    def test_reply_respects_caps_and_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replies.jsonl"
            with mock.patch.object(intake, "REPLIES_PATH", path):
                req = {"id": "x-1", "source": "x", "tweet_id": "1", "username": "shop", "public_url": "https://freehp.jp/mihon/r/a-1/", "history": []}
                self.assertIn("DRY_RUN", intake.reply(req, CFG, dry=True))
                req2 = dict(req, id="x-2", history=[])
                self.assertIn("今日もう返信", intake.reply(req2, CFG, dry=True))


class RebuildTest(unittest.TestCase):
    def test_extract_and_budoux(self):
        raw = "説明\n```html\n<!doctype html><html lang=\"ja\"><body><p>a</p></body></html>\n```"
        html = rebuild.extract_html(raw)
        self.assertTrue(html.startswith("<!doctype html>"))
        out = rebuild.with_budoux(html, "<!-- budoux:start -->X<!-- budoux:end -->")
        self.assertLess(out.index("budoux:start"), out.index("</body>"))

    def test_static_problems(self):
        good = ('<!doctype html><html lang="ja"><head><meta name="viewport" content="width=device-width">'
                '<meta name="robots" content="noindex"></head><body><p class="notice">見本です</p>'
                '<img src="/mihon/parts/img/soda-l.jpg" alt="a"><a href="https://freehp.jp/">freehp.jp</a></body></html>')
        self.assertEqual(sitecheck.static_problems(good, {"/mihon/parts/img/soda-l.jpg"}), [])
        bad = good.replace("</body>", '<script src="https://evil.example/x.js"></script><form></form>000-0000-0000 無料</body>')
        probs = " ".join(sitecheck.static_problems(bad, {"/mihon/parts/img/soda-l.jpg"}))
        for word in ("スクリプト", "フォーム", "仮の値", "禁止語"):
            self.assertIn(word, probs)


class PostTest(unittest.TestCase):
    def test_choose_avoids_recent_types_and_url_slot_forces_offer(self):
        import random
        rows = [{"ts": "2099-01-01T00:00:00+09:00", "mode": "dry_run", "type": t, "material_key": "k", "text": "x"} for t in ("part", "mihon", "photo")]
        with mock.patch.dict(os.environ, {"POST_TYPE": ""}), mock.patch.object(post, "recent_posts", side_effect=lambda r, m, d: r):
            t, _ = post.choose(rows, "dry_run", CFG, url_slot=False, rng=random.Random(1))
            self.assertNotIn(t, ("part", "mihon", "photo", "offer"))
            t, m = post.choose(rows, "dry_run", CFG, url_slot=True, rng=random.Random(1))
            self.assertEqual(t, "offer")
            self.assertTrue(m["url"])


class FailuresTest(unittest.TestCase):
    def test_consecutive_counts_skip_neutral(self):
        runs = [{"id": 9, "status": "in_progress"}, {"id": 8, "status": "completed", "conclusion": "failure"},
                {"id": 7, "status": "completed", "conclusion": "skipped"}, {"id": 6, "status": "completed", "conclusion": "failure"},
                {"id": 5, "status": "completed", "conclusion": "success"}, {"id": 4, "status": "completed", "conclusion": "failure"}]
        with mock.patch.object(failures.github, "workflow_runs", return_value=runs), \
             mock.patch.object(failures.github, "run_url", return_value="https://github.com/o/r/actions/runs/9"):
            self.assertEqual(failures.consecutive_failures("post.yml"), 3)


class MiscTest(unittest.TestCase):
    def test_redact(self):
        self.assertNotIn("abc", redact("https://x-access-token:abc@github.com sk-ant-abc github_pat_abc"))

    def test_report_svg(self):
        svg = report.svg_chart("2026-09-26", [("投稿", 3), ("失敗", 0)])
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("2026-09-26", svg)

    def test_config_shape(self):
        self.assertEqual(CFG["brand"]["x_handle"], "freehp3000")
        self.assertEqual(CFG["post"]["max_url_posts_per_day"], 1)
        self.assertEqual(json.loads((Path(__file__).resolve().parent.parent / "sources.json").read_text(encoding="utf-8"))[0]["kind"], "feed")


if __name__ == "__main__":
    unittest.main()
