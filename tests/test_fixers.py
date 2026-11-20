"""Fast tests (no Screaming Frog, no network beyond localhost). Run: python3 -m unittest discover -s tests -v"""
import json
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab.detect import load_audit  # noqa: E402
from lab.fixers import FIXERS, Context  # noqa: E402
from lab.fixers.base import get_h1, get_title  # noqa: E402
from lab.fixers.canonical import CanonicalFixer  # noqa: E402
from lab.fixers.h1 import H1Fixer  # noqa: E402
from lab.fixers.links404 import Broken4xxFixer  # noqa: E402
from lab.fixers.noindex import NoindexFixer  # noqa: E402
from lab.fixers.redirects import RedirectChainFixer  # noqa: E402
from lab.fixers.titles import DuplicateTitleFixer  # noqa: E402
from server import start_server  # noqa: E402

BASE = "http://test.local"
Finding = load_audit()[2]


def finding(path, issue, detail=""):
    return Finding(BASE + path, issue, detail)


class FixerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = self.tmp / "site"
        shutil.copytree(ROOT / "site", self.site)
        self.ctx = Context(site_dir=self.site, config={"link_fixes": {"/gone.html": "/final.html"}})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def text(self, name):
        return (self.site / name).read_text(encoding="utf-8")

    def test_canonical_added_and_corrected(self):
        res = CanonicalFixer().apply(self.ctx, [finding("/no-canonical.html", "missing_canonical"),
                                                finding("/wrong-canonical.html", "wrong_canonical")])
        self.assertEqual(res.skipped, [])
        self.assertIn('<link rel="canonical" href="{{BASE}}/no-canonical.html">', self.text("no-canonical.html"))
        self.assertIn('href="{{BASE}}/wrong-canonical.html"', self.text("wrong-canonical.html"))
        self.assertEqual(self.text("wrong-canonical.html").lower().count('rel="canonical"'), 1)

    def test_missing_h1_added_from_title(self):
        H1Fixer().apply(self.ctx, [finding("/no-h1.html", "missing_h1")])
        self.assertEqual(get_h1(self.text("no-h1.html")), "Home Loan Eligibility Guide")

    def test_extra_h1_demoted(self):
        H1Fixer().apply(self.ctx, [finding("/two-h1.html", "multiple_h1")])
        text = self.text("two-h1.html")
        self.assertEqual(text.count("<h1"), 1)
        self.assertIn("<h2>Check Your Monthly EMI</h2>", text)

    def test_noindex_removed_but_other_directives_kept(self):
        NoindexFixer().apply(self.ctx, [finding("/hidden.html", "noindex")])
        self.assertNotIn('name="robots"', self.text("hidden.html"))
        page = self.site / "hidden.html"
        page.write_text(page.read_text().replace('<link rel="canonical"', '<meta name="robots" content="noindex, nofollow">\n  <link rel="canonical"'))
        NoindexFixer().apply(self.ctx, [finding("/hidden.html", "noindex")])
        self.assertIn('content="nofollow"', self.text("hidden.html"))

    def test_noindex_requires_approval(self):
        self.assertEqual(NoindexFixer().approval, "noindex")

    def test_redirect_chain_collapsed_to_one_hop(self):
        RedirectChainFixer().apply(self.ctx, [finding("/old-a", "redirect_chain")])
        rules = json.loads(self.text("redirects.json"))
        self.assertEqual(set(rules.values()), {"/final.html"})

    def test_redirect_loop_is_left_for_a_human(self):
        (self.site / "redirects.json").write_text(json.dumps({"/a": "/b", "/b": "/a"}))
        res = RedirectChainFixer().apply(self.ctx, [finding("/a", "redirect_chain")])
        self.assertTrue(res.skipped)
        self.assertEqual(json.loads(self.text("redirects.json")), {"/a": "/b", "/b": "/a"})

    def test_broken_links_repointed_or_unwrapped(self):
        self.ctx.inlinks = [{"Source": BASE + "/links.html", "Destination": BASE + "/gone.html"},
                            {"Source": BASE + "/links.html", "Destination": BASE + "/missing/page.html"}]
        Broken4xxFixer().apply(self.ctx, [finding("/gone.html", "status_4xx"), finding("/missing/page.html", "status_4xx")])
        text = self.text("links.html")
        self.assertIn('<a href="/final.html">old loan guide</a>', text)
        self.assertNotIn("missing/page.html", text)
        self.assertIn("archived rates page", text)

    def test_broken_url_without_inlinks_is_skipped(self):
        res = Broken4xxFixer().apply(self.ctx, [finding("/gone.html", "status_4xx")])
        self.assertTrue(res.skipped)

    def test_duplicate_titles_made_unique(self):
        DuplicateTitleFixer().apply(self.ctx, [finding("/dup-1.html", "duplicate_title"), finding("/dup-2.html", "duplicate_title")])
        self.assertNotEqual(get_title(self.text("dup-1.html")), get_title(self.text("dup-2.html")))

    def test_every_fixer_is_idempotent(self):
        items = [finding("/no-canonical.html", "missing_canonical"), finding("/no-h1.html", "missing_h1"),
                 finding("/two-h1.html", "multiple_h1"), finding("/hidden.html", "noindex"),
                 finding("/old-a", "redirect_chain"), finding("/dup-1.html", "duplicate_title"),
                 finding("/dup-2.html", "duplicate_title")]
        for fixer in FIXERS:
            fixer.apply(self.ctx, [f for f in items if f.issue_id in fixer.issue_ids])
        snapshot = {p.name: p.read_text() for p in self.site.iterdir() if p.is_file()}
        for fixer in FIXERS:
            res = fixer.apply(self.ctx, [f for f in items if f.issue_id in fixer.issue_ids])
            self.assertEqual(res.changed, set(), fixer.name)
        self.assertEqual(snapshot, {p.name: p.read_text() for p in self.site.iterdir() if p.is_file()})


class ServerTests(unittest.TestCase):
    def test_redirects_404_and_base_substitution(self):
        httpd, base = start_server(ROOT / "site")
        self.addCleanup(httpd.shutdown)

        class NoFollow(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoFollow)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(base + "/old-a")
        self.assertEqual((cm.exception.code, cm.exception.headers["Location"]), (301, base + "/old-b"))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            opener.open(base + "/gone.html")
        self.assertEqual(cm.exception.code, 404)
        body = urllib.request.urlopen(base + "/").read().decode()
        self.assertIn(f'href="{base}/"', body)
        self.assertNotIn("{{BASE}}", body)


if __name__ == "__main__":
    unittest.main()
