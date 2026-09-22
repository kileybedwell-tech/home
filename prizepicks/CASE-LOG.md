# PrizePicks — blocked deposits and withdrawals

Running record of the dispute. Keep this updated: a dated, factual timeline is
the single most useful thing to have if this escalates to a regulator, and it is
the first thing any of them ask for.

## Facts to fill in

These are still blank. Everything downstream needs them.

- **Account email:** kileybedwell@gmail.com (assumed — confirm it matches PrizePicks)
- **Balance held in the account:** $[AMOUNT]
- **Date the problem started:** [DATE]
- **Exact error text when depositing:** [TEXT]
- **Exact error text when withdrawing:** [TEXT]
- **Does either error mention AeroPay or Aerosync?** [YES / NO]
- **Can you still see your balance and place entries?** [YES / NO]
- **Has a deposit ever bounced or been reversed?** [YES / NO]
- **Original deposit method:** [bank / PayPal / Venmo / debit card]
- **Identity verification status shown in the app:** [VERIFIED / PENDING / UNKNOWN]

## Timeline

| Date | Event | Evidence |
|---|---|---|
| [DATE] | Deposits and withdrawals both stop working | screenshot |
| 2026-09-?? | Posted to Reddit asking for help — no response | post link: [URL] |
| 2026-09-18 | Case file opened; letters drafted | this repo |
| | Email sent to AeroPay support | sent-mail copy |
| | Email sent to PrizePicks support | ticket # |
| | CFPB complaint filed | case # |

## Status

| Channel | Sent | Response due | Status |
|---|---|---|---|
| AeroPay (`support@aeropay.com`) | — | +3 business days | not sent |
| PrizePicks support | — | +5 business days | not sent |
| CFPB complaint | — | company has 15 days to respond | not filed |

## Why this order

Both directions being blocked points away from a withdrawal problem and toward
one of three account-level causes:

1. **AeroPay account flagged/disabled.** AeroPay is the bank-transfer processor
   behind PrizePicks. A failed or reversed deposit, or a broken bank link, gets
   the AeroPay account disabled — which blocks deposits *and* withdrawals at
   once. PrizePicks support cannot fix this; only AeroPay can. This is the best
   fit for the symptom, so it goes first.
2. **Identity verification (KYC) not cleared.** The account can look normal and
   still be transaction-locked pending compliance. PrizePicks' problem.
3. **Account-level restriction** — compliance hold, suspected duplicate account,
   or a state-eligibility change. PrizePicks' problem, and they generally won't
   volunteer the reason unless asked directly in writing.

Letters 1 and 2 go out together, since they test different causes. Letter 3 is
held back and filed only if neither replies within the stated window, or if
either one refuses to release the balance.

## Rules while this is open

- Don't deposit or play on the account while the balance is disputed — it
  muddies the amount being claimed.
- Keep everything in writing. Use email or the ticket system, not the chat
  widget, so each exchange is timestamped.
- Screenshot the error each time you retry, with the date visible.
- Never send full bank account or card numbers by email. Last four only.
