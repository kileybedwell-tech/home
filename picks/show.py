"""Print a card from the file it was written to, never from a builder's stdout.

Reporting a card by reading the build output has gone wrong three times -- a
`tail` hid an NFL game, a `head` truncated a script before its write, a `tail`
dropped two tennis picks from a card reported as 18 when it held 20. The file is
the record; this reads it, counts it, and says so.

    python picks/show.py                      # today's card
    python picks/show.py picks/<card>.json
"""
import json, os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fees
from collections import Counter
from datetime import datetime, timezone, timedelta

PT = timezone(timedelta(hours=-7))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else f"picks/{datetime.now(PT):%Y-%m-%d}-mixed.json"
    card = json.load(open(path))
    picks = card["players"]["claude"]
    print(f"{path}   seed {card.get('seed','?')}   priced {card.get('priced_at_pt','?')}\n")
    for i, p in enumerate(picks, 1):
        res = p.get("result")
        mark = {"hit": "HIT ", "miss": "miss", "push": "push"}.get(res, "    ")
        print(f"  {i:>2}. {mark} {p['league']:<6}{p['kickoff_pt'][11:]}  "
              f"{p['pick']:<26}{p['implied_win_pct']:.0%}  "
              f"be {fees.breakeven(p['cost']):.1%}  "
              f"fee {fees.fee_per_100(p['cost']):>4.2f}  {p['game'][:30]}")
    n = len(picks)
    o = sum(1 for p in picks if p["pick"].startswith("OVER"))
    u = sum(1 for p in picks if p["pick"].startswith("UNDER"))
    sp = [p for p in picks if p["type"] == "spread"]
    tk = sum(1 for p in sp if p["resolve"]["line"] > 0)
    lg = Counter(p["league"] for p in picks)
    print(f"\n  {n} picks in the file -- that number is the card, not whatever a "
          f"terminal happened to show")
    print(f"  {o} over / {u} under, {tk} taking / {len(sp)-tk} laying")
    print("  " + ", ".join(f"{k} {v}" for k, v in lg.most_common()))
    # the fee is a known cost of placing the card, so state it up front
    tf = sum(fees.fee_per_100(p["cost"]) for p in picks)
    need = sum(fees.breakeven(p["cost"]) for p in picks)
    held = sum(p["cost"] for p in picks)
    print(f"  at $100 a pick: ${n*100:,} staked, ${tf:,.2f} of fee to place it")
    print(f"  average price {held/n:.1%}, break-even {need/n:.2%} -- "
          f"needs {(need-held)/n*100:.2f} points over the market to profit")
    done = [p for p in picks if p.get("result") in ("hit", "miss")]
    if done:
        h = sum(1 for p in done if p["result"] == "hit")
        print(f"  scored {h}/{len(done)}")


if __name__ == "__main__":
    main()
