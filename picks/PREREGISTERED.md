# Pre-registered decision rule for the NHL total audit

Written **before** looking at `picks/lineaudit.py nhl 2025-10-07 2026-04-16`
output, so the threshold cannot be fitted to the result after the fact. Yesterday
I nearly took an NBA-preseason "edge" that only existed because I compared a
league average to a game's own posted total; the guard against that class of
error is deciding what counts as a finding in advance.

## The test

For each posted total L (DraftKings, recovered per game from ESPN
`summary?event=<id>` -> `pickcenter`), among finished 2025-26 NHL games where the
book posted exactly L, what fraction went over?

A line that is honest reads 50%. This conditions on the line itself, so it is
not the conditioning error above.

## What counts as actionable

A rung qualifies only if ALL THREE hold:

1. **n >= 100** graded games at that rung.
2. The over rate differs from 50% by **more than 1.96 standard errors** —
   i.e. the 95% interval excludes 50%.
3. The gap from 50% **exceeds the edge the fee demands**, which is
   `0.0695 * c * (1 - c)` = **1.74 points** at a 50c price (see picks/fees.py).
   Clearing significance but not the fee is not a bet.

If a rung qualifies, take that side on today's games posted at that rung, tagged
`basis: "line audit"`. If no rung qualifies, **take no NHL pick from this** and
say the audit came back empty.

## What this cannot show

`pickcenter` does not say whether its number is the opening or the closing line.
If it is an opening line, a bias found here may have been corrected by close and
would not have been available to bet. Treat a positive result as a lead to
re-test against captured closing prices, not as a proven edge.

Only 2025-26 is in scope — one season, so a qualifying rung is a hypothesis for
next season to confirm, not a law.
