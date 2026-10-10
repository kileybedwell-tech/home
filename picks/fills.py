"""Resolve whether a resting limit order actually filled, from the snapshots.

A pick entered by crossing the spread is a bet the moment it is logged. A pick
POSTED at the bid is not: it becomes a bet only when someone sells into it. That
shows up in the tracker as the ask coming down to the limit price or below.

So an unfilled pick is NO BET -- not a loss, not a win, and no fee. Scoring it as
a miss would be the most expensive bookkeeping error available, and counting it as
a bet that never happened would flatter the record. It gets `fill: "unfilled"` and
is excluded from the P&L.

A fill here is an UPPER bound: a later ask at the limit proves a willing seller
existed, not that this order was at the front of the queue.

    python picks/fills.py picks/2026-10-10-mixed.json [--write]
"""
import json, os, sys


def ticks(path, date):
    p = os.path.join(os.path.dirname(path) or ".", f"lines-{date}.jsonl")
    out = {}
    if not os.path.exists(p):
        return out
    for line in open(p):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "market_slug" not in r:
            continue
        out.setdefault(r["market_slug"], []).append(r)
    for v in out.values():
        v.sort(key=lambda r: r["at"])
    return out


def resolve(pick, snaps):
    """'filled' / 'unfilled' / None when there is nothing to judge from."""
    if pick.get("entry") in (None, "cross"):
        return "crossed"
    rows = snaps.get(pick.get("market_slug")) or []
    kick = (pick.get("kickoff_pt") or "").replace(" ", "T")[:16]
    pre = [r for r in rows if r["at"][:16] < kick]
    if len(pre) < 2:
        return None
    limit = pick["cost"]
    no = pick.get("market_side") == "NO"
    for r in pre[1:]:
        # the price a seller would hit on my side of the book
        avail = round(1 - r["bid"] if no else r["ask"], 4)
        if avail <= limit + 1e-9:
            return "filled"
    return "unfilled"


def main(path, write=False):
    card = json.load(open(path))
    snaps = ticks(path, card.get("date", ""))
    for who, picks in card.get("players", {}).items():
        rest = [p for p in picks if p.get("entry") == "rest"]
        if not rest:
            continue
        print(f"=== {who}: {len(rest)} resting order(s)")
        for p in rest:
            got = resolve(p, snaps)
            if write and got:
                p["fill"] = got
            tag = {"filled": "FILLED  ", "unfilled": "no fill ",
                   None: "undecided"}.get(got, got)
            print(f"  {tag} {p['pick']:<28} limit {p['cost']:.3f}  "
                  f"(crossing would have cost {p.get('cross_price')})")
        f = sum(1 for p in rest if p.get("fill") == "filled")
        u = sum(1 for p in rest if p.get("fill") == "unfilled")
        print(f"  -> {f} filled, {u} no fill; the unfilled ones are NO BET "
              f"and carry no fee")
    if write:
        json.dump(card, open(path, "w"), indent=2)
        print(f"\nwrote fills to {path}")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    main(a[0], "--write" in sys.argv)
