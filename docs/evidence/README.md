# Evidence

| File | What it shows |
| --- | --- |
| `01-broken-tests.txt` | Regression tests run against the deliberately broken v0 (`SCREENING_MODE=broken`). All 6 API incident tests and the review-page error-state test fail; every other test passes. |
| `02-broken-workload.json` | The two-workspace workload replayed against v0: 30 s healthy, 40 s provider timeout, 60 s recovery. |
| `03-fixed-tests.txt` | Same tests against the fix (added in step 5). |
| `04-fixed-workload.json` | Same workload against the fix (added in step 5). |

## v0 at a glance (from 02)

| Metric | v0 |
| --- | --- |
| Unchecked published | 30 |
| Refused prompts accepted | 9 of 9 |
| Provider calls per upload | 1.063 |
| ws_unverified image p50 / p95 during the outage | 28.5 s / 43.8 s |

"Published without a complete decision" reads 0 for v0. That is not a pass: v0 wrote decision rows that claimed every frame was covered. The real damage shows up in "published although the full-frame policy says no", which re-runs the policy over every frame of every item.

Regenerate with:

```bash
cd api
SCREENING_MODE=broken uv run pytest -v
uv run python ../scripts/workload.py --label "before (v0)" --out ../docs/evidence/02-broken-workload.json
uv run python ../scripts/report.py ../docs/evidence/02-broken-workload.json
```
