"""Closing-line value: did a pick beat the price the market settled into?

A hit rate cannot judge coin-flip picks. Distinguishing a real 55% from 50% takes
roughly 800 picks -- forty days of cards -- so on any one day it says nothing.
Closing-line value says something much sooner: if the market drifted toward a pick
after it was logged, the pick was taken at a better price than the close, whatever
the result. It is the one measure here that separates the pick from the bounce.

    python picks/clv.py picks/2026-09-26-mixed.json [--write]

Needs picks/lines-<date>.jsonl from track.py, since a settled market no longer
quotes a price. The last snapshot before kickoff is the close.
"""
import json, os, sys
from collections import defaultdict


def closings(path):
    """The final snapshot recorded for each market: {market_slug: implied mid}."""
    last = {}
    if not os.path.exists(path):
        return last
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        prev = last.get(r["market_slug"])
        if prev is None or r["at"] >= prev["at"]:
            last[r["market_slug"]] = r
    return {k: v["mid"] for k, v in last.items()}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: clv.py picks/<card>.json [--write]")
    path = args[0]
    card = json.load(open(path))
    close = closings(os.path.join(os.path.dirname(path) or ".",
                                  f"lines-{card.get('date', 'card')}.jsonl"))
    if not close:
        sys.exit("no lines-<date>.jsonl for this card — track.py has to run before kickoff")
    for who, picks in card.get("players", {}).items():
        rows, missing = [], 0
        for p in picks:
            mid = close.get(p.get("market_slug"))
            if mid is None:
                missing += 1
                continue
            # the snapshot is the YES mid; a NO pick closes at its complement
            shut = round(1 - mid if p.get("market_side") == "NO" else mid, 4)
            clv = round(shut - p["implied_win_pct"], 4)
            if "--write" in sys.argv:
                p["closing_pct"], p["clv"] = shut, clv
            rows.append((p, shut, clv))
        if not rows:
            continue
        print(f"=== {who} ({len(rows)} priced, {missing} without a close)")
        for p, shut, clv in sorted(rows, key=lambda r: -r[2]):
            print(f"  {clv*100:+5.1f}c  {p['pick']:<26} {p['implied_win_pct']:.0%}"
                  f" -> {shut:.0%}   {p.get('result') or 'pending'}")
        beat = sum(1 for _, _, c in rows if c > 0)
        mean = sum(c for _, _, c in rows) / len(rows)
        print(f"  -> mean CLV {mean*100:+.2f}c, beat the close on {beat}/{len(rows)}")
        print("     (0 is what a coin flip with no information should average)")
    if "--write" in sys.argv:
        json.dump(card, open(path, "w"), indent=2)
        print(f"\nwrote closing prices to {path}")


if __name__ == "__main__":
    main()
