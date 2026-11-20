"""Turn Screaming Frog exports into findings.

Re-uses the real checks from sf-automation-local (imported read-only, never modified) and adds the
lab-only `multiple_h1` check, which that project does not have yet.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit


def _sf_audit_root():
    env = os.environ.get("SF_AUDIT_PATH")
    root = Path(env) if env else Path(__file__).resolve().parents[2] / "sf-automation-local"
    if not (root / "sf_audit" / "analyze.py").exists():
        raise SystemExit(f"sf-automation-local not found at {root}. Set SF_AUDIT_PATH.")
    return root


def load_audit():
    """Return (analyze, read_csv, Finding, rules) from sf-automation-local."""
    root = _sf_audit_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from sf_audit.analyze import Finding, analyze, read_csv  # noqa: E402
    rules = json.loads((root / "config" / "rules.json").read_text(encoding="utf-8"))
    return analyze, read_csv, Finding, rules


def path_of(url):
    """'http://127.0.0.1:8765/a.html?x=1' -> '/a.html'."""
    return urlsplit(url).path or "/"


def detect(exports):
    """Return a sorted list of Finding for the exports produced by crawl_lab.crawl()."""
    analyze, read_csv, Finding, rules = load_audit()
    internal = read_csv(exports["internal"])
    chains = read_csv(exports["chains"]) if exports.get("chains") else []
    findings = set(analyze(internal, chains, rules))

    # Lab-only check: more than one H1 on a live HTML page.
    multi = set()
    if exports.get("h1_multiple"):
        multi |= {r["Address"] for r in read_csv(exports["h1_multiple"]) if r.get("Address")}
    for row in internal:
        if (row.get("Status Code") == "200" and "html" in (row.get("Content Type") or "").lower()
                and (row.get("H1-2") or "").strip()):
            multi.add(row["Address"])
    for url in multi:
        findings.add(Finding(url, "multiple_h1", "More than one H1 found"))

    return sorted(findings, key=lambda f: (f.issue_id, f.url))


def as_pairs(findings):
    """Set of (path, issue_id), the form used to compare against expected_findings.json."""
    return {(path_of(f.url), f.issue_id) for f in findings}
