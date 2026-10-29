# Evidence

| File | What it shows |
| --- | --- |
| `01-broken-tests.txt` | Regression tests against the deliberately broken v0 (`SCREENING_MODE=broken`, schema 0001). All 6 API incident tests and the review page error-state test fail; every other test passes. |
| `02-broken-workload.json` | The two-workspace workload replayed against v0 on schema 0001. |
| `03-fixed-tests.txt` | The same tests against the fix, with media in the task's format and the prompt engine: 186 API and 22 web tests pass. |
| `04-fixed-workload.json` | The same workload (same seed) against the fix. |
| `05-crash-test.json` | A worker killed with `os._exit(137)` right after the provider scored a video, before the save. |
| `trace-outage-recovery.txt` | One media item's full log story through an outage and recovery, from a single grep. |
| `report.md` | The before and after table below, produced by `scripts/report.py`. |
| `real-model-check.md` | Real uploads (optional `real` profile): public images and videos from Wikimedia Commons through the local model, including a 58 s video where only the last 3 frames are explicit and get it blocked. |

## Before and after (workload: 30 s healthy, 40 s provider timeout with a 200 video burst, 60 s recovery)

| Metric | before (v0) | after (fixed) |
| --- | --- | --- |
| Unchecked published (should be 0) | 30 | 0 |
|   published without a complete decision | 0 | 0 |
|   published although the full-frame policy says no | 30 | 0 |
| Refused prompts accepted | 9 of 9 | 0 of 9 |
| Provider calls per upload (target 1.0) | 1.034 | 1.021 |
| Oldest waiting job, outage | 39.7 s | 22.0 s |
| Oldest waiting job, recovery | 44.1 s | 24.3 s |
| ws_unverified image to first outcome (held counts) p50 / p95, healthy | 0.09 s / 0.15 s | 0.08 s / 0.12 s |
| ws_unverified image to first outcome (held counts) p50 / p95, outage | 27.63 s / 43.14 s | 20.54 s / 21.99 s |
| ws_unverified image to first outcome (held counts) p50 / p95, recovery | 0.08 s / 7.55 s | 0.13 s / 11.05 s |
| ws_unverified image to final decision p50 / p95, healthy | 0.09 s / 0.15 s | 0.08 s / 0.12 s |
| ws_unverified image to final decision p50 / p95, outage | 27.63 s / 43.14 s | 42.06 s / 50.64 s |
| ws_unverified image to final decision p50 / p95, recovery | 0.08 s / 7.55 s | 0.13 s / 11.05 s |
| Items whose final status differs from the full-frame policy | 30 (expected refused -> published: 9, expected blocked -> published: 13, expected held -> published: 8) | 0 |
| Final statuses | blocked 135, held 72, published 525 | blocked 136, held 91, published 496 |
| Expected statuses (full-frame policy) | blocked 148, held 80, published 495, refused 9 | blocked 136, held 91, published 496 |

### Reading the table

* **Unchecked published 30 to 0.** v0's decision rows claimed every frame was covered, so "published without a complete decision" reads 0 for v0 too. The real damage shows in the row below it, which re-runs the policy over every frame of every item.
* **Burst fairness during the outage:** `ws_unverified` images reached an outcome (held) at 20.5 s p50 and 22.0 s p95, the 20 s hold rule, not a queue behind the 200 videos. v0: 27.6 s to 43.1 s.
* **Calls per upload 1.021.** All 723 screened items were scored successfully exactly once. The 15 extra calls are timeouts sent in the first moments of the outage, before the breaker opened and stopped calls.
* **Time to final decision during the outage is higher after the fix (42 s vs 27.6 s).** v0 was fast because it published images it never screened. The fix holds them until the provider is back. Burst fairness is also tested directly by `tests/test_fairness.py` (with first-in-first-out ordering that test fails at 6.0 s).
* **Recovery p95 11 s.** Images uploaded in the first seconds after the provider returns wait for the breaker's 10 s open period to end.
* **Final status matches the full-frame policy for every item after the fix (0 mismatches), against 30 for v0.** Every outage hold was re-screened automatically; none needed a human.

## Crash test (05)

| Check | Result |
| --- | --- |
| Worker A exit code | 137, right after the provider answered |
| Status right after the crash | `pending` (invisible), ledger shows the call `in_flight` |
| Recovery | Worker B reclaimed the job when the 5 s lease expired, 5.8 s after the crash |
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
