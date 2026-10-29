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
