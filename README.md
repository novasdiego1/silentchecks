# silentchecks

Make checks prove they can fail, and make "never ran" visible.

**In plain words.** A smoke alarm that never rings could mean no fire, or a
dead battery, so it has a test button. A night guard who never reports anything
could mean a quiet night, or a guard who never came, so guards sign a logbook.
silentchecks gives your automated checks both: a test button (show it a fake
problem; if it stays quiet, it is broken) and a logbook (a missing signature
means the check never ran).

Two failures look exactly like success:

- **A check that cannot fail.** A gate that reads the wrong field, compares
  against an empty list or swallows its own error passes every time. A gate
  that passes every time looks the same as a gate that works.
- **A check that did not run.** A cron job that stopped, a skipped CI step or a
  script that died before its first line leaves no failure behind, only an
  absence. An absence reads as "all good".

silentchecks wraps any command-line check and fixes both:

- **Canary:** before the real check, it runs the same check on a planted problem
  it must catch. If the canary passes, the run is **BLIND**: the check cannot
  see the problem it exists for, whatever it says about the real input.
- **Ledger:** every run leaves a row (`passed`, `failed`, `blind` or `error`) in
  a plain JSON Lines file. `status` lists every check you expect, and names the
  ones that **never ran** or are **stale** instead of leaving them out.

Python 3.10+, no dependencies.

## Install

```sh
pip install silentchecks
```

## Use

```sh
# Run a check with a canary. The canary must FAIL (non-zero exit).
silentchecks run deliveries \
  --canary "python3 stamps.py canary-unstamped.json" \
  -- python3 stamps.py deliveries.json

# What happened, including what did not happen.
silentchecks status --expect deliveries=24h --expect nightly-backup=26h
```

```text
CHECK           STATE      LAST RUN                   AGE  RUNS  MAX AGE
deliveries      PASSED     2026-11-01T15:55:54+00:00  0s   2     24h
nightly-backup  NEVER-RAN  -                          -    0     26h
  nightly-backup: no run recorded
```

In cron, wrap the job itself and run `status` from a different machine or
schedule, so a dead scheduler cannot hide its own absence:

```cron
0 3 * * *  silentchecks --ledger /var/lib/checks.jsonl run nightly-backup -- /usr/local/bin/verify-backup
0 9 * * *  silentchecks --ledger /var/lib/checks.jsonl status --expect nightly-backup=26h || notify-me
```

## Try the example

`examples/stamps.py` checks that every delivered item has a completion stamp.
With `--buggy` it reads a field that does not exist and treats "missing" as
fine, so it always passes. On the real data, both versions say the same thing;
only the canary tells them apart:

```sh
cd examples
silentchecks run stamps --canary "python3 stamps.py canary-unstamped.json --buggy" \
  -- python3 stamps.py deliveries.json --buggy
# stamps: BLIND (the canary passed (exit 0): this check did not catch a planted problem; ...)

silentchecks run stamps --canary "python3 stamps.py canary-unstamped.json" \
  -- python3 stamps.py deliveries.json
# stamps: PASSED (exit 0; canary caught)
```

## Writing a good canary

A canary is the check pointed at something you know is wrong:

- a record that was never written, for a read-after-write check;
- an item without its completion stamp, for a "everything was delivered" check;
- a value that must be rejected, for a validator;
- an empty or truncated file, for a backup check.

Keep canaries out of real data (separate files, a reserved id, a test table),
so the safety net never pollutes what it protects.

- **Small, same logic.** The canary runs the same validation code on a tiny
  planted input, not the full job. A backup check does not need a 10 GB
  restore to prove it notices an empty file.
- **Setup and cleanup.** The canary command runs with `SILENTCHECKS_CANARY=1`
  (the real check gets `0`), so one script can create its fake record, check,
  and delete it, and refuse to touch real data while in canary mode.
- **Frequent checks.** `--canary-every 1h` runs the canary only if it was not
  caught in the last hour; the rows in between say "canary skipped, last caught
  ..." so nobody mistakes them for proof.

## States and exit codes

| `run` verdict | Meaning | Exit |
|---|---|---|
| `passed` | the check passed (and caught the canary, if any) | 0 |
| `failed` | the check failed | 1 |
| `error` | the check or the canary could not run (not found, timeout) | 2 |
| `blind` | the canary passed: the result cannot be trusted | 4 |

`status --json` gives the same states for scripts and monitoring. `status` adds
`never-ran` and `stale`, and exits `1` if any check failed, is
blind or errored, `3` if any expected check is missing or stale, `0` otherwise
(`2` on usage errors). A check that passed 20 or more times, never failed and
never used a canary gets a note: from the outside it cannot be told apart from
a check that checks nothing.

## Limits

- It runs commands; it does not know what they check. A canary is only as
  good as the planted problem you give it.
- The ledger is a local file. If the machine that writes it dies, `status` on
  that same machine dies with it: run `status` from somewhere else.
- Rows are appended under `flock` on Linux and macOS, so jobs finishing at the
  same time do not corrupt the file. On Windows there is no lock.
- It reads the whole ledger on every `status`; rotate the file if it grows to
  millions of rows.

## License

MIT.
