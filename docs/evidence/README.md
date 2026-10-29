# Evidence

| File | What it shows |
| --- | --- |
| `01-broken-tests.txt` | Regression tests against the deliberately broken v0 (`SCREENING_MODE=broken`, schema 0001). All 6 API incident tests and the review page error-state test fail; every other test passes. |
| `02-broken-workload.json` | The two-workspace workload replayed against v0 on schema 0001. |
| `03-fixed-tests.txt` | The same tests against the fix: 125 API and 7 web tests pass. |
| `04-fixed-workload.json` | The same workload (same seed) against the fix. |
| `05-crash-test.json` | A worker killed with `os._exit(137)` right after the provider scored a video, before the save. |
| `trace-outage-recovery.txt` | One media item's full log story through an outage and recovery, from a single grep. |
| `report.md` | The before and after table below, produced by `scripts/report.py`. |

## Before and after (workload: 30 s healthy, 40 s provider timeout with a 200 video burst, 60 s recovery)

| Metric | before (v0) | after (fixed) |
| --- | --- | --- |
| Unchecked published (should be 0) | 30 | 0 |
|   published without a complete decision | 0 | 0 |
|   published although the full-frame policy says no | 30 | 0 |
| Refused prompts accepted | 9 of 9 | 0 of 9 |
| Provider calls per upload (target 1.0) | 1.037 | 1.021 |
| Oldest waiting job, outage | 38.9 s | 21.8 s |
| Oldest waiting job, recovery | 45.1 s | 24.5 s |
| ws_unverified image p50 / p95, healthy | 0.10 s / 0.16 s | 0.08 s / 0.12 s |
| ws_unverified image p50 / p95, outage | 28.42 s / 43.62 s | 40.80 s / 50.77 s |
| ws_unverified image p50 / p95, recovery | 0.10 s / 8.71 s | 0.14 s / 11.02 s |
| Items whose final status differs from the full-frame policy | 30 (expected refused -> published: 9, expected blocked -> published: 13, expected held -> published: 8) | 0 |
| Final statuses | blocked 123, held 83, published 525 | blocked 136, held 91, published 495 |
| Expected statuses (full-frame policy) | blocked 136, held 91, published 495, refused 9 | blocked 136, held 91, published 495 |

### Reading the table

* **Unchecked published 30 to 0.** v0's decision rows claimed every frame was covered, so "published without a complete decision" reads 0 for v0 too. The real damage shows in the row below it, which re-runs the policy over every frame of every item.
* **Calls per upload 1.021.** Every one of the 722 screened items was scored successfully exactly once. The 15 extra calls are timeouts sent in the first moments of the outage, before the breaker opened and stopped calls. The provider may bill those.
* **Image latency during the outage is higher after the fix (p50 40.8 s vs 28.4 s).** That is the fix working: v0 was fast because it published images it never screened. The fix holds them until the provider is back, so the number includes the outage. Burst fairness is proven separately by `tests/test_fairness.py` (a 30 video burst does not delay the other workspace; with first-in-first-out ordering the same test fails at 6.0 s).
* **Recovery p95 11 s.** Images uploaded in the first seconds after the provider returns wait for the breaker's 10 s open period to end before the probe closes it.
* **Final status matches the full-frame policy for every item after the fix (0 mismatches), against 30 for v0.** Every outage hold was re-screened automatically; none needed a human.

## Crash test (05)

| Check | Result |
| --- | --- |
| Worker A exit code | 137, right after the provider answered |
| Status right after the crash | `pending` (invisible), ledger shows the call `in_flight` |
| Recovery | Worker B reclaimed the job when the 5 s lease expired, 6.0 s after the crash |
| Ledger | first call marked `abandoned`, second call `ok` |
| Final status | `blocked` (explicit on the last frame, unverified workspace), never `published` |
| Provider calls | 2: a synchronous call lost to a crash has to be paid for again |
| Scores rows | 1 |

What it proves: lease expiry and reclaim, the fencing token (also tested directly in `tests/test_crash.py::test_stale_worker_write_is_fenced`), the ledger stays honest, and nothing is published unchecked when a worker dies mid-job.

What it does not prove: a real network partition where the provider got our request and we never got the reply (we only simulate "we got the reply and died"); an OOM kill in the middle of a Postgres commit; Postgres failover; clock skew between workers and the database on lease times (leases use the database clock, which helps but is not tested); or a provider without idempotency keys, where a retried request after an unknown outcome is billed twice.

## Regenerate

```bash
cd api
SCREENING_MODE=broken uv run pytest -v         # schema 0001 to match 01
SCREENING_MODE=fixed  uv run pytest -v
uv run python ../scripts/workload.py --label "after (fixed)" --out ../docs/evidence/04-fixed-workload.json
uv run python ../scripts/crash_test.py --out ../docs/evidence/05-crash-test.json   # stop other workers first
uv run python ../scripts/report.py ../docs/evidence/02-broken-workload.json ../docs/evidence/04-fixed-workload.json
```
