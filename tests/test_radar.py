import email
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

from radar import scoring
from radar.cli import gather_fixtures, main
from radar.sources import ats, linkedin
from radar.state import State

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())


class Parsers(unittest.TestCase):
    def test_greenhouse_unescapes_html(self):
        jobs = ats.parse_greenhouse(json.loads((FX / "greenhouse.json").read_text()), "X")
        self.assertEqual(len(jobs), 3)
        self.assertIn("OSDU", jobs[0].description)
        self.assertNotIn("<p>", jobs[0].description)

    def test_workday_endpoint(self):
        ep, base = ats.workday_endpoint("https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers")
        self.assertEqual(ep, "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs")
        self.assertEqual(base, "https://acme.wd5.myworkdayjobs.com/AcmeCareers")

    def test_linkedin_guest_canonical_url(self):
        jobs = linkedin.parse_guest_html((FX / "linkedin_guest.html").read_text())
        self.assertEqual(jobs[0].url, "https://www.linkedin.com/jobs/view/4012345678/")
        self.assertEqual(jobs[0].company, "ADNOC Group")

    def test_linkedin_alert_email(self):
        msg = email.message_from_string((FX / "linkedin_alert.eml").read_text())
        jobs = linkedin.parse_alert_email(msg)
        self.assertEqual([j.title for j in jobs],
                         ["Director, Software Engineering", "Engineering Manager, Data Platform"])
        self.assertEqual(jobs[0].company, "Aramco Digital")
        self.assertIn("Riyadh", jobs[0].location)


class LinkedInBudget(unittest.TestCase):
    def test_cap_persists_and_resets(self):
        state = {}
        b = linkedin.DailyBudget(state, 3)
        self.assertEqual([b.spend() for _ in range(5)], [True, True, True, False, False])
        b2 = linkedin.DailyBudget(state, 3)          # a manual re-run the same day
        self.assertFalse(b2.spend())
        state["linkedin_requests"]["date"] = "2000-01-01"
        self.assertTrue(linkedin.DailyBudget(state, 3).spend())   # new day

    def test_public_jobs_never_exceeds_budget(self):
        calls = []

        class FakeHTTP:
            def get(self, url, params=None, timeout=None):
                calls.append(params)
                return type("R", (), {"status_code": 200, "text": ""})()

        b = linkedin.DailyBudget({}, 3)
        _, notes = linkedin.public_jobs([{"keywords": str(i)} for i in range(10)], b,
                                        http=FakeHTTP(), pause=(0, 0))
        self.assertEqual(len(calls), 3)
        self.assertTrue(any("cap" in n for n in notes))

    def test_stops_when_throttled(self):
        calls = []

        class Throttled:
            def get(self, url, params=None, timeout=None):
                calls.append(1)
                return type("R", (), {"status_code": 429, "text": ""})()

        linkedin.public_jobs([{"keywords": "a"}, {"keywords": "b"}],
                             linkedin.DailyBudget({}, 3), http=Throttled(), pause=(0, 0))
        self.assertEqual(len(calls), 1)

    def test_rotation_covers_pool(self):
        pool = list(range(14))
        firsts = {linkedin.rotate(pool, 3, date(2026, 10, d))[0] for d in range(1, 15)}
        self.assertGreaterEqual(len(firsts), 10)


class Filtering(unittest.TestCase):
    def setUp(self):
        self.jobs, self.posts, _ = gather_fixtures(FX)
        self.kept = scoring.prefilter_jobs(self.jobs, CFG["filters"], 50)
        self.titles = {j.title for j in self.kept}

    def test_keeps_relevant(self):
        for t in ["Director of Engineering, Subsurface Data Platform",
                  "Principal Architect - Industrial Data Platform",
                  "Engineering Manager - Digital Solutions",
                  "Head of Engineering (AI Platform)", "Head of Engineering - Energy Data"]:
            self.assertIn(t, self.titles)

    def test_drops_irrelevant(self):
        for t in ["Senior Recruiter", "Junior Software Engineer", "Drilling Engineering Manager",
                  "Sales Director", "VP Engineering", "Engineering Manager, Growth"]:
            self.assertNotIn(t, self.titles)

    def test_domain_role_ranks_first(self):
        self.assertIn(self.kept[0].title, {"Director of Engineering, Subsurface Data Platform",
                                           "Head of Engineering - Energy Data"})

    def test_posts_prefilter(self):
        kept = scoring.prefilter_posts(self.posts, CFG["engage"]["topic_terms"], 10)
        titles = [p.title for p in kept]
        self.assertNotIn("Show HN: My cat photo app", titles)
        self.assertIn("Support Keycloak token exchange for tool calls", titles)


class JsonExtraction(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(scoring.extract_json('Here:\n```json\n[{"id":"a","score":80}]\n```'),
                         [{"id": "a", "score": 80}])


class EndToEnd(unittest.TestCase):
    def test_fixture_run_writes_digest_and_hides_seen(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "digest.html"
            rc = main(["--config", str(ROOT / "config.yaml"), "--fixtures", str(FX),
                       "--state", str(Path(d) / "s.json"), "--out", str(out), "--no-email"])
            self.assertEqual(rc, 0)
            html = out.read_text()
            self.assertIn("Head of Engineering - Energy Data", html)
            self.assertIn("linkedin.com/search/results/content", html)

    def test_state_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            jobs, _, _ = gather_fixtures(FX)
            s = State(str(Path(d) / "s.json"))
            s.mark(jobs[:2], "seen_jobs")
            s.save()
            s2 = State(str(Path(d) / "s.json"))
            self.assertEqual(len(s2.unseen(jobs, "seen_jobs")), len(jobs) - 2)


if __name__ == "__main__":
    unittest.main()
