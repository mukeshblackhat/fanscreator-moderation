# AI log

Running log of AI suggestions that were wrong or needed correcting, kept as it happens.

| When | What was suggested | What was wrong | What I did |
| --- | --- | --- | --- |
| Scaffold | `decisions.media_id` unique | Breaks the audit trail once an admin approves a score hold (needs a second decision) | Made decisions append-only, `media.decision_id` points at the current one (decisions.md #3) |
| Scaffold | Run pytest and alembic from the project folder as is | Neither put the project root on `sys.path`, so `import app` failed | Added `pythonpath` to pytest config and `prepend_sys_path` to alembic.ini |
| Step 2 | Identity dependency shares the route's DB session | The identity query auto-began a transaction, so `session.begin()` in the route raised and every upload returned 500 | Gave identity its own short session (decisions.md #12) |
