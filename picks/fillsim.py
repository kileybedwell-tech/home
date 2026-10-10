"""Would a resting limit order have filled before kickoff, instead of paying the ask?

Every pick on every card is recorded at the ASK -- cost = ask -- which assumes
crossing the spread to get in. That is a real cost I have been treating as fixed:
the half-spread is 0.25 points on a game spread and 4 points on an MLB prop.

A buy posted at price L fills when someone sells into it, which shows up in the
snapshots as the ask coming down to L or below. That is an UPPER bound on fills:
seeing a willing seller at L does not prove my order was at the front of the
queue at L. Treat it as the best case.
"""
import json, glob, os
from collections import defaultdict

def ticks(date):
    p = f"picks/lines-{date}.jsonl"
    by = defaultdict(list)
    if not os.path.exists(p): return by
    for line in open(p):
        try: r = json.loads(line)
        except ValueError: continue
        if "market_slug" not in r: continue          # the 2026-09-20 file predates this schema
        by[r["market_slug"]].append(r)
    for v in by.values(): v.sort(key=lambda r: r["at"])
    return by

rows = []
for f in sorted(glob.glob("picks/2026-*.json")):
    card = json.load(open(f))
    by = ticks(card.get("date",""))
    if not by: continue
    for p in card["players"].get("claude", []):
        t = by.get(p.get("market_slug")) or []
        kick = (p.get("kickoff_pt") or "").replace(" ","T")[:16]
        pre = [r for r in t if r["at"][:16] < kick]
        if len(pre) < 2: continue
        first = pre[0]
        side_no = p.get("market_side") == "NO"
        # my entry price, and the two cheaper limits I could have posted instead
        entry = p["cost"]
        mid  = round(1-first["mid"] if side_no else first["mid"], 4)
        bid  = round(1-first["ask"] if side_no else first["bid"], 4)
        # later asks on MY side of the book
        later = [round(1-r["bid"] if side_no else r["ask"], 4) for r in pre[1:]]
        if not later: continue
        rows.append({"entry": entry, "mid": mid, "bid": bid,
                     "fill_mid": any(a <= mid + 1e-9 for a in later),
                     "fill_bid": any(a <= bid + 1e-9 for a in later),
                     "result": p.get("result"), "n_later": len(later)})

n = len(rows)
print(f"{n} picks with at least two pre-kickoff snapshots\n")
for label, key, pk in (("posted at MID", "fill_mid", "mid"),
                       ("posted at BID", "fill_bid", "bid")):
    got = [r for r in rows if r[key]]
    save = sum(r["entry"] - r[pk] for r in got)
    print(f"{label}: would have filled on {len(got)}/{n} = {len(got)/n:.0%}")
    print(f"   price saved on those fills: {save*100:.2f} cents total, "
          f"{(save/len(got)*100 if got else 0):.2f}c average")
    # what it is worth: on a $100 stake, entering lower raises the payout
    gain = 0.0
    for r in got:
        if r["result"] not in ("hit","miss"): continue
        old = (100/r["entry"] - 100) if r["result"]=="hit" else -100
        new = (100/r[pk]   - 100) if r["result"]=="hit" else -100
        gain += new - old
    print(f"   P&L difference on the settled ones: {gain:+,.2f} at $100 a pick\n")

print("=== the part that matters: not filling is not a loss, it is no bet")
import sys; sys.path.insert(0,"picks"); import fees
def pnl(rs, pricekey):
    g=fee=0.0; h=0; n=0
    for r in rs:
        if r["result"] not in ("hit","miss"): continue
        c=r[pricekey]; n+=1; h+= r["result"]=="hit"
        g += (100/c - 100) if r["result"]=="hit" else -100
        fee += fees.fee_per_100(c)
    return n,h,g,fee
for label,key,pk in (("ALL 171, crossing to the ask","__all__","entry"),
                     ("only the MID fills, at mid","fill_mid","mid"),
                     ("only the BID fills, at bid","fill_bid","bid")):
    rs = rows if key=="__all__" else [r for r in rows if r[key]]
    n,h,g,fee = pnl(rs, pk)
    print(f"  {label:<32} n={n:<4} hit {h}/{n} = {h/n if n else 0:.1%}  "
          f"gross {g:+9.2f}  fees {fee:7.2f}  net {g-fee:+9.2f}")

print("\n=== adverse selection check: a buy fills when the price is FALLING,")
print("    i.e. exactly when the market is moving against the side bought")
for key,lbl in (("fill_bid","filled at bid"),):
    fl=[r for r in rows if r[key] and r["result"] in ("hit","miss")]
    nf=[r for r in rows if not r[key] and r["result"] in ("hit","miss")]
    for rs,name in ((fl,lbl),(nf,"did NOT fill")):
        h=sum(1 for r in rs if r["result"]=="hit")
        print(f"   {name:<16} n={len(rs):<4} hit {h}/{len(rs)} = {h/len(rs) if rs else 0:.1%}")

print("\n=== decomposition: is the gain the PRICE, or just making fewer bets?")
mf=[r for r in rows if r["fill_mid"]]
n,h,g,fee = pnl(mf,"entry");  print(f"  the same 56 picks, but paying the ASK:  net {g-fee:+9.2f}  (hit {h}/{n})")
n,h,g,fee = pnl(mf,"mid");    print(f"  the same 56 picks, filled at MID:       net {g-fee:+9.2f}")
print("  -> the difference between those two lines is the PRICE effect alone.")
import math
h2=sum(1 for r in mf if r["result"]=="hit"); n2=sum(1 for r in mf if r["result"] in ("hit","miss"))
p=h2/n2; se=math.sqrt(p*(1-p)/n2)
print(f"\n  but the hit rate on them is {p:.1%} +-{1.96*se:.1%} (95%) -- 50% is inside that,")
print(f"  so the positive net is not yet distinguishable from a lucky {n2}-pick slice.")
