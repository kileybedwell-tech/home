"""Write a small card from an explicit, reasoned selection.

card.py builds the whole board and deals the sides, which is right for a blind
coin-flip card. A researched card is the opposite shape: few picks, each chosen
for a stated reason, and the reason stored next to the price so it can be scored
against later. Every pick still carries the fee economics from fees.py, so what
it needs to clear is on the record before it settles.

    python picks/pick.py spec.json
"""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fees
from datetime import datetime, timezone, timedelta

PM = "https://gateway.polymarket.us"
PT = timezone(timedelta(hours=-7))


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "pick/1.0", "Accept": "application/json"}), timeout=45))


def quote(q):
    return float(q["value"]) if isinstance(q, dict) and q.get("value") is not None else None


def price(slug, side):
    """(implied mid, crossing price, resting price, market).

    The crossing price is the ask -- what it costs to get in immediately. The
    resting price is the bid: what a posted limit would pay if someone sells into
    it. Crossing a coin flip costs $4.43 of EV per $100 at zero edge against
    $2.50 resting, so which one a card assumes is not a detail.
    """
    m = (get(f"{PM}/v1/markets?slug={slug}").get("markets") or [None])[0]
    if not m:
        raise SystemExit(f"no market for {slug}")
    bid, ask = quote(m.get("bestBidQuote")), quote(m.get("bestAskQuote"))
    mid = (bid + ask) / 2
    if side == "YES":
        return mid, ask, bid, m
    return 1 - mid, 1 - bid, 1 - ask, m


def main(spec_path):
    spec = json.load(open(spec_path))
    now = datetime.now(PT)
    picks = []
    for s in spec["picks"]:
        pct, cross, rest, m = price(s["market_slug"], s["market_side"])
        # "cross" (default), "mid", or "rest" (the bid). Posting at the BID fills
        # only when the price has moved further against the side bought, and that
        # showed up as adverse selection in fillsim.py: bid fills hit 24/51 =
        # 47.1% against 50.8% for the ones that never filled, while mid fills hit
        # 51.8%. Mid is the shallower, less-selected resting price.
        limit = s.get("limit")
        cost = round({"rest": rest, "mid": pct}.get(limit, cross), 4)
        picks.append({
            "game": s["game"], "league": s["league"], "kickoff_pt": s["kickoff_pt"],
            "live_when_logged": False, "decided_pregame": True,
            "pick": s["pick"], "type": s["type"],
            "market_slug": s["market_slug"], "market_side": s["market_side"],
            "resolve": s["resolve"],
            "basis": s["basis"], "rung_why": s["why"],
            "implied_win_pct": round(pct, 4), "cost": cost,
            "pays_per_100": round(100 / cost, 1),
            "entry": limit or "cross",
            # a resting order is not a bet until someone sells into it, so the
            # fill is unknown at logging time and resolved from the snapshots
            "fill": "crossed" if limit in (None, "cross") else None,
            "cross_price": round(cross, 4), "rest_price": round(rest, 4),
            **fees.economics(cost),
        })
        print(f"  {s['pick']:<30} {cost:.3f}  needs {fees.breakeven(cost):.2%}  "
              f"[{s['basis']}]")
        time.sleep(0.3)

    card = {"date": f"{now:%Y-%m-%d}", "sport": "MIXED", "venue": "polymarket",
            "rule": spec.get("rule", "Coin flips only, 47%-53% band. Researched, not dealt."),
            "seed": spec.get("seed", "none -- picks chosen for stated reasons, not tossed"),
            "priced_at_pt": f"{now:%Y-%m-%d %H:%M}",
            "note": spec.get("note", ""),
            "research": spec.get("research", {}),
            "players": {"claude": picks, "kiley": []}}
    out = spec.get("out", f"picks/{now:%Y-%m-%d}-mixed.json")
    if os.path.exists(out):                      # keep anything already logged
        old = json.load(open(out))
        card["players"]["kiley"] = old.get("players", {}).get("kiley", [])
    json.dump(card, open(out, "w"), indent=2)
    print(f"\n{len(picks)} picks -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
