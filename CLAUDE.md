# Project notes

- The user (Kiley) is in the Pacific time zone (PST/PDT). When time of day
  matters — greetings, assumptions about whether it's late, etc. — check the
  actual current time (e.g. `TZ='America/Los_Angeles' date`) rather than
  guessing from earlier context in the conversation.

- This repo contains `ebay/`, a working CLI already connected to Kiley's real
  eBay seller account (credentials and network access are provisioned for
  this environment — `python -m ebay status` confirms the connection with no
  setup needed). It manages live inventory: real listings, real money.

  Kiley's account has 1,700+ active listings, almost all created outside
  this tool (Seller Hub, File Exchange, crosslisting tools) — `python -m
  ebay listings` only shows SKUs this tool itself created (a handful), since
  that command hits the Sell Inventory API, which only knows about its own
  SKUs. It will drastically undercount what's actually live; don't take a
  low count from it as reassurance that nothing else matches.

  When Kiley sends photos of an item to list:
  1. **Before drafting anything, run `python -m ebay find "keywords"`**
     (e.g. `python -m ebay find "chatot perap"`) to check the photographed
     item against *every* active listing on the account, not just this
     tool's own. Kiley has been burned before by a session that didn't know
     to check and risked a duplicate listing. If it looks like a possible
     match, say so and ask rather than silently drafting a duplicate or
     silently assuming it's new. Try a couple of keyword variations (e.g.
     the Pokémon name, then the set/card number) since `find` needs all
     given words to appear in the title.
  2. Draft the listing (title, description, condition, item specifics,
     category via `python -m ebay categories "..."`) and show it to Kiley
     before creating anything.
  3. Create as a **draft**, not published (`create ... --draft`), using
     `--photo` for local image files. Never pass `--dry-run` when Kiley
     actually wants it created — that flag only previews the payload and
     creates nothing.
  4. Publishing is a separate, deliberate step Kiley approves explicitly
     (`python -m ebay pending` to see what's queued, `python -m ebay publish`
     to go live). Don't publish without that explicit go-ahead.
  5. **Every time a listing goes live or gets revised/relisted/merged, give
     Kiley the direct `https://www.ebay.com/itm/<id>` link so she can look
     at the actual page.** `publish` and `create --publish` (i.e. not
     `--draft`) print this automatically — just relay it. For anything done
     by hand outside the normal create/publish flow (e.g. a Trading API
     ReviseItem/EndItem to merge duplicate listings), build and share the
     link yourself since there's no command doing it automatically.

  Kiley also has a large backlog of physical items she hasn't listed yet and
  wants help staying organized. `python -m ebay backlog-add "description"`
  logs one (pure local JSON file, `inventory.json`, no eBay auth needed);
  `backlog-list [--status unlisted]` shows what's queued; `backlog-update ID
  --status listed --sku ... --item-id ...` links a backlog entry to the
  listing once it's created, so `backlog-list --status unlisted` stays an
  accurate "still needs to be listed" view. Log a new backlog item whenever
  Kiley mentions or shows something she wants listed eventually but isn't
  ready to draft right now; when a backlog item does get turned into a
  listing, update its status/sku/item-id rather than leaving it stale.
  **This file is only useful if committed** — it's tracked in git like
  everything else here, so commit and push changes to it (same as any other
  file) or the backlog is gone the next time a fresh container starts.

  **Mercari.** There is no Mercari API: Mercari US has none for sellers,
  and this environment's network policy blocks mercari.com anyway. Nothing
  here can list on, read from, or end a Mercari listing, and Kiley knows
  this. What exists instead is `python -m ebay mercari-draft
  drafts/NAME.json`, which turns a listing draft into paste-ready Mercari
  text (title/description inside Mercari's 80/1000-character limits,
  condition mapped to New/Like New/Good/Fair/Poor, photos from
  `photos/NAME/`, and a CHECK BEFORE POSTING list of anything that didn't
  carry across). When Kiley wants something on Mercari: run it, show her
  the full output, and if the description got cut, write a shorter
  Mercari-specific one under `"mercari": {"description": ...}` in the draft
  JSON (`create` ignores that block) instead of letting the tool truncate.
  She posts it in the app herself. The backlog tracks both sides:
  `--status`/`--sku`/`--item-id` are eBay, `--mercari`/`--mercari-url` are
  Mercari. `backlog-list --mercari unlisted` is "still needs Mercari";
  `mercari-draft --backlog ID` marks an item drafted; when she gives the
  Mercari link (or the `m...` id from the app's share link), run
  `backlog-update ID --mercari-url <it>`. When she says something sold on
  one site, mark it (`--status sold` or `--mercari sold`); the command
  prints a reminder if it is still live on the other, and ending that
  other listing is a separate deliberate step (eBay via this tool only
  with her explicit go-ahead; Mercari only she can do, in the app).

  **Pricing an item (sold comps).** eBay's connected APIs (Sell Inventory,
  Trading) only expose *active* listings — there is no API access to sold or
  completed listings here (that needs eBay's Marketplace Insights API,
  which requires a separate developer-portal approval Kiley hasn't applied
  for). When Kiley wants a price for something, use `WebSearch` for recent
  eBay sold listings/completed prices for that item (e.g. `<item> ebay sold
  price`) — this is the same approach used to price the Chatot Perap AR
  card. It's a web search, not a structured API, so sanity-check results
  and note the uncertainty rather than presenting a single number as exact.

  See `README.md` in this repo for the full command reference.

- **Hotel finder (`hotels/`).** `python -m hotels compare examples/vegas-quotes.json`
  is Kiley's Las Vegas shortlist with 2026 resort fees, safety, sportsbook and
  rewards ratings filled in; `travel` does drive vs fly vs bus. Booking and
  casino sites are blocked from this environment, so dated rates come from
  Kiley or from web-search snippets, never presented as exact.

  **Standing note (Sep 2026): look out for deals that beat Kiley's usual
  rate.** She joined MGM Rewards and recovered an existing Caesars Rewards
  account on 2026-09-11 and joined Best Western Rewards the same day (offers from all three
  are wanted, not spam; she is Caesars Gold). Venetian Rewards is next,
  to be joined on property right before playing (new-member spin needs
  100 points in the first 5 days). A weekly routine ("Weekly
  Vegas deals check", Mondays 9 AM PT) screens offers; it needs Gmail
  attached from the claude.ai Routines page to read member emails. When Vegas comes up,
  check for: member-rate or new-member offers at those programs, resort-fee
  or parking waivers (Caesars Diamond/Platinum, MGM Pearl), Sunday-night and
  midweek rates, no-resort-fee hotels (Casino Royale on the center Strip,
  Four Queens and Binion's downtown), and myVEGAS comp rooms. Add anything
  better than the example file's numbers to `examples/vegas-quotes.json`.

- **Sports pick'em (`picks/`).** Kiley plays a pick'em game with friends and
  compares cards with Claude. The rule they settled on is **coin flips only**
  — pick markets the book prices near 50/50, since picking heavy favorites
  isn't much of a game. Spreads and totals are coin flips by construction;
  money lines rarely are, so a money-line card is mostly chalk and she has
  said so. When she asks for picks she wants **guesses for fun**, a short
  list, not a screened edge — say the pick and move on.

  **Claude has no handicapping ability here and should not pretend to.** No
  injury news, no form, no roster knowledge (training predates the current
  season), and every sports site is blocked. Never invent records, injuries
  or trends. Picks are honest guesses; market prices are the only real input.

  **Do not bother hunting for an edge.** It was measured across a full NCAAF
  Saturday and NFL Sunday: of ~1,900 comparable markets, cross-venue
  disagreement above the bid/ask was essentially nil, and it is smaller still
  in the NFL, the most liquid board. Two traps already hit, both of which
  manufacture fake edges: a **favorite-longshot skew between the venues**
  (Kalshi prices longshots richer, favorites cheaper — stratify by price
  before believing any gap), and **games already in play**, whose prices have
  moved on what happened. Filter kickoffs.

  **Reading the ladder is the one genuinely useful trick.** A spread ladder
  is a cumulative distribution: difference adjacent rungs and you get the
  implied probability of each exact margin. In the NFL that exposes the key
  numbers — 3 and 7 carry several times the mass of neighbours — so
  half-points are not interchangeable and "nearest 50%" is a poor way to
  choose a rung.

  **Commands.** `python picks/score.py picks/<card>.json [--write]` scores a
  card; `python picks/scores.py <league|card> [--write]` fetches exact final
  scores; `python picks/track.py <card> [--loop 900]` records how ladders
  move before kickoff, which also captures the closing line a pick can be
  measured against. Card JSON is player-oriented: each pick carries its own
  `settle_ticker` and `hits_on`, so money lines, spreads and totals all score
  through one path and nothing depends on matching team names afterwards.

  **Scores: use Polymarket, not the web.** Every scoreboard host — ESPN
  (including `site.api.espn.com`), NFL.com, CBS, Fox, team sites — is 403 at
  this environment's proxy. Web-search summaries are worse than useless for
  this: they hand back **live scores labelled as finals**, which is how a
  Cowboys game that ended 37-20 got recorded as 27-13. Polymarket's event
  feed is reachable and carries per-quarter scores plus an `ended` /
  `period: FT` flag that tells a finished game from one in play. A finished
  game drops out of the league listing, so resolve it by slug:
  `/v1/events/slug/nfl-<away>-<home>-<YYYY-MM-DD>`. Kalshi settles markets
  correctly but lags the final whistle, sometimes by hours.

  **Team naming differs per league and has broken matching twice.** MLB
  `teams[].name` is the full name; NCAAF gives the *nickname* for FBS schools
  ("Nittany Lions") while the event title holds the school; NFL titles read
  "PHI Eagles vs TEN Titans" while Kalshi says "Philadelphia". Match NFL on
  abbreviations, NCAAF on school names parsed from the title. Polymarket
  spread markets also carry a **signed** line in football and an unsigned one
  in baseball, and `titleShort` names the YES team only when the line is
  negative — reading it naively produced dozens of fake 50-cent arbitrages.

  **Never quietly tilt a card.** An early spread card took the side nearest
  the 53% cap on every game, so 60 of 61 picks sat above even money and none
  below; Kiley spotted it. Choosing a rung is fine, but pick the *side* by
  seeded coin toss and record the seed so it is auditable.

  Kiley's picks are hers to log as given: log them, price them, and say
  plainly whether each one clashes with Claude's, agrees, or sits on an
  adjacent rung. She decides before kickoff and may send picks after, so
  `live_when_logged` records when Claude wrote a pick down, never when she
  chose it.
