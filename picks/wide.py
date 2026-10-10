"""Select a card from the WHOLE board by cost, not by price band.

Kiley lifted the 47-53% rule on 2026-10-10 and the goal became slow recovery.
card.py still enforces the band through --cap, which threw out the cheapest
markets on the board: the one MLB game on 2026-10-10 quoted all eight of its
markets at a 0.005 spread and not one of them was inside 47-53%.

With no predictive edge the only lever measured to work is cost, so that is what
this selects on: the tightest bid/ask available, posted as a resting limit rather
than crossed. Sides are still assigned by seeded toss and the seed recorded, so
nothing is quietly tilted toward favourites -- the ban on flattering a number by
drifting to chalk outlived the band it was written for.

    python picks/wide.py --max 15 --seed 20261010
"""
import hashlib, json, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder

PM = "https://gateway.polymarket.us"
PT = timezone(timedelta(hours=-7))
LEAGUES = ["nfl", "cfb", "mlb", "wnba", "nba", "nhl", "atp", "wta", "boxing",
           "epl", "ucl", "mls"]
NAME = {"cfb": "NCAAF", "atp": "ATP", "wta": "WTA", "boxing": "BOXING",
        "epl": "EPL", "ucl": "UCL", "mls": "MLS"}
KINDS = ("_team_full_game_spread", "_team_full_game_total",
         "_team_full_game_winner", "_match_winner")


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        u, headers={"User-Agent": "wide/1.0", "Accept": "application/json"}), timeout=60))


def hashed(seed, key):
    return int(hashlib.sha256(f"{seed}|{key}".encode()).hexdigest(), 16)


def board(leagues, now, lo=0.20, hi=0.80, max_spread=0.01):
    """Every tradeable market on today's board, with its cost."""
    out = []
    for lg in leagues:
        try:
            d = get(f"{PM}/v2/leagues/{lg}/events?limit=100&offset=0&type=sport&section=general")
        except Exception:
            continue
        games = 0
        for e in d.get("events") or []:
            ts = e.get("eventDate") or e.get("startTime")
            if not ts or len(e.get("teams") or []) != 2:
                continue
            st = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PT)
            if st.date() != now.date() or st <= now + timedelta(minutes=20):
                continue
            games += 1
            full = get(f"{PM}/v1/events/{e['id']}").get("event") or e
            ab = [(t.get("displayAbbreviation") or "").upper()
                  for t in (full.get("teams") or [])]
            for m in (full.get("markets") or []):
                smt = (m.get("sportsMarketType") or "").lower()
                if not any(smt.endswith(k) for k in KINDS):
                    continue
                if "full_time_winner" in smt:        # 3-way soccer: NO != opposite
                    continue
                b, a = ladder.quote(m.get("bestBidQuote")), ladder.quote(m.get("bestAskQuote"))
                if b is None or a is None or a <= b or a - b > max_spread:
                    continue
                mid = (a + b) / 2
                if not lo <= mid <= hi:
                    continue
                out.append({"lg": lg, "league": NAME.get(lg, lg.upper()),
                            "game": full.get("title"), "slug": m.get("slug"),
                            "smt": smt, "line": m.get("line"), "teams": ab,
                            "bid": b, "ask": a, "mid": mid, "spread": a - b,
                            "start": st,
                            "named": [x.get("description") for x in
                                      sorted(m.get("marketSides", []),
                                             key=lambda x: not x.get("long"))]})
            time.sleep(0.2)
        print(f"  {lg:<6} {games:>3} upcoming game(s)", flush=True)
    return out


def main():
    now = datetime.now(PT)
    def opt(f, d):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d
    seed = opt("--seed", f"{now:%Y%m%d}")
    cap = int(opt("--max", 15))
    rows = board(LEAGUES, now)
    print(f"\n{len(rows)} tradeable markets at a spread of 1c or tighter")
    # one per game, the tightest; then the tightest games, with ties broken by seed
    best = {}
    for r in sorted(rows, key=lambda r: (r["spread"], hashed(seed, r["slug"]))):
        best.setdefault(r["game"], r)
    # Round-robin across leagues, not a global sort on tightness. Sorting globally
    # let CFB's 94 games swamp the card -- 16 of 16 were NCAAF, which is no more
    # "the whole board" than the old band was. Each pass takes the tightest
    # remaining game in each league that still has one.
    byleague = {}
    for r in sorted(best.values(),
                    key=lambda r: (r["spread"], hashed(seed, "g|" + r["game"]))):
        byleague.setdefault(r["lg"], []).append(r)
    order = sorted(byleague, key=lambda lg: -len(byleague[lg]))
    picked = []
    while len(picked) < cap and any(byleague.values()):
        for lg in order:
            if byleague.get(lg) and len(picked) < cap:
                picked.append(byleague[lg].pop(0))
    json.dump(picked, open("/tmp/wide.json", "w"), indent=2, default=str)
    from collections import Counter
    print(f"{len(best)} distinct games; taking {len(picked)}")
    print("  by league:", dict(Counter(r["league"] for r in picked)))
    print(f"  median spread {sorted(r['spread'] for r in picked)[len(picked)//2]:.4f}")
    for r in picked:
        print(f"   {r['league']:<6} {r['start']:%H:%M}  mid {r['mid']:.3f}  "
              f"sp {r['spread']:.3f}  {r['smt'].split('_')[-1]:<7} "
              f"line={str(r['line']):<6} {r['game'][:38]}")


if __name__ == "__main__":
    main()
