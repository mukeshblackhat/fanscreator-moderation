# How I used AI

## Tools

- **Claude (Anthropic) as a pair engineer.** I worked from a written plan: the case study and starter prompt in my project notes, which set out the architecture, the commit order, every test, the workload and this README's outline. Claude wrote most of the code, tests, scripts and docs, and ran them in a cloud workspace with PostgreSQL 16 and Redis 7.
- No other code generation tools.

## How the work was checked

Nothing was marked done without running it. The outputs are committed in [`docs/evidence/`](docs/evidence/).

- **Reproduce first.** The broken v0 was written first. The regression tests were run against it and failed for the right reasons, and that output was saved (`01-broken-tests.txt`) before any fix was written.
- **Real services in tests.** The API tests run against a real Postgres and the real fake provider and API as subprocesses, not mocks, so timeouts, `202`s and signed callbacks behave like they do in compose.
- **Checking the checks.** Some tests were deliberately broken to confirm they can fail:
  - The fairness test fails at 6.0 s when the claim query is switched to first-in-first-out ordering.
  - The MSW setup fails a test on any unmocked request (checked by pointing one test at a wrong path).
  - The database guard rejects a decision row that claims full coverage over 8 stored frames.
- **Live runs, beyond the tests.** The full stack (API, 2 workers, provider) was run by hand in both modes:
  - The incident reproduced against v0.
  - One item was traced through an outage and recovery (`trace-outage-recovery.txt`).
  - The workload ran against both versions.
  - The crash test was run against the live stack.
- **Compose from clean.** `docker compose build --no-cache` and `docker compose up` on macOS (Docker 29.6, Compose 5.3). Every service was healthy, and a smoke run covered publish, block over the async callback, the refused prompt (422), isolation (404/403/401), and an outage hold with 409 on approve and automatic recovery. Unchecked published stayed 0.
- **Ledger against the provider.** The ledger was compared with the provider's own call counts, and successful scores per media were counted in the database (722 of 722 exactly once).

## Mistakes that were caught

The running log, with what was wrong and what changed, is [`docs/ai-log.md`](docs/ai-log.md). The ones that mattered most:

- **Recovery stuck after the provider came back.** A stale "last success" time meant held items never left `held`. No test caught this; a live trace did. It was fixed, and the recovery test now covers the probe path.
- **Re-screen probe slot wasted.** The loop spent its probe slot at startup with nothing to probe, so real recovery waited up to 30 s.
- **Shared database session.** Identity and the route shared one session, which made every upload return 500 until it was split.
- **A test written wrong.** The watchdog test's timing made the code look wrong when it was right (3 calls was the correct answer for those settings). The test was fixed, not the code.
- **One deviation from the plan, on purpose.** Decisions are append-only history instead of one row per media, so admin approvals keep an audit trail (decisions.md #3).

## Not verified

- The full workload and the crash test were run against local services, not inside `docker compose` (compose itself was verified with a clean build and a smoke run, see below).
- Behaviour under a real network partition, an OOM kill mid-commit, a Postgres failover, or clock skew (see the crash test section of the README).
- Prompt gate quality beyond the 30 listed cases. Rules are weak by nature.
- Vendor prices in Part 3 are list prices checked in October 2026, not quotes.
