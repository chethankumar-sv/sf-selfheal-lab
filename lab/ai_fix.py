"""AI fixer: a Cursor agent edits the site files in a git worktree, one commit per issue type.

The crawl, detection, re-crawl and assertions stay deterministic (Screaming Frog + detect.py).
The agent only proposes edits. Everything it changes is a commit on the selfheal branch, so a
human reviews it as a normal PR.

Needs:  pip install cursor-sdk   and   export CURSOR_API_KEY=...
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_MODEL = "composer-2.5"
NETWORK_HINT = (
    "The Cursor SDK could not reach Cursor's servers. On networks with a TLS-inspecting proxy "
    "(for example Zscaler), the lab sets NODE_USE_SYSTEM_CA=1 and CURSOR_SDK_USE_HTTP1_FOR_AGENT=1 for you. "
    "If you still see this, check VPN/proxy and run `curl -I https://api2.cursor.sh`."
)


def describe_failure(result):
    """The SDK puts the real cause in error_code / error_message. Show them, or the run's text."""
    code = getattr(result, "error_code", None)
    message = getattr(result, "error_message", None)
    parts = [p for p in (code, message) if p]
    if not parts and (result.result or "").strip():
        parts.append(result.result.strip()[:500])
    return " | ".join(parts) or "no error details returned by the SDK"


_configured = False


def configure_network():
    """The SDK bridge runs a bundled Node that ignores the macOS keychain, so corporate
    TLS-inspection certificates (Zscaler etc.) fail with 'Network request failed'.
    NODE_USE_SYSTEM_CA=1 makes it trust the system certificates. The bridge inherits this env."""
    os.environ.setdefault("NODE_USE_SYSTEM_CA", "1")
    # Agent streams need HTTP/2. Proxies like Zscaler often negotiate HTTP/1.1 instead, which fails with
    # "expected h2 session, but ALPN protocol failed to negotiate". This makes the SDK use HTTP/1.1 for agent streams.
    os.environ.setdefault("CURSOR_SDK_USE_HTTP1_FOR_AGENT", "1")
    global _configured
    if _configured:
        return
    _configured = True
    try:
        from cursor_sdk import Cursor
    except ImportError:
        return  # the callers report a missing SDK with a clear message
    # Explicit form of the same switch. Must run before the first bridge starts, so it is called once, here.
    Cursor.configure(local={"use_http1_for_agent": True})

# Order only affects the order of commits.
ISSUE_ORDER = ["wrong_canonical", "missing_canonical", "missing_h1", "multiple_h1", "noindex",
               "redirect_chain", "status_4xx", "duplicate_title"]

SITE_RULES = """\
Site conventions (a small hand-written static site in site/):
- Canonical links use the literal placeholder {{BASE}} as the origin, e.g.
  <link rel="canonical" href="{{BASE}}/page.html">. Never hard-code a host or port. The dev server
  replaces {{BASE}} at serve time.
- Redirects live in site/redirects.json as {"/from": "/to"}. Each source must point straight at the
  FINAL destination (one hop). Keep every existing key. Do not create loops.
- Every page needs exactly one <h1>, a unique <title>, and a self-referencing canonical.
- Keep edits minimal and in the existing style. Do not reformat or touch anything unrelated.
"""

PROMPT = """\
You are fixing SEO defects that a Screaming Frog crawl found on a static site.
Your working directory is the site's git worktree. Edit files under site/ only.

{rules}
Defect type: {issue}
Findings (URL path -> detail):
{findings}
{extra}
Rules for this task:
- Fix ONLY the findings above. Do not fix other defects you may notice.
- Do not run git commands. Do not create, rename or delete files outside site/.
- If a fix needs a judgement call you cannot justify from the site's content (for example which page
  a dead link should point to), choose the safest option and mention it in a line starting with
  "REVIEW:".
- If you cannot fix a finding safely, leave it alone and say so in a line starting with "SKIPPED:".
- Finish with a short plain-text summary (max 6 lines) of what you changed.
"""


def _path(url):
    return urlsplit(url).path or "/"


def _extra_for(issue, findings, inlinks, config):
    if issue != "status_4xx":
        return ""
    lines = ["Which pages link to each broken URL (the page to edit is the source):"]
    for f in findings:
        for row in inlinks:
            if row.get("Destination") == f.url:
                lines.append(f"- {_path(row['Source'])} links to {_path(f.url)} "
                             f"(anchor text: {row.get('Anchor') or 'n/a'})")
    hints = config.get("link_fixes") or {}
    if hints:
        lines.append("Human-approved replacements (use these): "
                     + ", ".join(f"{k} -> {v}" for k, v in hints.items()))
    else:
        lines.append("No approved replacement is given. Prefer repointing to a clearly equivalent page; "
                     "otherwise remove the link but keep its text.")
    return "\n".join(lines) + "\n"


def _changed_files(git, work):
    out = git("status", "--porcelain", cwd=work)
    return [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]


def run_ai_fixes(findings, work, inlinks, config, approvals, git, round_label="fix"):
    """Fix `findings` in worktree `work`. Returns a list of
    (issue_id, changed_files, summary_text, skipped_reason or None)."""
    configure_network()
    try:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
    except ImportError:
        raise SystemExit("cursor-sdk is not installed. Run: pip install cursor-sdk")
    api_key = os.environ.get("CURSOR_API_KEY")
    if not api_key:
        raise SystemExit("CURSOR_API_KEY is not set. Run: export CURSOR_API_KEY=...")

    by_issue = defaultdict(list)
    for f in findings:
        by_issue[f.issue_id].append(f)
    issues = [i for i in ISSUE_ORDER if i in by_issue] + sorted(set(by_issue) - set(ISSUE_ORDER))

    work = Path(work)
    model = os.environ.get("SELFHEAL_MODEL", DEFAULT_MODEL)
    log = []
    try:
        agent_cm = Agent.create(AgentOptions(
            api_key=api_key, model=model, name="sf-selfheal",
            local=LocalAgentOptions(cwd=str(work))))
    except CursorAgentError as e:
        hint = f"\n{NETWORK_HINT}" if "Network request failed" in str(e) else ""
        raise SystemExit(f"Could not start the Cursor agent: {e}{hint}")

    with agent_cm as agent:
        for issue in issues:
            items = by_issue[issue]
            if issue == "noindex" and "noindex" not in approvals:
                log.append((issue, set(), "", "needs approval 'noindex' (see lab/fix_config.json)"))
                print(f"  [{round_label}] {issue}: SKIPPED, needs human approval")
                continue
            prompt = PROMPT.format(
                rules=SITE_RULES, issue=issue,
                findings="\n".join(f"- {_path(f.url)} -> {f.detail or issue}" for f in items),
                extra=_extra_for(issue, items, inlinks, config))
            print(f"  [{round_label}] {issue}: asking the agent to fix {len(items)} finding(s)...")
            try:
                run = agent.send(prompt)
                print(f"      run {run.id} (agent {agent.agent_id})")
                result = run.wait()
            except CursorAgentError as e:
                log.append((issue, set(), "", f"agent did not start: {e}"))
                print(f"      FAILED to start: {e}", file=sys.stderr)
                continue
            summary = (result.result or "").strip()
            if result.status != "finished":
                _discard(git, work)
                reason = describe_failure(result)
                log.append((issue, set(), summary, f"agent run ended with status '{result.status}': {reason}"))
                print(f"      agent status: {result.status}, changes discarded")
                print(f"      reason: {reason}")
                continue

            # Guardrail: only changes under site/ are kept. Anything else is thrown away.
            changed = _changed_files(git, work)
            kept = [c for c in changed if c.startswith("site/")]
            if len(kept) != len(changed):
                print("      note: discarded edits outside site/")
            if kept:
                git("add", "-A", "site", cwd=work)
                git("commit", "-q", "-m", f"selfheal(ai): {issue}\n\n{summary}", cwd=work)
            _discard(git, work)  # drops whatever is left outside site/
            if not kept:
                log.append((issue, set(), summary, "agent made no changes"))
                print("      no changes made")
                continue
            log.append((issue, {c[len('site/'):] for c in kept}, summary, None))
            print(f"      committed {len(kept)} file(s)")
    return log


def _discard(git, work):
    git("reset", "-q", "--hard", cwd=work)
    git("clean", "-fdq", cwd=work)
