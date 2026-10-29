| Metric | before (v0) | after (fixed) |
| --- | --- | --- |
| Unchecked published (should be 0) | 30 | 0 |
|   published without a complete decision | 0 | 0 |
|   published although the full-frame policy says no | 30 | 0 |
| Refused prompts accepted | 9 of 9 | 0 of 9 |
| Provider calls per upload (target 1.0) | 1.034 | 1.022 |
| Oldest waiting job, outage | 39.7 s | 21.8 s |
| Oldest waiting job, recovery | 44.1 s | 22.6 s |
| ws_unverified image to first outcome (held counts) p50 / p95, healthy | 0.09 s / 0.15 s | 0.08 s / 0.12 s |
| ws_unverified image to first outcome (held counts) p50 / p95, outage | 27.63 s / 43.14 s | 20.46 s / 21.31 s |
| ws_unverified image to first outcome (held counts) p50 / p95, recovery | 0.08 s / 7.55 s | 0.13 s / 9.10 s |
| ws_unverified image to final decision p50 / p95, healthy | 0.09 s / 0.15 s | 0.08 s / 0.12 s |
| ws_unverified image to final decision p50 / p95, outage | 27.63 s / 43.14 s | 41.43 s / 52.47 s |
| ws_unverified image to final decision p50 / p95, recovery | 0.08 s / 7.55 s | 0.13 s / 9.10 s |
| Items whose final status differs from the full-frame policy | 30 (expected refused -> published: 9, expected blocked -> published: 13, expected held -> published: 8) | 0 |
| Final statuses | blocked 135, held 72, published 525 | blocked 148, held 80, published 495 |
| Expected statuses (full-frame policy) | blocked 148, held 80, published 495, refused 9 | blocked 148, held 80, published 495 |
