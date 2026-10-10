"""The fee, folded into every pick instead of explained away afterwards.

The venue publishes no schedule, but every market carries feeCoefficient 0.0695,
matching the usual regulated event-contract formula

    fee = coefficient * contracts * price * (1 - price)

Staking S at price c buys S/c contracts, so the fee per dollar staked collapses to
`coefficient * (1 - c)` -- the contract count cancels. That gives three numbers
worth carrying on a pick rather than quoting once a week:

  fee_per_100         what the pick costs to place, win or lose
  breakeven_win_pct   the true probability that makes it a push
  edge_needed_pts     how far above the market's own price that sits

The third is the one that matters: edge = coefficient * c * (1 - c), which peaks at
c = 0.50. A coin flip needs the LARGEST edge of any price to break even -- about
1.74 points -- so the coin-flip rule and profitability pull against each other by
construction. That is a fact about the rule, not an excuse for a result.

Both readings of the schedule are kept: whether the fee is charged once or again on
settlement is unknown, and the two straddle zero.
"""
COEFF = 0.0695          # the taker coefficient every market carries

# Web search (2026-10-10) turned up something the gateway does not expose and that
# this model got wrong: the fee is a TAKER fee. Every third-party guide found --
# and they disagree on the rates -- agrees that MAKERS PAY ZERO, with a rebate
# around a 0.0125 coefficient on resting orders. Taker coefficients quoted range
# 0.06 to 0.0695 depending on source and on US-vs-global.
#
# This matters more than any pick: over 320 picks the record is +$56.89 gross
# against $1,089.75 of fees. The whole loss is the fee, and it was charged on the
# assumption that it applies to every order.
#
# NOT VERIFIED. polymarket.com and docs.polymarket.com are both blocked by this
# environment's network policy, the gateway serves no fee endpoint (/v1/fees is a
# 404) and no maker/taker field, so this rests on third-party pages that conflict.
# Treat `maker=True` as a hypothesis to confirm from a real statement.
MAKER_COEFF = 0.0125


def fee_per_100(cost, both_sides=False, maker=False):
    """Dollars of fee on a $100 stake at this price. Negative for a maker rebate."""
    if maker:
        return -100 * MAKER_COEFF * (1 - cost)
    f = 100 * COEFF * (1 - cost)
    return f * 2 if both_sides else f


def breakeven(cost, both_sides=False, maker=False):
    """True win probability at which a $100 stake breaks even."""
    if maker:
        return cost * (1 - MAKER_COEFF * (1 - cost))
    return cost * (1 + COEFF * (1 - cost) * (2 if both_sides else 1))


def economics(cost, both_sides=False, maker=False):
    be = breakeven(cost, both_sides, maker)
    return {"fee_per_100": round(fee_per_100(cost, both_sides, maker), 2),
            "breakeven_win_pct": round(be, 4),
            "edge_needed_pts": round((be - cost) * 100, 2),
            # if the market price is exactly right, this is what the pick returns
            "ev_per_100_if_price_is_true": round(-fee_per_100(cost, both_sides, maker), 2),
            "fee_role": "maker" if maker else "taker"}


if __name__ == "__main__":
    print(f"{'price':>7}{'fee/$100':>10}{'breakeven':>11}{'edge needed':>13}")
    for c in (0.13, 0.30, 0.40, 0.47, 0.50, 0.53, 0.60, 0.80):
        e = economics(c)
        print(f"{c:>7.2f}{e['fee_per_100']:>10.2f}{e['breakeven_win_pct']:>11.2%}"
              f"{e['edge_needed_pts']:>12.2f}p")
