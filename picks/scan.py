"""List today's coin-flip markets across leagues, from Polymarket.

This is the routine behind a pick card: find every game on today, pull its money
line, spread and total, and keep only what the market prices near even. A game
with nothing near 50/50 is reported as having no coin flip rather than padded
with a priced-up side.

    python picks/scan.py                 # every league with games today
    python picks/scan.py nfl mlb         # just these
    python picks/scan.py --cap 0.53      # band edge (default 0.53)
"""
import json, re, sys, urllib.request
from datetime import datetime, timezone, timedelta

PM = "https://gateway.polymarket.us"
PT = timezone(timedelta(hours=-7))
LEAGUES = ["nfl", "cfb", "mlb", "wnba", "nba", "nhl"]   # order the report follows


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "scan/1.0", "Accept": "application/json"}), timeout=45))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def quote(q):
    return num(q.get("value")) if isinstance(q, dict) else None


def today_games(league, today):
    try:
        d = get(f"{PM}/v2/leagues/{league}/events?limit=100&offset=0&type=sport&section=general")
    except Exception:
        return []
    out = []
    for e in d.get("events") or []:
        ts = e.get("eventDate") or e.get("startTime") or e.get("startDate")
        if not ts:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PT)
        if dt.date() == today and len(e.get("teams") or []) == 2:
            e["_start"] = dt
            out.append(e)
    return sorted(out, key=lambda e: e["_start"])


def coin_flips(event, cap, max_width=0.04):
    """The nearest-even money line, spread and total, each inside the band.

    A coin flip is a band, not a ceiling: a 6% longshot also sits under a 53% cap
    but is not a coin flip. The floor mirrors the cap, so 53% gives 47-53%.
    """
    floor = 1 - cap
    full = get(f"{PM}/v1/events/{event['id']}").get("event") or event
    rows = []
    for m in full.get("markets") or []:
        smt = (m.get("sportsMarketType") or "").lower()
        if not any(smt.endswith(k) for k in
                   ("full_game_winner", "full_game_spread", "full_game_total")):
            continue
        bid, ask = quote(m.get("bestBidQuote")), quote(m.get("bestAskQuote"))
        if bid is None or ask is None or ask - bid > max_width:
            continue
        mid = (bid + ask) / 2
        short = (m.get("titleShort") or "").strip()
        if smt.endswith("winner"):
            side = next((s for s in m.get("marketSides", []) if s.get("long")), None)
            yes = (side or {}).get("description") or short
            kind, labels = "ML", (f"{yes} ML", f"not {yes} ML")
        elif smt.endswith("spread"):
            kind, labels = "spread", (short, f"other side of {short}")
        else:
            line = num(m.get("line"))
            kind, labels = "total", (f"OVER {line}", f"UNDER {line}")
        for pct, label in ((mid, labels[0]), (1 - mid, labels[1])):
            if floor <= pct <= cap:
                rows.append({"kind": kind, "pick": label, "pct": round(pct, 3)})
    # one per market type: the side sitting closest to even money
    best = {}
    for r in rows:
        if r["kind"] not in best or abs(r["pct"] - .5) < abs(best[r["kind"]]["pct"] - .5):
            best[r["kind"]] = r
    return [best[k] for k in ("ML", "spread", "total") if k in best]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cap = 0.53
    if "--cap" in sys.argv:
        cap = float(sys.argv[sys.argv.index("--cap") + 1])
    wanted = [a.lower() for a in args] or LEAGUES
    now = datetime.now(PT)
    print(f"Coin-flip scan · {now:%A %-d %B %Y, %-I:%M %p PT} · Polymarket · band {1-cap:.0%}-{cap:.0%}\n")
    total = 0
    for league in wanted:
        games = today_games(league, now.date())
        if not games:
            continue
        print(f"=== {league.upper()}")
        for e in games:
            flips = coin_flips(e, cap)
            live = "  [in play]" if e["_start"] <= now else ""
            print(f"  {e['_start']:%-I:%M%p} {e.get('title')[:38]:<38}{live}")
            if not flips:
                print("           no market near even money")
            for r in flips:
                print(f"           {r['pct']:.0%}  {r['kind']:<6} {r['pick']}")
                total += 1
        print()
    print(f"{total} coin-flip pick(s) in the {1-cap:.0%}-{cap:.0%} band")


if __name__ == "__main__":
    main()
