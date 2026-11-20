"""redirect_chain -> point every hop straight at the final URL (edits site/redirects.json)."""
from __future__ import annotations

import json

from .base import FixError, Fixer, FixResult


def resolve(redirects, start):
    """Follow `start` through the map. Returns (hops_in_order, final) or raises on a loop."""
    chain, current = [start], start
    while current in redirects:
        current = redirects[current]
        if current in chain:
            raise FixError(f"redirect loop {' -> '.join(chain + [current])}: pick the destination by hand")
        chain.append(current)
    return chain, current


class RedirectChainFixer(Fixer):
    name = "redirects"
    issue_ids = ("redirect_chain",)

    def apply(self, ctx, findings):
        result = FixResult()
        rules_file = ctx.site_dir / "redirects.json"
        if not rules_file.exists():
            result.skipped.append("redirect_chain: site/redirects.json not found")
            return result
        redirects = json.loads(rules_file.read_text(encoding="utf-8"))
        original = dict(redirects)

        for f in findings:
            path = ctx.path_of(f.url)
            try:
                chain, final = resolve(redirects, path)
            except FixError as e:
                result.skipped.append(f"redirect_chain {path}: {e}")
                continue
            for hop in chain[:-1]:
                redirects[hop] = final
            result.notes.append(f"redirect_chain: {' -> '.join(chain)}  now  {path} -> {final} (one hop)")

        if redirects != original:
            rules_file.write_text(json.dumps(redirects, indent=2) + "\n", encoding="utf-8")
            result.changed.add("redirects.json")
        return result

    def suggest(self, finding):
        return (f"Change the redirect rule for {finding.url} so it goes straight to the final URL in one 301 hop "
                f"({finding.detail}). Also update internal links to use the final URL.")
