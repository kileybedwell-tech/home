"""Scan every ladder on today's board for an internal inconsistency.

A spread/total ladder is a cumulative distribution, so differencing adjacent rungs
must give a non-negative number. A negative one means the two rungs are quoted
against each other: buying the cheap side of both is a locked position independent
of the result. This needs no handicapping at all, so if it exists it is the only
free money on the board -- and if it doesn't, that is worth knowing too.

Checked against the executable prices (bid/ask), not the mid, so a "violation" that
is really just the spread does not count.
"""
import sys, time
sys.path.insert(0, "picks")
import scan, ladder

def ladders(ev):
    ms = ev.get("markets") or []
    for suffix in ("_team_full_game_spread", "_team_full_game_total"):
        lad = ladder.rungs(ms, suffix)
        if len(lad) >= 2:
            yield suffix, lad

def main(leagues):
    import datetime
    today = datetime.date.today()
    hits = 0
    for lg in leagues:
        try:
            games = scan.today_games(lg, today)
        except Exception as e:
            print(f"{lg}: {e}"); continue
        for g in games:
            for suffix, lad in ladders(g):
                # executable check: to be a lock, you must be able to BUY the
                # outer rung and SELL the inner one with no gap left over
                for (l1, m1, k1), (l2, m2, k2) in zip(lad, lad[1:]):
                    a1 = ladder.quote(k1.get("bestAskQuote"))
                    b2 = ladder.quote(k2.get("bestBidQuote"))
                    a2 = ladder.quote(k2.get("bestAskQuote"))
                    b1 = ladder.quote(k1.get("bestBidQuote"))
                    up = ladder.rising(lad)
                    # coherent: the further-out rung is cheaper in the ladder's direction
                    # coherence needs P(more-likely rung) >= P(less-likely rung). The lock is being
                    # able to BUY the more likely one below the SELL price of the less
                    # likely one. On a rising ladder the more likely rung is the higher
                    # line; on a falling one it is the lower.
                    lock = (b1 - a2) if up else (b2 - a1)
                    if lock > 0.005:
                        hits += 1
                        print(f"  LOCK {lg} {g.get('title','')[:38]} {suffix.split('_')[-1]} "
                              f"{l1}->{l2}  +{lock:.3f}")
                mids = [(l, m) for l, m, _ in lad]
                neg = [(lo, hi, p) for lo, hi, p in ladder.mass(lad) if p < -0.005]
                if neg:
                    print(f"  mid-cross {lg} {g.get('title','')[:38]} "
                          f"{suffix.split('_')[-1]}: " +
                          ", ".join(f"{lo}/{hi} {p:+.3f}" for lo, hi, p in neg[:4]))
            time.sleep(0.15)
    print(f"\nexecutable locks found: {hits}")

if __name__ == "__main__":
    main(sys.argv[1:] or ["nba","nhl","wnba","mlb","cfb"])
