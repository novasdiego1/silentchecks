import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from silentchecks.cli import main
from silentchecks.core import Ledger, Run, check, exit_code, parse_duration, status

PY = sys.executable
PASS = [PY, "-c", "pass"]
FAIL = [PY, "-c", "import sys; print('3 rows missing'); sys.exit(1)"]
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def test_verdicts():
    assert check("c", PASS).verdict == "passed"
    r = check("c", FAIL)
    assert r.verdict == "failed" and r.exit_code == 1 and "3 rows missing" in r.output
    assert check("c", ["no-such-command-xyz"]).verdict == "error"
    assert check("c", [PY, "-c", "import time; time.sleep(5)"], timeout=0.5).verdict == "error"


def test_canary_that_is_caught_keeps_the_real_verdict():
    r = check("c", PASS, canary=FAIL)
    assert r.verdict == "passed" and r.canary["caught"]
    assert check("c", FAIL, canary=FAIL).verdict == "failed"


def test_canary_that_passes_makes_the_check_blind_even_if_it_passes():
    r = check("c", PASS, canary=PASS)
    assert r.verdict == "blind" and r.exit_code == 0 and not r.canary["caught"]
    assert "cannot be trusted" in r.detail


def test_canary_that_cannot_run_is_an_error():
    assert check("c", PASS, canary=["no-such-command-xyz"]).verdict == "error"


def test_a_real_blind_gate():
    """A gate that reads the wrong key: it can never see a missing row, so it always passes."""
    gate = ("import json, sys; rows = json.loads(sys.argv[1]); "
            "sys.exit(0 if all(r.get('stamp_at') is not None or True for r in rows) else 1)")
    planted = json.dumps([{"id": 1, "stamp": None}])
    r = check("stamps", [PY, "-c", gate, "[]"], canary=[PY, "-c", gate, planted])
    assert r.verdict == "blind"
    fixed = "import json, sys; rows = json.loads(sys.argv[1]); sys.exit(0 if all(r.get('stamp') for r in rows) else 1)"
    assert check("stamps", [PY, "-c", fixed, "[]"], canary=[PY, "-c", fixed, planted]).verdict == "passed"


def run(name, verdict, hours_ago, canary=None):
    return Run(name, (NOW - timedelta(hours=hours_ago)).isoformat(), verdict, 0.1, 0, "detail", "", canary)


def test_status_names_what_never_ran_and_what_is_stale():
    runs = [run("backup", "passed", 30), run("links", "passed", 1)]
    states = {s.name: s for s in status(runs, {"backup": timedelta(hours=26), "deliveries": timedelta(hours=24),
                                               "links": timedelta(hours=24)}, now=NOW)}
    assert states["deliveries"].state == "never-ran"
    assert states["backup"].state == "stale" and "30h old" in states["backup"].notes[0]
    assert states["links"].state == "passed"
    assert exit_code(list(states.values())) == 3


def test_failures_outrank_missing_checks():
    states = status([run("a", "failed", 1)], {"b": timedelta(hours=1)}, now=NOW)
    assert exit_code(states) == 1
    assert exit_code(status([run("a", "passed", 1)], {}, now=NOW)) == 0


def test_always_passing_check_without_canary_gets_a_note():
    rows = [run("gate", "passed", h) for h in range(20, 0, -1)]
    note = status(rows, {}, now=NOW)[0].notes
    assert note and "may be checking nothing" in note[0]
    with_canary = [run("gate", "passed", h, canary={"caught": True}) for h in range(20, 0, -1)]
    assert status(with_canary, {}, now=NOW)[0].notes == []


def test_ledger_round_trip_and_bad_rows(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    assert ledger.runs() == []
    ledger.append(check("c", PASS))
    assert [r.verdict for r in ledger.runs()] == ["passed"]
    (tmp_path / "l.jsonl").write_text("not json\n")
    with pytest.raises(ValueError, match="l.jsonl:1"):
        ledger.runs()


def test_parse_duration():
    assert parse_duration("90m") == timedelta(minutes=90)
    assert parse_duration("7d") == timedelta(days=7)
    with pytest.raises(ValueError):
        parse_duration("tomorrow")


def test_cli_end_to_end(tmp_path, capsys):
    led = str(tmp_path / "l.jsonl")
    assert main(["--ledger", led, "run", "ok", "--", *PASS]) == 0
    assert main(["--ledger", led, "run", "gate", "--canary", f"{PY} -c pass", "--", *PASS]) == 4
    assert main(["--ledger", led, "run", "bad", "--", *FAIL]) == 1
    capsys.readouterr()
    assert main(["--ledger", led, "status", "--expect", "nightly=24h"]) == 1
    out = capsys.readouterr().out
    assert "NEVER-RAN" in out and "BLIND" in out and "FAILED" in out
    assert main(["--ledger", led, "status", "--expect", "bad"]) == 2
