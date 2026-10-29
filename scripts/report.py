"""Before vs after table from two workload JSON files.

  uv run python ../scripts/report.py ../docs/evidence/02-broken-workload.json ../docs/evidence/04-fixed-workload.json
"""
import json
import sys
from pathlib import Path


def lat(run: dict, ws: str, phase: str, p: str) -> str:
    v = run.get("image_latency_s", {}).get(ws, {}).get(phase, {}).get(p)
    return "n/a" if v is None else f"{v:.2f} s"


def first(run: dict, ws: str, phase: str, p: str) -> str:
    v = run.get("image_time_to_first_outcome_s", {}).get(ws, {}).get(phase, {}).get(p)
    return "n/a" if v is None else f"{v:.2f} s"


def age(run: dict, phase: str) -> str:
    v = run.get("oldest_waiting_job_age_s_max_by_phase", {}).get(phase)
    return "n/a" if v is None else f"{v:.1f} s"


ROWS = [
    ("Unchecked published (should be 0)", lambda r: r["unchecked_published"]),
    ("  published without a complete decision", lambda r: r["published_without_complete_decision"]),
    ("  published although the full-frame policy says no", lambda r: r["published_against_true_policy"]),
    ("Refused prompts accepted", lambda r: f'{r["refused_prompts_accepted"]} of {r["refused_prompts_sent"]}'),
    ("Provider calls per upload (target 1.0)", lambda r: r["provider_calls_per_upload"]),
    ("Oldest waiting job, outage", lambda r: age(r, "outage")),
    ("Oldest waiting job, recovery", lambda r: age(r, "recovery")),
    ("ws_unverified image to first outcome (held counts) p50 / p95, healthy",
     lambda r: f'{first(r, "ws_unverified", "healthy", "p50")} / {first(r, "ws_unverified", "healthy", "p95")}'),
    ("ws_unverified image to first outcome (held counts) p50 / p95, outage",
     lambda r: f'{first(r, "ws_unverified", "outage", "p50")} / {first(r, "ws_unverified", "outage", "p95")}'),
    ("ws_unverified image to first outcome (held counts) p50 / p95, recovery",
     lambda r: f'{first(r, "ws_unverified", "recovery", "p50")} / {first(r, "ws_unverified", "recovery", "p95")}'),
    ("ws_unverified image to final decision p50 / p95, healthy",
     lambda r: f'{lat(r, "ws_unverified", "healthy", "p50")} / {lat(r, "ws_unverified", "healthy", "p95")}'),
    ("ws_unverified image to final decision p50 / p95, outage",
     lambda r: f'{lat(r, "ws_unverified", "outage", "p50")} / {lat(r, "ws_unverified", "outage", "p95")}'),
    ("ws_unverified image to final decision p50 / p95, recovery",
     lambda r: f'{lat(r, "ws_unverified", "recovery", "p50")} / {lat(r, "ws_unverified", "recovery", "p95")}'),
    ("Items whose final status differs from the full-frame policy",
     lambda r: f'{r.get("final_vs_expected_mismatches", "n/a")}'
               + (f' ({", ".join(f"{k}: {v}" for k, v in r["mismatch_breakdown"].items())})'
                  if r.get("mismatch_breakdown") else "")),
    ("Final statuses", lambda r: ", ".join(f"{k} {v}" for k, v in sorted(r["final_status_counts"].items()))),
    ("Expected statuses (full-frame policy)",
     lambda r: ", ".join(f"{k} {v}" for k, v in sorted(r["expected_status_counts"].items()))),
]


def main() -> None:
    runs = [json.loads(Path(p).read_text()) for p in sys.argv[1:]]
    if not runs:
        sys.exit("usage: report.py RUN.json [RUN.json ...]")
    print("| Metric | " + " | ".join(r["label"] for r in runs) + " |")
    print("| --- | " + " | ".join("---" for _ in runs) + " |")
    for name, get in ROWS:
        print(f"| {name} | " + " | ".join(str(get(r)) for r in runs) + " |")


if __name__ == "__main__":
    main()
