"""Crawl a URL with Screaming Frog (headless) and export everything the lab needs.

Besides the two exports that sf-automation-local uses, the lab also exports:
  * H1:Multiple                 -> pages with more than one H1
  * Client Error (4xx) Inlinks  -> which page links to each broken URL (so a fixer knows which file to edit)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

DEFAULT_SF = "/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher"

EXPORT_FILES = {
    "internal": "internal_all.csv",
    "chains": "redirect_chains.csv",
    "h1_multiple": "h1_multiple.csv",
    "inlinks_4xx": "client_error_(4xx)_inlinks.csv",
}


def crawl(url, out_dir, timeout=600):
    """Crawl `url`, write CSVs into out_dir and return {name: Path or None}."""
    sf = os.environ.get("SF_PATH") or DEFAULT_SF
    if not os.path.exists(sf):
        raise SystemExit(f"Screaming Frog not found at {sf}. Set SF_PATH.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sf, "--headless", "--crawl", url,
        "--output-folder", str(out_dir), "--overwrite",
        "--export-tabs", "Internal:All,H1:Multiple",
        "--bulk-export", "Response Codes:Client Error (4xx) Inlinks",
        "--save-report", "Redirects:Redirect Chains",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"Crawl of {url} did not finish within {timeout}s.")
    (out_dir / "sf.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")

    exports = {name: (out_dir / fname if (out_dir / fname).exists() else None)
               for name, fname in EXPORT_FILES.items()}
    if not exports["internal"]:
        raise SystemExit(f"Crawl produced no internal_all.csv. See {out_dir / 'sf.log'}.")
    return exports
