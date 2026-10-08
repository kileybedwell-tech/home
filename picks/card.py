"""Build a coin-flip card from Polymarket, with directions balanced.

Every market kept sits inside the band, so both of its sides are coin flips and the
choice of side carries no edge either way. Choosing each side by an independent toss
still clusters them, though: one card came out 7 unders in 10 totals, which is not
seven coin flips but one bet on a quiet night placed seven times, and it lost as a
block. So the sides are dealt instead of tossed -- shuffled by the seed, then
alternated -- which leaves each side just as unbiased while forcing the counts even.
Same expected hit rate, far less swing.

    python picks/card.py                        # today, every league
    python picks/card.py cfb mlb --max-per 8    # cap a league's picks
    python picks/card.py --out picks/2026-09-26-mixed.json --seed 20260926
"""
import hashlib, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder, fees
from datetime import datetime, timezone, timedelta

PM = "https://gateway.polymarket.us"
PT = timezone(timedelta(hours=-7))
LEAGUES = ["nfl", "cfb", "mlb", "wnba", "nba", "nhl", "atp", "wta", "boxing",
           "epl", "ucl", "mls"]          # soccer: spreads and totals only
NAME = {"cfb": "NCAAF", "atp": "ATP", "wta": "WTA", "boxing": "BOXING",
        "epl": "EPL", "ucl": "UCL", "mls": "MLS"}


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "card/1.0", "Accept": "application/json"}), timeout=45))


def quote(q):
    return float(q["value"]) if isinstance(q, dict) and q.get("value") is not None else None


def hashed(seed, key):
    return int(hashlib.sha256(f"{seed}|{key}".encode()).hexdigest(), 16)


def games(league, today):
    out = []
    try:
        d = get(f"{PM}/v2/leagues/{league}/events?limit=100&offset=0&type=sport&section=general")
    except Exception:
        return out
    for e in d.get("events") or []:
        ts = e.get("eventDate") or e.get("startTime")
        if not ts or len(e.get("teams") or []) != 2:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PT)
        if dt.date() == today:
            e["_start"] = dt
            out.append(e)
    return sorted(out, key=lambda e: e["_start"])


def candidates(event, league, cap):
    """One spread, one total, or a match winner where that is all the book posts.

    The spread and total rungs are chosen by differencing the ladder (see
    picks/ladder.py), not by taking whichever rung sits nearest even money. Two
    rungs half a point apart can straddle a key number carrying several times the
    mass of its neighbours, and nearest-50% cannot see that. Both sides of a kept
    market are inside the band, so the side itself is still free to assign.
    """
    floor = 1 - cap
    full = get(f"{PM}/v1/events/{event['id']}").get("event") or event
    ab = [(t.get("displayAbbreviation") or "").upper() for t in full.get("teams", [])]
    if len(ab) != 2:
        return []
    markets = full.get("markets") or []

    def row(kind, m, mid, why=None):
        bid, ask = quote(m.get("bestBidQuote")), quote(m.get("bestAskQuote"))
        return {"kind": kind, "mid": mid, "bid": bid, "ask": ask, "slug": m.get("slug"),
                "line": None if m.get("line") is None else float(m["line"]),
                "teams": ab, "game": full.get("title"),
                "league": NAME.get(league, league.upper()), "start": event["_start"],
                "rung_why": why,
                # both competitors as the book names them, long side first
                "named": [x.get("description") for x in
                          sorted(m.get("marketSides", []), key=lambda x: not x.get("long"))]}

    best = {}
    for kind, suffix in (("spread", "_team_full_game_spread"),
                         ("total", "_team_full_game_total")):
        lad = ladder.rungs(markets, suffix)
        got = ladder.choose(lad, floor, cap)
        if got:
            _, mid, m, why = got
            best[kind] = row(kind, m, mid, why)

    if not best:                       # money line only where there is nothing else
        for m in markets:
            smt = (m.get("sportsMarketType") or "").lower()
            if not (smt.endswith("_team_full_game_winner") or smt.endswith("_match_winner")):
                continue
            bid, ask = quote(m.get("bestBidQuote")), quote(m.get("bestAskQuote"))
            if bid is None or ask is None or ask - bid > 0.04:
                continue
            mid = (bid + ask) / 2
            if not floor <= mid <= cap:
                continue
            if "ml" not in best or abs(mid - .5) < abs(best["ml"]["mid"] - .5):
                best["ml"] = row("ml", m, mid, "no spread or total posted")
    return [best[k] for k in ("spread", "total", "ml") if k in best]


def deal(rows, seed, kind, first, second):
    """Shuffle by the seed, then alternate the two directions."""
    rows = sorted(rows, key=lambda r: hashed(seed, r["slug"]))
    flip = hashed(seed, kind) % 2
    return [(r, (first, second)[(i + flip) % 2]) for i, r in enumerate(rows)]


def build(rows, seed):
    out = []
    for r, want in (deal([x for x in rows if x["kind"] == "total"], seed, "total", "over", "under")
                    + deal([x for x in rows if x["kind"] == "spread"], seed, "spread", "take", "lay")):
        a, b = r["teams"]
        if r["kind"] == "total":
            pct = r["mid"] if want == "over" else 1 - r["mid"]
            cost = r["ask"] if want == "over" else 1 - r["bid"]
            label = f"{want.upper()} {r['line']:g}"
            resolve, side = {"kind": "total", "side": want, "line": r["line"]}, \
                ("YES" if want == "over" else "NO")
        else:
            # YES is teams[0] on the signed line, so the side taking points is
            # teams[0] when the line is positive and teams[1] when it is negative
            taker_is_yes = r["line"] > 0
            yes = taker_is_yes if want == "take" else not taker_is_yes
            team, hcap = (a, r["line"]) if yes else (b, -r["line"])
            pct = r["mid"] if yes else 1 - r["mid"]
            cost = r["ask"] if yes else 1 - r["bid"]
            label = f"{team} {hcap:+g}"
            resolve, side = {"kind": "spread", "team": team, "line": hcap}, ("YES" if yes else "NO")
        out.append((r, label, resolve, side, pct, cost))
    for r in [x for x in rows if x["kind"] == "ml"]:          # no direction to balance
        yes = hashed(seed, r["slug"]) % 2 == 0
        team = r["teams"][0] if yes else r["teams"][1]
        pct = r["mid"] if yes else 1 - r["mid"]
        cost = r["ask"] if yes else 1 - r["bid"]
        named = r["named"] if len(r["named"]) == 2 else r["teams"]
        label = f"{named[0] if yes else named[1]} to win"
        out.append((r, label, {"kind": "moneyline", "team": team}, "YES" if yes else "NO", pct, cost))
    picks = []
    for r, label, resolve, side, pct, cost in out:
        cost = round(cost, 4)
        picks.append({"game": r["game"], "league": r["league"],
                      "kickoff_pt": f"{r['start']:%Y-%m-%d %H:%M}", "live_when_logged": False,
                      "decided_pregame": True, "pick": label, "type": resolve["kind"],
                      "market_slug": r["slug"], "market_side": side, "resolve": resolve,
                      "rung_why": r.get("rung_why"),
                      "implied_win_pct": round(pct, 3), "cost": cost,
                      "pays_per_100": round(100 / cost, 1),
                      # the fee is a known cost, so it rides on the pick rather
                      # than being accounted for after the card is settled
                      **fees.economics(cost)})
    return sorted(picks, key=lambda p: (p["league"], p["kickoff_pt"], p["type"]))


def main():
    VALUED = ("--cap", "--max-per", "--seed", "--out")
    argv, args = sys.argv[1:], []
    skip = False
    for i, a in enumerate(argv):
        if skip:
            skip = False
        elif a.startswith("--"):
            skip = a in VALUED          # its value is not a league name
        else:
            args.append(a)
    def opt(flag, default):
        return argv[argv.index(flag) + 1] if flag in argv else default
    cap = float(opt("--cap", 0.53))
    per = int(opt("--max-per", 0)) or None
    now = datetime.now(PT)
    seed = opt("--seed", f"{now:%Y%m%d}")
    wanted = [a.lower() for a in args] or LEAGUES
    rows = []
    for lg in wanted:
        got = []
        for e in games(lg, now.date()):
            if e["_start"] <= now:                    # never price a game in play
                continue
            got += candidates(e, lg, cap)
        if per:
            got = sorted(got, key=lambda r: abs(r["mid"] - .5))[:per]
        rows += got
    picks = build(rows, seed)
    card = {"date": f"{now:%Y-%m-%d}", "sport": "MIXED", "venue": "polymarket",
            "rule": f"Coin flips only, {1-cap:.0%}-{cap:.0%} band. Directions dealt, not tossed.",
            "seed": f"{seed} (sha256 of seed|market_slug; shuffles the deal order)",
            "priced_at_pt": f"{now:%Y-%m-%d %H:%M}",
            "note": ("Polymarket only. Both sides of every market kept are inside the band, so "
                     "over/under and take/lay counts are dealt even rather than tossed "
                     "independently -- same expected hit rate, less swing."),
            "players": {"claude": picks, "kiley": []}}
    out = opt("--out", f"picks/{now:%Y-%m-%d}-mixed.json")
    json.dump(card, open(out, "w"), indent=2)
    o = sum(1 for p in picks if p["pick"].startswith("OVER"))
    u = sum(1 for p in picks if p["pick"].startswith("UNDER"))
    sp = [p for p in picks if p["type"] == "spread"]
    tk = sum(1 for p in sp if p["resolve"]["line"] > 0)
    for p in picks:
        print(f'  {p["league"]:<6}{p["kickoff_pt"][11:]}  {p["pick"]:<22}'
              f'{p["implied_win_pct"]:.0%}  {p["game"][:34]}')
    print(f"\n{len(picks)} picks -> {out}\n  totals {o} over / {u} under"
          f"\n  spreads {tk} taking / {len(sp)-tk} laying")


if __name__ == "__main__":
    main()
