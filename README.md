# sf-selfheal-lab

A deliberately broken static site plus an end-to-end test for a "self-healing" SEO bot:
crawl, detect, fix (as git commits and patches), re-crawl, assert clean.

It imports the checks from `../sf-automation-local` read-only (set `SF_AUDIT_PATH` to move it) and never changes that project.

```bash
python3 lab/run_e2e.py                              # full loop, exit code 0 = all assertions passed
python3 lab/run_e2e.py --detect-only                # crawl + detection assertion only
python3 -m unittest discover -s tests -t . -v       # fast fixer + server tests (no Screaming Frog)
python3 server.py --port 8765                       # browse the broken site at http://127.0.0.1:8765/
```

Needs Screaming Frog SEO Spider installed (same as sf-automation-local). Python stdlib only.

## AI mode (Cursor agent writes the fixes, opens a PR for human approval)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
export CURSOR_API_KEY=...                                            # never commit this
gh auth login                                                        # once, for the PR
.venv/bin/python lab/run_e2e.py --ai                                 # prints the PR link at the end
```

Flow: crawl, assert detection, worktree branch `selfheal/<run-id>` from `demo-base`, the agent fixes each
issue type (one commit each, edits outside `site/` are discarded), re-crawl and assert clean (one retry round),
then push and open a PR into `demo-base`. `main` stays broken for demos. `noindex` is still gated by
`lab/fix_config.json`. Flags: `--no-pr` (local branch only), `--retries N`, `SELFHEAL_MODEL` env var to change the model.

Troubleshooting (corporate proxy such as Zscaler):

- Run `.venv/bin/python lab/ai_probe.py` first. It checks the key, lists models and runs one trivial agent prompt.
- The lab sets `NODE_USE_SYSTEM_CA=1` so the SDK trusts the macOS keychain (fixes `Network request failed`).
- It sets `CURSOR_SDK_USE_HTTP1_FOR_AGENT=1` because proxies often downgrade HTTP/2 (fixes
`expected h2 session, but ALPN protocol failed to negotiate`).
- If the network still fails, check VPN/proxy and run `curl -I https://api2.cursor.sh`.



## What the site contains (`site/`, branch `main` is always the broken baseline)


| Page                                                | Issue                      | Fixer                                                           |
| --------------------------------------------------- | -------------------------- | --------------------------------------------------------------- |
| `/no-h1.html`                                       | missing H1                 | `h1`: adds an H1 from the title                                 |
| `/two-h1.html`                                      | two H1s                    | `h1`: demotes extra H1s to H2                                   |
| `/wrong-canonical.html`                             | canonical points elsewhere | `canonical`: sets a self canonical                              |
| `/no-canonical.html`                                | missing canonical          | `canonical`: adds it                                            |
| `/hidden.html`                                      | noindex                    | `noindex`: removes it (needs approval in `lab/fix_config.json`) |
| `/old-a` -> `/old-b` -> `/old-c` -> `/final.html`   | 3-hop redirect chain       | `redirects`: collapses to one hop in `site/redirects.json`      |
| `/dup-1.html`, `/dup-2.html`                        | duplicate titles           | `titles`: makes them unique from the H1                         |
| `/links.html` -> `/gone.html`, `/missing/page.html` | 404 links                  | `links404`: repoints or unwraps the links on the source page    |


`expected_findings.json` is the answer key. The run fails if the crawl misses one of these or reports anything else.

## What a run does

1. Serves `site/` with `server.py` (stdlib; `http.server` alone cannot send redirects) and crawls it.
2. Asserts the findings equal `expected_findings.json`.
3. Creates a git worktree on a new branch `selfheal/<run-id>` and runs each fixer: one commit and one `.patch` per fixer. `main` is not touched.
4. Serves the fixed copy, crawls again, asserts there are no findings.

Outputs in `runs/<run-id>/`: `patches/`, `PR.md`, `suggestions.md` (advice for teams without code access), `results.json`, and both crawls.
Open a real PR from the branch with `git push origin selfheal/<run-id>` and `gh pr create --body-file runs/<run-id>/PR.md`.

## Notes

- Canonicals use `{{BASE}}`, which `server.py` replaces with the real origin at serve time, so tests do not depend on a port.
- The 404 fixer needs the "Client Error (4xx) Inlinks" export to know which page to edit. The lab crawler adds it.
- `multiple_h1` exists only here. `sf-automation-local` has no such check yet.
- The `noindex` fixer is gated because noindex is often intentional. Fixers skip, and report, anything they cannot change safely (redirect loops, 404s nobody links to, X-Robots-Tag headers).

