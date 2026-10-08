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
COEFF = 0.0695


def fee_per_100(cost, both_sides=False):
    """Dollars of fee on a $100 stake at this price."""
    f = 100 * COEFF * (1 - cost)
    return f * 2 if both_sides else f


def breakeven(cost, both_sides=False):
    """True win probability at which a $100 stake breaks even."""
    return cost * (1 + COEFF * (1 - cost) * (2 if both_sides else 1))


def economics(cost, both_sides=False):
    be = breakeven(cost, both_sides)
    return {"fee_per_100": round(fee_per_100(cost, both_sides), 2),
            "breakeven_win_pct": round(be, 4),
            "edge_needed_pts": round((be - cost) * 100, 2),
            # if the market price is exactly right, this is what the pick returns
            "ev_per_100_if_price_is_true": round(-fee_per_100(cost, both_sides), 2)}


if __name__ == "__main__":
    print(f"{'price':>7}{'fee/$100':>10}{'breakeven':>11}{'edge needed':>13}")
    for c in (0.13, 0.30, 0.40, 0.47, 0.50, 0.53, 0.60, 0.80):
        e = economics(c)
        print(f"{c:>7.2f}{e['fee_per_100']:>10.2f}{e['breakeven_win_pct']:>11.2%}"
              f"{e['edge_needed_pts']:>12.2f}p")
