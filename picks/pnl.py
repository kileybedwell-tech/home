"""What the cards would have returned at a flat stake, card by card and running.

The hit rate hides the thing that decides profit: the price paid. 53% is a win at
51c and a loss at 55c. This values every settled pick at what it actually cost.

    python picks/pnl.py                  # every card, plus a running total
    python picks/pnl.py --stake 50       # a different flat stake (default 100)
    python picks/pnl.py --both-sides     # charge the fee again on a settled winner
    python picks/pnl.py --write          # store each card's own line in its JSON

FEES. The venue publishes no schedule -- /v1/fees is a 404 and the config states
none -- but every market carries `feeCoefficient` (0.0695), which matches the
standard regulated event-contract formula:

    fee = coefficient x contracts x price x (1 - price)

What is NOT known is whether it is charged once on entry or again when a winner
settles, so --both-sides gives the pessimistic reading and the two straddle zero.
Note the formula peaks at exactly 50c: the coin-flip rule puts every pick on the
maximum-fee point of the curve by construction, about 3.4c per $100 staked.
Treat these numbers as an estimate whose fee half is assumed, not quoted.
"""
import json, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fees import COEFF


def settled(card):
    for p in card.get("players", {}).get("claude", []):
        if p.get("result") not in ("hit", "miss", "push") or not p.get("cost"):
            continue
        # A resting limit order that nobody sold into is NO BET: no stake, no
        # payout, no fee. Counting it as a loss would be the most expensive
        # bookkeeping error here; counting it as a win would flatter the record.
        if p.get("entry") in ("rest", "mid") and p.get("fill") != "filled":
            continue
        yield p


def value(pick, stake, both_sides):
    cost = float(pick["cost"])
    shares = stake / cost
    res = pick["result"]
    payout = shares if res == "hit" else (stake if res == "push" else 0.0)
    fee = COEFF * shares * cost * (1 - cost)
    if both_sides and res == "hit":
        fee *= 2
    return payout - stake, fee


def main():
    argv = sys.argv[1:]
    def opt(flag, default):
        return type(default)(argv[argv.index(flag) + 1]) if flag in argv else default
    stake = opt("--stake", 100.0)
    both = "--both-sides" in argv
    run_gross = run_fees = run_staked = 0.0
    run_hits = run_n = 0
    clv_sum = 0.0; clv_n = 0
    print(f"  ${stake:,.0f} a pick, fee = {COEFF} x contracts x P x (1-P)"
          f"{' on entry and settlement' if both else ' on entry'}\n")
    print(f"  {'date':<12}{'picks':>6}{'hit':>6}{'gross':>11}{'fees':>9}"
          f"{'net':>11}{'running':>12}{'CLV':>8}{'CLV run':>9}")
    for f in sorted(glob.glob("picks/2026-*.json")):
        card = json.load(open(f))
        picks = list(settled(card))
        if not picks:
            continue
        g = fee = 0.0
        for p in picks:
            dg, df = value(p, stake, both)
            g += dg; fee += df
        hits = sum(1 for p in picks if p["result"] == "hit")
        run_gross += g; run_fees += fee; run_staked += stake * len(picks)
        run_hits += hits; run_n += len(picks)
        if "--write" in argv:
            card["pnl"] = {"stake": stake, "picks": len(picks), "hits": hits,
                           "gross": round(g, 2), "fees": round(fee, 2),
                           "net": round(g - fee, 2),
                           "fee_model": f"{COEFF} x contracts x P x (1-P), "
                                        + ("entry and settlement" if both else "entry only"),
                           "running_net": round(run_gross - run_fees, 2)}
            json.dump(card, open(f, "w"), indent=2)
        # closing-line value, where the card has been priced against a real close
        day = [q["clv"] for q in picks if q.get("clv") is not None]
        if day:
            clv_sum += sum(day); clv_n += len(day)
        c = f"{sum(day)/len(day)*100:+.2f}" if day else "  --"
        r = f"{clv_sum/clv_n*100:+.2f}" if clv_n else "  --"
        print(f"  {f[6:16]:<12}{len(picks):>6}{hits:>6}{g:>+11,.2f}{fee:>9,.2f}"
              f"{g-fee:>+11,.2f}{run_gross-run_fees:>+12,.2f}{c:>8}{r:>9}")
    net = run_gross - run_fees
    print(f"\n  {run_n} settled picks, ${run_staked:,.0f} staked")
    print(f"  gross {run_gross:+,.2f}   fees {run_fees:,.2f}   net {net:+,.2f}"
          f"   ({net/run_staked:+.2%} of turnover)")
    print(f"  hit {run_hits}/{run_n} = {run_hits/run_n:.1%}")
    if clv_n:
        print(f"  CLV {clv_sum/clv_n*100:+.2f}c over {clv_n} picks measured against a"
              f" real close ({clv_n/run_n:.0%} of them)")
    if "--write" in argv:
        print("\n  wrote a pnl block to each card")


if __name__ == "__main__":
    main()
