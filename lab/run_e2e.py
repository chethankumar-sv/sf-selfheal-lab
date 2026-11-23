#!/usr/bin/env python3
"""End-to-end self-healing test.

  1. serve the deliberately broken site (baseline = git branch `main`)
  2. crawl it with Screaming Frog
  3. ASSERT the detected issues equal expected_findings.json (nothing missed, nothing extra)
  4. create a git worktree + branch `selfheal/<run-id>` and run every fixer (one commit + one .patch each)
  5. serve the fixed copy and crawl it again
  6. ASSERT no issues remain

Outputs go to runs/<run-id>/: patches/*.patch, PR.md, suggestions.md, results.json and both crawls.
The branch `selfheal/<run-id>` is the "PR". `main` is never modified, so the baseline stays broken.

Usage:  python3 lab/run_e2e.py          (exit code 0 = every assertion passed)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab.crawl_lab import crawl  # noqa: E402
from lab.detect import as_pairs, detect, load_audit, path_of  # noqa: E402
from lab.fixers import FIXERS, Context, fixer_for  # noqa: E402
from server import start_server  # noqa: E402


def git(*args, cwd=ROOT):
    cmd = ["git", "-c", "user.name=selfheal-bot", "-c", "user.email=selfheal-bot@example.invalid", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{proc.stderr or proc.stdout}")
    return proc.stdout


def load_expected():
    data = json.loads((ROOT / "expected_findings.json").read_text(encoding="utf-8"))
    return {(item["path"], item["issue"]) for item in data}


def crawl_and_detect(site_dir, out_dir, label):
    httpd, base = start_server(site_dir)
    try:
        print(f"[{label}] serving {site_dir} at {base}, crawling with Screaming Frog...")
        exports = crawl(base + "/", out_dir)
        findings = detect(exports)
    finally:
        httpd.shutdown()
    return findings, exports


def show(title, rows):
    print(f"\n{title}")
    for row in rows:
        print("  " + row)


AI_BASE_BRANCH = "demo-base"


def ensure_base_branch(name, push=True):
    """demo-base = a copy of main that PRs target, so main itself stays broken for demos."""
    if not git("branch", "--list", name).strip():
        git("branch", name, "main")
        print(f"Created local branch {name} from main")
    if not push:
        return
    remote = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin", name],
                            cwd=ROOT, capture_output=True, text=True)
    if remote.returncode != 0:
        push = subprocess.run(["git", "push", "origin", name], cwd=ROOT, capture_output=True, text=True)
        if push.returncode != 0:
            sys.exit(f"Could not push {name} to origin (needed as the PR base):\n{push.stderr}")
        print(f"Pushed {name} to origin")


def preflight_ai(args):
    """Fail fast, before any crawl or branch, if the AI/PR prerequisites are missing."""
    from lab.ai_fix import NETWORK_HINT, configure_network

    configure_network()
    problems = []
    try:
        from cursor_sdk import Cursor, CursorAgentError
    except ImportError:
        sys.exit("cursor-sdk is not installed. Run: .venv/bin/pip install -r requirements.txt, "
                 "then start with .venv/bin/python")
    if not os.environ.get("CURSOR_API_KEY"):
        problems.append("CURSOR_API_KEY is not set (export CURSOR_API_KEY=...)")
    else:
        # Read-only call: checks the key and that Cursor is reachable, before anything is created.
        try:
            Cursor.models.list()
        except CursorAgentError as e:
            problems.append(f"Cursor API check failed: {e}"
                            + (f"\n    {NETWORK_HINT}" if "Network request failed" in str(e) else ""))
    if not args.no_pr:
        gh = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        if gh.returncode != 0:
            problems.append("gh is not logged in (gh auth login), needed to open the PR")
    if problems:
        sys.exit("Cannot start the AI run:\n  - " + "\n  - ".join(problems))


def split_summary(summary):
    notes, review, skipped = [], [], []
    for line in (summary or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("REVIEW:"):
            review.append(line[7:].strip())
        elif line.upper().startswith("SKIPPED:"):
            skipped.append(line[8:].strip())
        else:
            notes.append(line)
    return notes, review, skipped


def open_pr(branch, base, body_file, title):
    """Push the branch and open a PR into `base`. Returns a URL (PR or compare link) and a status line."""
    push = subprocess.run(["git", "push", "-u", "origin", branch], cwd=ROOT, capture_output=True, text=True)
    if push.returncode != 0:
        return None, f"push failed: {(push.stderr or push.stdout).strip()}"
    pr = subprocess.run(["gh", "pr", "create", "--base", base, "--head", branch, "--title", title,
                         "--body-file", str(body_file)], cwd=ROOT, capture_output=True, text=True)
    if pr.returncode == 0:
        return pr.stdout.strip().splitlines()[-1], "PR created"
    remote = git("remote", "get-url", "origin").strip().removesuffix(".git")
    return f"{remote}/compare/{base}...{branch}?expand=1", f"gh could not create the PR ({(pr.stderr or '').strip()}); open the link"


def run_ai(args, findings, exports, expected, found, detection_ok, config, run_dir, run_id, branch):
    from lab.ai_fix import run_ai_fixes

    _, read_csv, _, _ = load_audit()
    ensure_base_branch(args.base_ref, push=not args.no_pr)
    work = run_dir / "work"
    git("worktree", "add", "-q", "-b", branch, str(work), args.base_ref)
    approvals = set(config.get("approve", []))
    inlinks = read_csv(exports["inlinks_4xx"]) if exports.get("inlinks_4xx") else []

    print("\nAI FIX  (Cursor agent edits the worktree; crawl + assertions stay deterministic)")
    ai_log = run_ai_fixes(findings, work, inlinks, config, approvals, git, "round 1")

    after_findings, after_exports = crawl_and_detect(work / "site", run_dir / "after", "after fix")
    for attempt in range(args.retries):
        if not after_findings:
            break
        print(f"\n{len(after_findings)} issue(s) remain, retrying ({attempt + 1}/{args.retries})")
        inl = read_csv(after_exports["inlinks_4xx"]) if after_exports.get("inlinks_4xx") else []
        ai_log += run_ai_fixes(after_findings, work, inl, config, approvals, git, f"retry {attempt + 1}")
        after_findings, after_exports = crawl_and_detect(work / "site", run_dir / "after", "after retry")

    remaining = sorted(as_pairs(after_findings))
    fix_ok = not remaining
    show("RE-CRAWL  (was the issue fixed?)",
         [f"{'STILL BROKEN' if pair in remaining else 'FIXED       '}  {pair[1]:<18} {pair[0]}"
          for pair in sorted(expected, key=lambda p: (p[1], p[0]))])
    new_issues = [pair for pair in remaining if pair not in expected]
    if new_issues:
        show("NEW issues introduced by the fixes", [f"{i:<18} {p}" for p, i in new_issues])
    print(f"\nFix assertion: {'PASS' if fix_ok else 'FAIL'}")

    # ---- PR body -------------------------------------------------------------------------------
    review_all, pr = [], [f"# Self-heal (AI): fix {len(expected)} SEO issues found by the crawl", "",
                          f"Branch: `{branch}` (base: `{args.base_ref}`). Fixes written by a Cursor agent, "
                          "verified by a Screaming Frog re-crawl.", "", "## Changes", ""]
    for issue, changed, summary, skip in ai_log:
        notes, review, skipped = split_summary(summary)
        review_all += [f"`{issue}`: {r}" for r in review]
        pr.append(f"### {issue}")
        pr += [f"- {n}" for n in notes]
        if changed:
            pr.append(f"- files: {', '.join(sorted(changed))}")
        pr += [f"- NEEDS A HUMAN: {s}" for s in skipped] + ([f"- NEEDS A HUMAN: {skip}"] if skip else []) + [""]
    if review_all:
        pr += ["## Please review (AI judgement calls)", ""] + [f"- {r}" for r in review_all] + [""]
    pr += ["## Verification", "",
           f"- Baseline crawl: {len(found)} issues, matching expected_findings.json: {'yes' if detection_ok else 'NO'}",
           f"- Re-crawl after fixes: {len(remaining)} issues remaining", ""]
    pr_file = run_dir / "PR.md"
    pr_file.write_text("\n".join(pr), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({
        "run_id": run_id, "branch": branch, "mode": "ai", "detection_ok": detection_ok, "fix_ok": fix_ok,
        "expected": sorted(expected), "remaining": remaining}, indent=2), encoding="utf-8")

    print(f"\nBranch:  {branch}  (git log {args.base_ref}..{branch})\nPR body: {pr_file}")
    if args.no_pr:
        print("--no-pr set: not pushing.")
    elif not (detection_ok and fix_ok):
        print("Not opening a PR because an assertion failed. Inspect the branch and retry.")
    else:
        url, status = open_pr(branch, args.base_ref, pr_file, f"Self-heal: fix {len(expected)} SEO issues (AI)")
        print(f"\n{status}\nPR (needs human approval, base {args.base_ref}): {url}" if url else f"\n{status}")
    ok = detection_ok and fix_ok
    print("\nEND-TO-END: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default=None,
                        help="git ref the fix branch starts from (default: main, or demo-base with --ai)")
    parser.add_argument("--detect-only", action="store_true", help="stop after the detection assertion")
    parser.add_argument("--ai", action="store_true",
                        help="let a Cursor agent write the fixes, then push the branch and open a PR into the base branch")
    parser.add_argument("--retries", type=int, default=1, help="extra AI rounds if issues remain (default: 1)")
    parser.add_argument("--no-pr", action="store_true", help="with --ai: stop after the local branch (no push, no PR)")
    args = parser.parse_args()
    if args.base_ref is None:
        args.base_ref = AI_BASE_BRANCH if args.ai else "main"
    if args.ai and args.base_ref == "main":
        sys.exit("Refusing to open an AI PR into main (kept broken for demos). Use the default demo-base.")

    if args.ai:
        preflight_ai(args)
    if git("status", "--porcelain", "--", "site").strip():
        sys.exit("site/ has uncommitted changes. Commit or revert them so the baseline is known.")
    _, read_csv, _, _ = load_audit()
    config = json.loads((ROOT / "lab" / "fix_config.json").read_text(encoding="utf-8"))

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True)
    print(f"Run {run_id}  ->  {run_dir}")

    # ---- 1-3. baseline crawl and detection assertion ------------------------------------------
    expected = load_expected()
    findings, exports = crawl_and_detect(ROOT / "site", run_dir / "baseline", "baseline")
    found = as_pairs(findings)
    missing, extra = sorted(expected - found), sorted(found - expected)
    detection_ok = not missing and not extra

    show("DETECTION  (expected issue -> found by the crawler?)",
         [f"{'FOUND ' if pair in found else 'MISSED'}  {pair[1]:<18} {pair[0]}" for pair in sorted(expected, key=lambda p: (p[1], p[0]))])
    if extra:
        show("UNEXPECTED findings (false positives or a baseline page that is not clean)",
             [f"{issue:<18} {path}" for path, issue in extra])
    print(f"\nDetection assertion: {'PASS' if detection_ok else 'FAIL'}")

    suggestions = []
    for f in findings:
        fx = fixer_for(f.issue_id)
        if fx:
            suggestions.append(f"### {f.issue_id}: {path_of(f.url)}\n\n```\n{fx.suggest(f)}\n```\n")
    (run_dir / "suggestions.md").write_text(
        "# Suggestions for teams without code access\n\n" + "\n".join(suggestions), encoding="utf-8")

    if args.detect_only or not detection_ok:
        if not detection_ok:
            print("Stopping: fix the detection gap before testing fixers.")
        return 0 if detection_ok else 1

    # ---- 4. run the fixers in a git worktree on a new branch ----------------------------------
    branch = f"selfheal/{run_id}"
    if args.ai:
        return run_ai(args, findings, exports, expected, found, detection_ok, config, run_dir, run_id, branch)
    work = run_dir / "work"
    git("worktree", "add", "-q", "-b", branch, str(work), args.base_ref)
    patches = run_dir / "patches"
    patches.mkdir()
    ctx = Context(site_dir=work / "site",
                  inlinks=read_csv(exports["inlinks_4xx"]) if exports.get("inlinks_4xx") else [],
                  config=config, approvals=set(config.get("approve", [])))

    by_issue = defaultdict(list)
    for f in findings:
        by_issue[f.issue_id].append(f)

    log = []  # (fixer name, issue ids, changed files, notes, skipped)
    for index, fixer in enumerate(FIXERS, start=1):
        items = [f for f in findings if f.issue_id in fixer.issue_ids]
        if not items:
            continue
        if fixer.approval and fixer.approval not in ctx.approvals:
            log.append((fixer.name, fixer.issue_ids, set(), [], [f"needs approval '{fixer.approval}' (see lab/fix_config.json)"]))
            continue
        result = fixer.apply(ctx, items)
        diff = git("diff", cwd=work)
        if diff.strip():
            (patches / f"{index:02d}-{fixer.name}.patch").write_text(diff, encoding="utf-8")
            git("add", "-A", cwd=work)
            git("commit", "-q", "-m", f"selfheal: {fixer.name} ({', '.join(fixer.issue_ids)})\n\n" + "\n".join(result.notes), cwd=work)
        log.append((fixer.name, fixer.issue_ids, result.changed, result.notes, result.skipped))

    show("FIXERS", [f"{name:<10} {len(changed)} file(s) changed" + "".join(f"\n      - {n}" for n in notes)
                    + "".join(f"\n      ! SKIPPED: {s}" for s in skipped)
                    for name, _, changed, notes, skipped in log])

    # ---- 5-6. re-crawl the fixed copy and assert it is clean ----------------------------------
    after_findings, _ = crawl_and_detect(work / "site", run_dir / "after", "after fix")
    remaining = sorted(as_pairs(after_findings))
    fix_ok = not remaining
    show("RE-CRAWL  (was the issue fixed?)",
         [f"{'STILL BROKEN' if pair in remaining else 'FIXED       '}  {pair[1]:<18} {pair[0]}"
          for pair in sorted(expected, key=lambda p: (p[1], p[0]))])
    new_issues = [pair for pair in remaining if pair not in expected]
    if new_issues:
        show("NEW issues introduced by the fixes", [f"{i:<18} {p}" for p, i in new_issues])
    print(f"\nFix assertion: {'PASS' if fix_ok else 'FAIL'}")

    # ---- reports ------------------------------------------------------------------------------
    pr_lines = [f"# Self-heal: fix {len(expected)} SEO issues found by the crawl", "",
                f"Branch: `{branch}` (base: `{args.base_ref}`)", "", "## Changes", ""]
    for name, issue_ids, changed, notes, skipped in log:
        pr_lines.append(f"### {name}  ({', '.join(issue_ids)})")
        pr_lines += [f"- {n}" for n in notes] + [f"- NEEDS A HUMAN: {s}" for s in skipped] + [""]
    pr_lines += ["## Verification", "",
                 f"- Baseline crawl: {len(found)} issues, matching expected_findings.json: {'yes' if detection_ok else 'NO'}",
                 f"- Re-crawl after fixes: {len(remaining)} issues remaining", ""]
    (run_dir / "PR.md").write_text("\n".join(pr_lines), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({
        "run_id": run_id, "branch": branch, "detection_ok": detection_ok, "fix_ok": fix_ok,
        "expected": sorted(expected), "remaining": remaining}, indent=2), encoding="utf-8")

    print(f"\nPatches: {patches}\nPR body: {run_dir / 'PR.md'}\nBranch:  {branch}  (git log {branch})")
    ok = detection_ok and fix_ok
    print("\nEND-TO-END: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
