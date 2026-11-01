"""silentchecks run NAME [--canary 'CMD'] -- CMD ...   |   silentchecks status [--expect NAME=24h ...]"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import asdict

from silentchecks import __version__
from silentchecks.core import Ledger, check, exit_code, parse_duration, status

RUN_EXIT = {"passed": 0, "failed": 1, "error": 2, "blind": 4}


def _expectations(items: list[str]) -> dict:
    out = {}
    for item in items:
        name, sep, dur = item.rpartition("=")
        if not sep or not name:
            raise ValueError(f"bad --expect {item!r}; use NAME=DURATION, for example nightly-backup=26h")
        out[name] = parse_duration(dur)
    return out


def render(states) -> str:
    rows = [("CHECK", "STATE", "LAST RUN", "AGE", "RUNS", "MAX AGE")]
    rows += [(s.name, s.state.upper(), s.last_run or "-", s.age or "-", str(s.runs), s.max_age or "-") for s in states]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    for s in states:
        for note in s.notes:
            lines.append(f"  {s.name}: {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="silentchecks",
                                 description="Make checks prove they can fail, and make 'never ran' visible.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--ledger", help="JSON Lines file (default $SILENTCHECKS_LEDGER or ./silentchecks.jsonl)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a check, record its verdict")
    run.add_argument("name")
    run.add_argument("--canary", help="command that runs the check on a planted problem; it must fail")
    run.add_argument("--timeout", type=float, default=300.0, help="seconds per command (default 300)")
    run.add_argument("--quiet", action="store_true", help="print nothing, only the exit code")
    run.epilog = "Put the check command after --, for example: silentchecks run backup -- ./verify-backup.sh"

    st = sub.add_parser("status", help="state of every check, including those that never ran")
    st.add_argument("--expect", action="append", default=[],
                    help="NAME=DURATION: the check must have run within DURATION (repeatable)")
    st.add_argument("--json", action="store_true", help="machine-readable output")

    argv = list(sys.argv[1:] if argv is None else argv)
    # Everything after the first -- is the check command, untouched (argparse would read its options as ours).
    command = argv[argv.index("--") + 1:] if "--" in argv else []
    args = ap.parse_args(argv[: argv.index("--")] if "--" in argv else argv)
    ledger = Ledger(args.ledger)
    try:
        if args.cmd == "run":
            if not command:
                ap.error("give the check command after --")
            r = check(args.name, command, canary=shlex.split(args.canary) if args.canary else None,
                      timeout=args.timeout)
            ledger.append(r)
            if not args.quiet:
                print(f"{r.name}: {r.verdict.upper()} ({r.detail})")
                if r.verdict != "passed" and r.output.strip():
                    print(r.output.rstrip())
            return RUN_EXIT[r.verdict]
        states = status(ledger.runs(), _expectations(args.expect))
        print(json.dumps([asdict(s) for s in states], indent=2) if args.json else render(states))
        return exit_code(states)
    except ValueError as e:
        print(f"silentchecks: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
