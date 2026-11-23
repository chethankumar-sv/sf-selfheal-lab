"""Quick check that the Cursor agent works, before a full AI run.

  .venv/bin/python lab/ai_probe.py                 # uses composer-2.5
  SELFHEAL_MODEL=auto .venv/bin/python lab/ai_probe.py

Lists models, then runs one trivial prompt in an empty folder and prints the real status and error
fields from the SDK. It edits nothing in the repo.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab.ai_fix import DEFAULT_MODEL, configure_network  # noqa: E402


def main():
    configure_network()
    if not os.environ.get("CURSOR_API_KEY"):
        sys.exit("CURSOR_API_KEY is not set. Run: export CURSOR_API_KEY=...")
    from cursor_sdk import Agent, AgentOptions, Cursor, LocalAgentOptions

    model = os.environ.get("SELFHEAL_MODEL", DEFAULT_MODEL)
    print(f"1. Model list (wanted: {model})")
    try:
        ids = [getattr(m, "id", m) for m in Cursor.models.list()]
        print(f"   {len(ids)} models, e.g. {ids[:8]}")
        print(f"   '{model}' listed: {model in ids}")
    except Exception as e:  # noqa: BLE001 - we want to show whatever happened
        print(f"   FAILED: {type(e).__name__}: {e}")

    print("2. One trivial agent run in an empty folder")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = Agent.prompt(
                "Reply with exactly the word OK and do nothing else.",
                AgentOptions(api_key=os.environ["CURSOR_API_KEY"], model=model,
                             local=LocalAgentOptions(cwd=tmp)),
            )
    except Exception as e:  # noqa: BLE001 - the run never started; show why
        print(f"   FAILED TO START: {type(e).__name__}: {e}")
        print("PROBE: FAILED, the agent did not start (see message above)")
        return 1
    print(f"   status:        {result.status}")
    print(f"   error_code:    {getattr(result, 'error_code', None)}")
    print(f"   error_message: {getattr(result, 'error_message', None)}")
    print(f"   result:        {(result.result or '').strip()[:300]!r}")
    ok = result.status == "finished"
    print("PROBE: " + ("OK, the agent works" if ok else "FAILED, see error fields above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
