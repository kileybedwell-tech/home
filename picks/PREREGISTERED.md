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

---

## Amendment, 2026-10-09: a validity gate, added after the rule fired wrongly

The 2023-24 NHL sample **passed all three criteria above** and is still not a bet.
Unibet's `pickcenter` total reads 5.5 on **1253 of 1312 games (95.5%)**. A book
that posts the same total whatever the matchup is not forecasting, and the 57.3%
over rate is arithmetic rather than inefficiency: 2023-24 scoring averaged 6.23
goals, and the share of games clearing a FIXED 5.5 line is 752/1312 = 57.3%, the
audit's number to the decimal. Measuring that is measuring league scoring, not a
mispriced market.

The three criteria screen statistical noise. They assumed, without saying so,
that the input was a real market line. So a fourth condition, required before any
rung counts:

4. **The line has to move.** No single posted number may account for more than
   **80%** of the sample, and the book must be one quoted on the venue actually
   being bet. A rung from a feed pinned to one value is void however large n is,
   and a bias in a book nobody here can bet into is not an edge either.

For contrast, 2025-26 DraftKings splits 60% / 40% across 6.5 and 5.5 — a line
that moves — so that sample is valid, and it read 50.1% over at the 6.5 rung
(n=563). The honest result stands; the exciting one does not.

Recording this because the rule worked exactly as intended and still would have
produced a losing bet. Pre-registration stops a threshold being fitted to noise.
It does not check that the data means what it is assumed to mean.
