"""Difference a ladder into per-margin probability, and choose a rung with it.

A spread ladder is a cumulative distribution: the price of "team +L" is the
probability the margin lands above L. Difference adjacent rungs and you get the
probability of each exact margin band. In football that exposes the key numbers --
3 and 7 carry several times the mass of their neighbours -- so two rungs half a
point apart are not interchangeable even when both are priced near even.

The practical consequence is about WHICH side of a spike a rung sits on. A rung
that collects a spike and one that just misses it can both sit inside the band,
and the one that collects it is the better rung at the same price. Picking by
"nearest 50%" is blind to that; this is not.

This buys no edge -- the spike is already in the price. It avoids being a hair on
the wrong side of a number for no reason.

    python picks/ladder.py nfl              # every game's ladder, with the mass
    python picks/ladder.py nfl --band 0.53  # and which rung it would choose
"""
import json, sys, urllib.request

PM = "https://gateway.polymarket.us"


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "ladder/1.0", "Accept": "application/json"}), timeout=45))


def quote(q):
    return float(q["value"]) if isinstance(q, dict) and q.get("value") is not None else None


def rungs(markets, suffix):
    """[(line, mid, market)] sorted by line -- one coherent ladder for teams[0]."""
    out = []
    for m in markets or []:
        if not (m.get("sportsMarketType") or "").lower().endswith(suffix):
            continue
        bid, ask = quote(m.get("bestBidQuote")), quote(m.get("bestAskQuote"))
        if bid is None or ask is None or m.get("line") is None or ask - bid > 0.04:
            continue
        out.append((float(m["line"]), (bid + ask) / 2, m))
    return sorted(out)


def rising(lad):
    """A spread ladder climbs with the line (more points, more likely); a total
    ladder falls (a higher line is harder to go over). Read it off the ends rather
    than assuming, so the same differencing serves both."""
    return len(lad) < 2 or lad[-1][1] >= lad[0][1]


def mass(lad):
    """[(lo, hi, p)] -- probability the result lands strictly between two rungs.

    The gap between adjacent rungs is the mass of the outcomes between them,
    signed so that p is positive for a coherent ladder in either direction. A
    negative value means those two rungs are quoted inconsistently; it is left
    visible rather than smoothed away."""
    up = rising(lad)
    return [(lad[i][0], lad[i + 1][0],
             (lad[i + 1][1] - lad[i][1]) if up else (lad[i][1] - lad[i + 1][1]))
            for i in range(len(lad) - 1)]


def spikes(lad, factor=2.5):
    """Gaps carrying `factor` times the median gap -- the key numbers, found in the
    prices rather than assumed from the sport."""
    gaps = mass(lad)
    sizes = sorted(abs(p) for _, _, p in gaps)
    if not sizes:
        return []
    med = sizes[len(sizes) // 2] or 1e-9
    return [(lo, hi, p) for lo, hi, p in gaps if p > factor * med]


def choose(lad, floor, cap):
    """The rung to take, among those inside the band.

    Prefer a rung that sits ABOVE a spike on this ladder -- one whose price already
    includes that bundle of outcomes -- over one sitting just below it, which loses
    them. Among equals, fall back to nearest even money. Returns (line, mid, market,
    why)."""
    inband = [(L, p, m) for L, p, m in lad if floor <= p <= cap]
    if not inband:
        return None
    sp = spikes(lad)
    def score(item):
        L, p, _ = item
        # spikes already inside this rung's price, against those it gives up
        kept = sum(mp for lo, hi, mp in sp if (hi <= L) == rising(lad))
        lost = sum(mp for lo, hi, mp in sp if (hi <= L) != rising(lad))
        return (-(kept - lost), abs(p - 0.5))
    best = min(inband, key=score)
    L, p, m = best
    up = rising(lad)
    def fmt(xs):
        return ", ".join(f"{lo:+g}..{hi:+g} ({mp*100:.1f}pts)" for lo, hi, mp in xs)
    near = [(lo, hi, mp) for lo, hi, mp in sp if abs(lo - L) <= 3 or abs(hi - L) <= 3]
    kept = [x for x in near if (x[1] <= L) == up]
    lost = [x for x in near if (x[1] <= L) != up]
    bits = ([f"collects {fmt(kept)}"] if kept else []) + ([f"gives up {fmt(lost)}"] if lost else [])
    return L, p, m, ("; ".join(bits) if bits
                     else "no key number within 3 points; nearest even money")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    league = args[0] if args else "nfl"
    cap = float(sys.argv[sys.argv.index("--band") + 1]) if "--band" in sys.argv else 0.53
    d = get(f"{PM}/v2/leagues/{league}/events?limit=100&offset=0&type=sport&section=general")
    for e in (d.get("events") or [])[:12]:
        full = get(f"{PM}/v1/events/{e['id']}").get("event") or e
        ab = [(t.get("displayAbbreviation") or "").upper() for t in full.get("teams", [])]
        lad = rungs(full.get("markets"), "_team_full_game_spread")
        if len(lad) < 4:
            continue
        print(f"\n== {full.get('title')}   ladder on {ab[0] if ab else '?'}")
        for lo, hi, p in mass(lad):
            bar = "#" * int(max(0, p) * 200)
            print(f"   {lo:+6g} .. {hi:+6g}   {p*100:+5.1f}pts  {bar}")
        got = choose(lad, 1 - cap, cap)
        print(f"   -> take {ab[0]} {got[0]:+g} at {got[1]:.0%}: {got[3]}" if got
              else "   -> nothing in band")


if __name__ == "__main__":
    main()
