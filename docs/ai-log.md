# AI log

Running log of AI suggestions that were wrong or needed correcting, kept as it happens.

| When | What was suggested | What was wrong | What I did |
| --- | --- | --- | --- |
| Scaffold | `decisions.media_id` unique | Breaks the audit trail once an admin approves a score hold (needs a second decision) | Made decisions append-only, `media.decision_id` points at the current one (decisions.md #3) |
| Scaffold | Run pytest and alembic from the project folder as is | Neither put the project root on `sys.path`, so `import app` failed | Added `pythonpath` to pytest config and `prepend_sys_path` to alembic.ini |
| Step 2 | Identity dependency shares the route's DB session | The identity query auto-began a transaction, so `session.begin()` in the route raised and every upload returned 500 | Gave identity its own short session (decisions.md #12) |
| Step 3 | Assert the loading state with `getByRole("status")` | v0 renders plain "Loading" text, so that test failed for a reason that has nothing to do with the incident and would have muddied the evidence | Matched on the loading text instead, so only the real symptom fails against v0 |
| Step 3 | Stop test services with `terminate()` and a 10 s wait | The provider holds requests open on purpose in timeout mode, so shutdown hung and reported an error on an unrelated test | Kill after 5 s if terminate does not finish |
| Step 4 | Re-screen loop claims its probe slot on every tick | The first tick at startup spent the 30 s probe window with nothing held, so real recovery waited up to 30 s and the recovery test failed | Only claim a probe when held items are waiting |
| Step 4 | Watchdog test with a 1 s callback timeout and a 2.5 s callback delay | My test was wrong, not the code: the watchdog correctly resubmitted twice (3 calls) | Set timeout 2 s and delay 3 s so exactly one resubmit happens |
| Step 4 | MSW `server.listen({ onUnhandledRequest: "error" })` | MSW 3 renamed the option to `onUnhandledFrame`; vitest ran fine but `tsc` failed the build | Switched to `onUnhandledFrame` and checked that an unmocked request now fails the test |
