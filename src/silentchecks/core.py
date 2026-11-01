"""Two failures that look exactly like success.

1. A check that cannot fail. A gate that compares the wrong field, reads an empty list or swallows its own error
   passes every time, and a gate that passes every time looks the same as a gate that works. The fix is a
   canary: before the real check, run it on a planted problem it must catch. If the canary passes, the check is
   blind, whatever it says about the real input.

2. A check that did not run. A cron job that stopped, a CI step that was skipped or a script that crashed before
   its first line leaves no failure behind, only an absence, and an absence reads as "all good". The fix is a
   ledger: every run leaves a row with its verdict, and the status report names checks that never ran or have
   not run recently, instead of leaving them out.

Verdicts of a run: passed, failed, blind (the canary was not caught), error (the check could not run: command
not found, timeout). Extra states in the status report: never-ran, stale.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_LEDGER = "silentchecks.jsonl"
OUTPUT_TAIL = 500
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta:
    m = _DURATION.match(text.strip())
    if not m:
        raise ValueError(f"bad duration {text!r}; use a number and s, m, h or d (for example 30m, 24h, 7d)")
    return timedelta(seconds=float(m.group(1)) * _UNITS[m.group(2)])


@dataclass
class Outcome:
    exit_code: int | None  # None when the command could not run
    seconds: float
    output: str
    error: str | None = None


def execute(argv: Sequence[str], timeout: float) -> Outcome:
    """Run a command without a shell. Not found and timeout are errors, not failures."""
    t0 = time.monotonic()
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return Outcome(None, time.monotonic() - t0, "", f"command not found: {argv[0]}")
    except subprocess.TimeoutExpired as e:
        out = "".join(x.decode(errors="replace") if isinstance(x, bytes) else x for x in (e.stdout, e.stderr) if x)
        return Outcome(None, time.monotonic() - t0, out[-OUTPUT_TAIL:], f"timed out after {timeout:g}s")
    except OSError as e:
        return Outcome(None, time.monotonic() - t0, "", f"could not run: {e}")
    return Outcome(p.returncode, time.monotonic() - t0, (p.stdout + p.stderr)[-OUTPUT_TAIL:])


@dataclass
class Run:
    name: str
    started: str  # ISO 8601, UTC
    verdict: str  # passed | failed | blind | error
    seconds: float
    exit_code: int | None
    detail: str
    output: str = ""
    canary: dict | None = None  # {"exit_code", "caught", "error"} when a canary was used

    @property
    def started_at(self) -> datetime:
        return datetime.fromisoformat(self.started)


def check(name: str, argv: Sequence[str], *, canary: Sequence[str] | None = None, timeout: float = 300.0,
          now: datetime | None = None) -> Run:
    """Run the canary (if any), then the check, and return the verdict.

    The canary is a command that runs the same check against a planted problem: a record that must not appear,
    a value that must be rejected, a file that must be missing. It has to FAIL (non-zero exit). If it passes,
    the check cannot see the problem it exists for, and the run is "blind" even if the real check passes.
    """
    started = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    canary_info = None
    if canary:
        c = execute(canary, timeout)
        canary_info = {"exit_code": c.exit_code, "caught": c.exit_code not in (None, 0), "error": c.error}
        if c.exit_code is None:
            return Run(name, started, "error", c.seconds, None, f"canary could not run: {c.error}", c.output,
                       canary_info)
        if c.exit_code == 0:
            # The real check is still run, so the ledger shows what it would have said.
            r = execute(argv, timeout)
            said = "passed" if r.exit_code == 0 else "failed" if r.exit_code is not None else "error"
            return Run(name, started, "blind", c.seconds + r.seconds, r.exit_code,
                       f"the canary passed (exit 0): this check did not catch a planted problem; the real check "
                       f"{said}, but that result cannot be trusted", r.output, canary_info)
    r = execute(argv, timeout)
    if r.exit_code is None:
        return Run(name, started, "error", r.seconds, None, r.error or "could not run", r.output, canary_info)
    verdict = "passed" if r.exit_code == 0 else "failed"
    detail = f"exit {r.exit_code}" + ("; canary caught" if canary_info else "")
    return Run(name, started, verdict, r.seconds, r.exit_code, detail, r.output, canary_info)


class Ledger:
    """Append-only JSON Lines file, one row per run. Plain text on purpose: readable, diffable, greppable."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path or os.environ.get("SILENTCHECKS_LEDGER") or DEFAULT_LEDGER)

    def append(self, run: Run) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(run), ensure_ascii=False) + "\n")

    def runs(self) -> list[Run]:
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                out.append(Run(**json.loads(line)))
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"{self.path}:{n}: not a silentchecks row ({e})") from e
        return out


@dataclass
class CheckState:
    name: str
    state: str  # passed | failed | blind | error | never-ran | stale
    last_run: str | None = None
    age: str | None = None
    runs: int = 0
    max_age: str | None = None
    notes: list[str] = field(default_factory=list)


def _age(delta: timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 120:
        return f"{s}s"
    if s < 7200:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def status(runs: list[Run], expected: dict[str, timedelta], *, now: datetime | None = None,
           always_passed_after: int = 20) -> list[CheckState]:
    """One state per check: every expected check, plus every check that appears in the ledger.

    An expected check with no rows is "never-ran"; one whose last run is older than its max age is "stale".
    A check that has passed many times, never failed and never used a canary gets a note: from the outside it
    cannot be told apart from a check that checks nothing.
    """
    now = now or datetime.now(UTC)
    by_name: dict[str, list[Run]] = {}
    for r in runs:
        by_name.setdefault(r.name, []).append(r)
    out = []
    for name in sorted(set(by_name) | set(expected)):
        rows = sorted(by_name.get(name, []), key=lambda r: r.started)
        limit = expected.get(name)
        if not rows:
            out.append(CheckState(name, "never-ran", max_age=_age(limit) if limit else None,
                                  notes=["no run recorded"]))
            continue
        last = rows[-1]
        age = now - last.started_at
        st = CheckState(name, last.verdict, last.started, _age(age), len(rows), _age(limit) if limit else None)
        if limit is not None and age > limit:
            st.notes.append(f"last {last.verdict} run is {_age(age)} old (max {_age(limit)})")
            st.state = "stale"
        if last.verdict in ("failed", "blind", "error"):
            st.notes.append(last.detail)
        verdicts = {r.verdict for r in rows}
        if len(rows) >= always_passed_after and verdicts == {"passed"} and not any(r.canary for r in rows):
            st.notes.append(f"passed all {len(rows)} runs and never used a canary: it may be checking nothing")
        out.append(st)
    return out


SEVERITY = {"failed": 1, "blind": 1, "error": 1, "never-ran": 3, "stale": 3, "passed": 0}


def exit_code(states: list[CheckState]) -> int:
    """1 if any check failed, is blind or could not run; else 3 if any is missing or stale; else 0."""
    codes = {SEVERITY[s.state] for s in states}
    return 1 if 1 in codes else 3 if 3 in codes else 0
