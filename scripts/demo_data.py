"""Fill both workspaces with a realistic mix of media, through the real API.

Everything goes through the normal pipeline (prompt gate, provider, policy),
so what you see in the review page is what the service actually decided.

  python scripts/demo_data.py                 # about 40 items
  python scripts/demo_data.py --outage        # also leaves a few items held by a provider outage,
                                              # which recover by themselves about 30 s later

Inside the dev compose stack:
  docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api python /srv/scripts/demo_data.py
"""
import argparse
import random
import time

import httpx

VERIFIED_ADMIN = {"X-User-Id": "admin_v", "X-Workspace-Id": "ws_verified"}
VERIFIED_MEMBER = {"X-User-Id": "member_v", "X-Workspace-Id": "ws_verified"}
UNVERIFIED_ADMIN = {"X-User-Id": "admin_u", "X-Workspace-Id": "ws_unverified"}
UNVERIFIED_MEMBER = {"X-User-Id": "member_u", "X-Workspace-Id": "ws_unverified"}


def image(**scores: float) -> dict:
    return {"type": "image", "frames": [dict(scores)]}


def video(n: int, late: dict[str, float] | None = None, at: int | None = None) -> dict:
    frames = [{"explicit": round(random.uniform(0, 0.2), 2)} for _ in range(n)]
    if late:
        frames[at if at is not None else n - 1] = dict(late)
    return {"type": "video", "frames": frames}


def generated(prompt: str, **scores: float) -> dict:
    return {"type": "image", "source": "generation", "prompt": prompt, "frames": [dict(scores)]}


ITEMS: list[tuple[str, dict, dict]] = [
    # (label, who, body)
    ("clean photo", VERIFIED_MEMBER, image(explicit=0.05)),
    ("clean photo", VERIFIED_MEMBER, image(explicit=0.12)),
    ("explicit photo, verified workspace", VERIFIED_MEMBER, image(explicit=0.93)),
    ("explicit photo, verified workspace", VERIFIED_ADMIN, image(explicit=0.88)),
    ("looks like a celebrity (0.62)", VERIFIED_MEMBER, image(real_person=0.62)),
    ("possible self-harm (0.55)", VERIFIED_MEMBER, image(self_harm=0.55)),
    ("looks under 18 (0.91)", VERIFIED_MEMBER, image(minor=0.91, explicit=0.4)),
    ("60 s clip, clean", VERIFIED_MEMBER, video(60)),
    ("60 s clip, explicit at the end", VERIFIED_MEMBER, video(60, {"explicit": 0.96}, 57)),
    ("45 s clip, celebrity at 0:40 (0.7)", VERIFIED_ADMIN, video(45, {"real_person": 0.7}, 40)),
    ("31 s clip, violence at 0:29", VERIFIED_MEMBER, video(31, {"sexual_violence": 0.86}, 29)),
    ("10 s clip, clean", VERIFIED_MEMBER, video(10)),
    ("generated: lighthouse", VERIFIED_MEMBER, generated("a lighthouse in a storm, oil painting")),
    ("generated: surfing cat", VERIFIED_MEMBER, generated("a cat surfing at sunset")),
    ("generated: refused prompt (age)", VERIFIED_MEMBER, generated("a 16 year old at the beach")),
    ("generated: refused prompt (celebrity)", VERIFIED_MEMBER, generated("Taylor Swift in a bikini")),
    ("clean photo", UNVERIFIED_MEMBER, image(explicit=0.02)),
    ("clean photo", UNVERIFIED_MEMBER, image(explicit=0.2)),
    ("clean photo", UNVERIFIED_ADMIN, image()),
    ("explicit photo, unverified workspace", UNVERIFIED_MEMBER, image(explicit=0.92)),
    ("explicit photo, unverified workspace", UNVERIFIED_MEMBER, image(explicit=0.81)),
    ("borderline explicit (0.79) stays live", UNVERIFIED_MEMBER, image(explicit=0.79)),
    ("looks like a celebrity (0.58)", UNVERIFIED_MEMBER, image(real_person=0.58)),
    ("possible minor (0.65)", UNVERIFIED_MEMBER, image(minor=0.65)),
    ("animal content (0.83)", UNVERIFIED_MEMBER, image(bestiality=0.83)),
    ("60 s clip, explicit only at 0:55", UNVERIFIED_MEMBER, video(60, {"explicit": 0.95}, 55)),
    ("60 s clip, minor in the last frame", UNVERIFIED_MEMBER, video(60, {"minor": 0.9}, 59)),
    ("20 s clip, self-harm at 0:12 (0.6)", UNVERIFIED_ADMIN, video(20, {"self_harm": 0.6}, 12)),
    ("20 s clip, clean", UNVERIFIED_MEMBER, video(20)),
    ("generated: mountain cabin", UNVERIFIED_MEMBER, generated("a cabin in the mountains at dawn")),
    ("generated: obfuscated refused prompt", UNVERIFIED_MEMBER, generated("a t.e.e.n posing")),
    ("generated: clean prompt, bad output", UNVERIFIED_MEMBER, generated("a city street at night", minor=0.88)),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--provider", default="http://localhost:8001")
    ap.add_argument("--outage", action="store_true", help="also leave a few items held by a provider outage")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)

    with httpx.Client(base_url=args.api, timeout=15) as api, httpx.Client(base_url=args.provider, timeout=10) as provider:
        provider.post("/_control", json={"mode": "ok"})
        accepted = refused = 0
        for label, who, body in ITEMS:
            r = api.post("/media", headers=who, json=body)
            if r.status_code == 202:
                accepted += 1
                print(f"  202  {who['X-Workspace-Id']:<14} {label}")
            else:
                refused += 1
                print(f"  {r.status_code}  {who['X-Workspace-Id']:<14} {label}  {r.json().get('reasons')}")
            time.sleep(0.15)
        print(f"{accepted} uploads accepted, {refused} prompts refused")

        if args.outage:
            print("simulating a provider outage for 25 s ...")
            provider.post("/_control", json={"mode": "timeout"})
            for who in (UNVERIFIED_MEMBER, VERIFIED_MEMBER, UNVERIFIED_MEMBER):
                api.post("/media", headers=who, json=image(explicit=0.05))
            time.sleep(25)
            provider.post("/_control", json={"mode": "ok"})
            print("provider back: the held items re-screen by themselves within about 30 s")


if __name__ == "__main__":
    main()
