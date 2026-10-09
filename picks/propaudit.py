"""Check the player-prop book against constraints that must hold by arithmetic.

Props are not independent markets. A player's total bases accrue only on hits, so
"at least 1 total base" and "at least 1 hit" are the SAME event. A home run is four
bases, so "at least 1 HR" cannot be likelier than "at least 4 total bases". Each
ladder must also fall as its threshold rises. None of that is a forecast -- it is
arithmetic -- so a violation is a pricing error rather than a disagreement about
baseball, and it needs no handicapping to collect.

Tested on EXECUTABLE prices. For nested events X subset-of Y, coherence needs
P(X) <= P(Y); the lock is being able to buy the likelier event Y below the price
the less likely X can be sold at, i.e. bid(X) - ask(Y) > 0. Buying YES(Y) and
NO(X) then pays at least 1 whatever happens, for less than 1.

    python picks/propaudit.py mlb-cws-cle-2026-10-10 [...more event slugs]
"""
import json, re, sys, time, urllib.request
from collections import defaultdict

PM = "https://gateway.polymarket.us"
SLUG = re.compile(r"astatc-\w+-\w+-\w+-\d{4}-\d{2}-\d{2}-([a-z]+)-([a-z]+)-gte(\d+)$")
EDGE = 0.005          # ignore anything inside half a cent: that is quoting noise


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        u, headers={"User-Agent": "propaudit/1.0", "Accept": "application/json"}),
        timeout=60))


def quote(q):
    return float(q["value"]) if isinstance(q, dict) and q.get("value") is not None else None


def book(event_slug):
    """{(player, stat, threshold): (bid, ask, question)} for one event."""
    ev = get(f"{PM}/v1/events/slug/{event_slug}")
    ev = ev.get("event") or ev
    out = {}
    for m in ev.get("markets") or []:
        mt = SLUG.match(m.get("slug") or "")
        if not mt:
            continue
        b, a = quote(m.get("bestBidQuote")), quote(m.get("bestAskQuote"))
        if b is None or a is None:
            continue
        out[(mt.group(2), mt.group(1), int(mt.group(3)))] = (b, a, m.get("question"))
    return ev.get("title"), out


def constraints(bk):
    """Yield (label, subset_key, superset_key) -- every pair where P(sub) <= P(sup)."""
    by = defaultdict(list)
    for (pl, st, n) in bk:
        by[(pl, st)].append(n)

    # 1. each ladder falls as the threshold rises
    for (pl, st), ns in by.items():
        for lo, hi in zip(sorted(ns), sorted(ns)[1:]):
            yield f"{st} ladder", (pl, st, hi), (pl, st, lo)

    for (pl, st), ns in list(by.items()):
        if st != "tb":
            continue
        # 2. total bases accrue only on hits, so tb>=1 and hits>=1 are one event:
        #    the inequality is required BOTH ways, which makes any gap a lock
        if (pl, "hits", 1) in bk and (pl, "tb", 1) in bk:
            yield "tb>=1 == hits>=1", (pl, "tb", 1), (pl, "hits", 1)
            yield "hits>=1 == tb>=1", (pl, "hits", 1), (pl, "tb", 1)
        # 3. a home run is four bases
        if (pl, "hr", 1) in bk and (pl, "tb", 4) in bk:
            yield "hr>=1 -> tb>=4", (pl, "hr", 1), (pl, "tb", 4)
        # 4. k hits give at least k bases
        for n in ns:
            if (pl, "hits", n) in bk:
                yield f"hits>={n} -> tb>={n}", (pl, "hits", n), (pl, "tb", n)

    # 5. hits+runs+RBIs is a sum, so it dominates each part
    for (pl, st) in list(by):
        if st not in ("hits", "rbi"):
            continue
        for n in by[(pl, st)]:
            if (pl, "hrr", n) in bk:
                yield f"{st}>={n} -> hrr>={n}", (pl, st, n), (pl, "hrr", n)


def main(slugs):
    locks = total = 0
    for s in slugs:
        title, bk = book(s)
        print(f"\n=== {title}   ({len(bk)} quoted prop markets)")
        n = 0
        for label, sub, sup in constraints(bk):
            if sub not in bk or sup not in bk:
                continue
            n += 1
            bid_sub, _, qs = bk[sub]
            _, ask_sup, qp = bk[sup]
            lock = bid_sub - ask_sup
            if lock > EDGE:
                locks += 1
                print(f"  LOCK +{lock:.3f}  [{label}]")
                print(f"        sell {sub[0]} {sub[1]}>={sub[2]} at {bid_sub:.3f}")
                print(f"         buy {sup[0]} {sup[1]}>={sup[2]} at {ask_sup:.3f}")
        total += n
        print(f"  {n} constraint pairs checked")
        time.sleep(0.3)
    print(f"\n{total} constraints checked across {len(slugs)} event(s): "
          f"{locks} executable violation(s)")


if __name__ == "__main__":
    main(sys.argv[1:] or ["mlb-cws-cle-2026-10-10"])
