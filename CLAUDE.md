# Project notes

- The user (Kiley) is in the Pacific time zone (PST/PDT). When time of day
  matters — greetings, assumptions about whether it's late, etc. — check the
  actual current time (e.g. `TZ='America/Los_Angeles' date`) rather than
  guessing from earlier context in the conversation. **This applies to the
  date too:** the date this environment reports is UTC, which rolls over to
  "tomorrow" at 5 PM PDT (4 PM PST). Take "today" from the Pacific-time
  `date` output, never from the environment's date line, for anything
  dated: notes, "arrives today/tomorrow", commit messages, logs.

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
  3. For a multi-item **lot**, build the group photo first with `python -m
     ebay lot-photo out.jpg <one photo per item>` and pass it as the FIRST
     `--photo`. Kiley's lot listings lead with an image showing everything
     in the lot; a single item's cover makes a five-CD lot look like one CD
     in search results. Build it rather than asking her to shoot one.
  4. Create as a **draft**, not published (`create ... --draft`), using
     `--photo` for local image files. Never pass `--dry-run` when Kiley
     actually wants it created — that flag only previews the payload and
     creates nothing.
  5. Publishing is a separate, deliberate step Kiley approves explicitly
     (`python -m ebay pending` to see what's queued, `python -m ebay publish`
     to go live). Don't publish without that explicit go-ahead.
     **Never publish without Kiley having seen and approved the shipping
     details**: service, cost to the buyer, handling time, returns. Put them
     in the draft you show her. If shipping changes after she approves (e.g.
     publish fails, or a different policy looks necessary), stop and ask
     again before publishing. Her
     go-ahead covers only what she was shown. (On 2026-09-30 a Penthouse
     listing was switched to $5 flat and published without asking.)
  6. **Every time a listing goes live or gets revised/relisted/merged, give
     Kiley the direct `https://www.ebay.com/itm/<id>` link so she can look
     at the actual page.** `publish` and `create --publish` (i.e. not
     `--draft`) print this automatically — just relay it. For anything done
     by hand outside the normal create/publish flow (e.g. a Trading API
     ReviseItem/EndItem to merge duplicate listings), build and share the
     link yourself since there's no command doing it automatically.

  **Shipping and photo rules - EVERY listing, EVERY session (cloud or
  Mac), EVERY branch.** Kiley has had to repeat these many times; sessions
  that started from an older copy of this file never saw them. Show the
  shipping (service, buyer cost, package, handling time) in every draft.

  - **NEVER list anything without buyer-paid shipping and a real package.**
    This is Kiley's hardest rule and it has been broken repeatedly, costing
    her real money: in Sep 2026 a batch of magazines, train cars and diecast
    went up on "Trading Cards Free Shipping (eBay Standard Envelope)" with no
    package, several sold, and she paid the postage herself (eBay's label
    defaulted to 1 oz, 1x1x1). Only trading cards (261328, 261329, 183454,
    183050) may use a free-shipping policy; everything else uses buyer-paid
    calculated shipping (`--fulfillment-policy 253136828026`) with a package.
    `create` and `publish` now refuse listings that break this, but that
    only covers this tool - never create listings any other way (Seller Hub
    forms, raw API calls) without setting both. After any batch, run
    `python -m ebay shipping-audit` (add `--fix` to repair); it checks every
    active listing on the account, however it was made.
  - **Always calculated, never flat rate** (Kiley, Sep 30 2026: "always
    calculated"). "Standard shipping" (flat $5) and every other flat or free
    policy are off-limits for non-cards; `create`/`publish`/`shipping-audit`
    treat them as violations.
  - **Always USPS.** Listings use buyer-paid calculated USPS Ground Advantage
    (policy 253136828026), and labels are always USPS - never FedEx or UPS,
    even when eBay shows another carrier as cheaper (Kiley, Sep 30 2026:
    "always usps").
  - **Always no returns** (Kiley, Sep 30 2026: "It's always no returns").
    Pass `--return-policy 253125003026` (No Return Accepted), never the
    30-day policy. With it: `--payment-policy 253125004026` (eBay Managed
    Payments) and `--location home` (93065); `create` refuses without them.
  - **Every listing needs a package weight and dimensions.** Kiley asked
    for this, and `create` enforces it by refusing a draft without them.
    Cards (categories 261328, 261329, 183454, 183050) default to 11 × 6 ×
    1 in, 1 oz, and magazines (280, 64488) to 15 × 10 × 2 in, 2 lb, which
    matches every live listing in those categories. Anything else needs
    `"package": {"weight_oz": ..., "length_in": ..., "width_in": ...,
    "height_in": ...}` in the draft (or `--weight-oz` and `--dimensions
    LxWxH`). Estimate from the item and similar live listings (mugs 8 × 8 ×
    8 in, 2 lb; CDs 7 × 5 × 3 in, 1 lb; video games 12 × 7 × 1 in, 8 oz),
    and show the package in the draft table for approval.
  - **Adult magazines.** Upload the photos with nudity covered by
    `./cover in.jpg out.jpg x y w h` on the Mac (fractions of the image,
    origin top left; in a cloud session black the area out with Pillow),
    and never the raw photos, on both sites.

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

  **Local sessions on Kiley's Mac are different** (check with `hostname`:
  a Mac name, and `~/Downloads` is readable). There, the working copy is
  `~/ebay-tool`, the network is not restricted, and Claude in Chrome
  drives Kiley's own logged-in Chrome. Never say Downloads or Mercari is
  out of reach without checking first. In a local session:

  - **AirDrop intake.** Kiley AirDrops item photos to the Mac.
    `python -m ebay airdrop-watch` (or a one-off `airdrop-scan`) groups
    each send into `~/Desktop/eBay Photos/New <date> <time>/`, converting
    HEIC to JPEG with originals kept in `originals/`, and logs a backlog
    item for it ("needs a draft", with the folder in its notes). When
    Kiley says "draft my new items": for each such backlog item, look at
    the JPEGs, run `find` for duplicates, then draft as above. Rename the
    folder after the item, like the existing folders, and link the backlog
    item. Batch it: Kiley lists several items a day, so show all the drafts
    in one table for approval.
  - **Posting to Mercari.** Use Claude in Chrome on mercari.com/sell with
    the `mercari-draft` output and the folder's JPEGs. Fill the form, show
    Kiley the filled form, and click List only after a clear yes. A yes
    that covers a named batch counts for those items. Then run
    `backlog-update ID --mercari-url <url>`. If a login page, CAPTCHA or
    verification appears, stop and hand it to Kiley; never type her
    password. Some drafts note the Mercari account was "under review" in
    Sep 2026, so if listing is blocked, say so plainly. On 2026-09-28
    Mercari blocked listing with "Your user privileges are limited pending
    account review" (no listing, buying, offers, chat or payouts). While
    that holds, fill the form and click **Save draft** instead of List, so
    each item sits in Drafts > Ready to list for a one-click List later. Things learned on
    the first listing: Mercari pre-fills its own title from the photos
    (replace it), turns **Smart pricing ON** by default (turn it off
    unless Kiley wants it), and has no magazine brands (use "No brand /
    Not sure"; don't let a suggested brand like Playboy stay selected).
    Pick the label weight to match the eBay weight. The Chrome extension
    can end up with two connections that each call lands on at random, so
    calls fail with "not in Claude's tab group". That's harmless; retry
    the same call until it lands. Work in one tab only, and never hand
    the task back over it.
  - **Sold sync ("check sales").** First run `python -m ebay sold-sync`.
    It reads recent eBay orders and marks matching backlog items sold (by
    item id or SKU). Next, search Gmail for Mercari sale emails
    (`from:mercari newer_than:3d`); for each sale, match it to a backlog
    item and run `backlog-update ID --mercari sold`. Then rerun
    `sold-sync` or `backlog-list` for the "take these down" list, and
    present it as one batch for a single yes. After that yes: `end-listing
    ITEM_ID --backlog ID` ends it on eBay (it works for Seller Hub listings
    too), and for Mercari, delete the listing in Chrome and run
    `backlog-update ID --mercari ended`. Double sales mean cancellations
    and seller defects, so run this often.
  - Sync only knows items in the backlog with an `--item-id`/`--sku` and a
    Mercari URL. When something goes up on both sites, make sure its
    backlog entry has both.

  **`create` and `publish` both run a price check by themselves.** They
  compare the price against comparable *active* listings via the Browse API
  and refuse to put anything live that is priced below every comparable
  found - `create` creates nothing when it refuses, and `publish` checks
  every offer in a batch before any of them go live. `create --draft`
  skips the check (a draft cannot sell), which is exactly why `publish`
  carries it too. Override with `--yes-price` only when you have actually
  checked; use `--draft` to hold it instead. `python -m ebay price-check
  "<title>" <price>` runs the same check standalone. This exists because a
  session on another device published a $150 Pokemon card at $24.99 and it
  sold within minutes.

  **Photo orientation is handled on upload.** A phone photo carries an EXIF
  tag saying how to rotate it, and eBay honours that tag - but most
  quick-look tooling renders the raw pixels and ignores it, so a photo can
  look fine while being prepared and publish sideways. Never "fix" a photo
  that looks rotated by running `sips -r` on it: that moves the pixels but
  leaves the tag, so the rotation gets applied twice and the listing goes out
  sideways (this happened to two live card listings). `upload_image` now
  normalises every photo before sending it - see `ebay/photo.py` - so pass
  photos through untouched and let it do the work.

  **Pricing an item (sold comps).** eBay's connected APIs (Sell Inventory,
  Trading) only expose *active* listings — there is no API access to sold or
  completed listings here (that needs eBay's Marketplace Insights API,
  which requires a separate developer-portal approval Kiley hasn't applied
  for). When Kiley wants a price for something, use `WebSearch` for recent
  eBay sold listings/completed prices for that item (e.g. `<item> ebay sold
  price`) — this is the same approach used to price the Chatot Perap AR
  card. It's a web search, not a structured API, so sanity-check results
  and note the uncertainty rather than presenting a single number as exact.

  **Package weight and dimensions are required on every `create`, not just
  comics/magazines.** `ListingDraft.validate()` refuses to create or publish
  a listing without `weight_oz`/`length_in`/`width_in`/`height_in` set (CLI:
  `--weight-oz`/`--weight-lb` plus `--length`/`--width`/`--height`; draft
  JSON: the same field names). This is enforced in code, not just a
  reminder here, because leaving it unset is exactly what let eBay default
  a calculated-shipping offer to roughly 1oz/1x1x1in — the buyer pays
  pennies for shipping that costs real money, and Kiley loses that
  difference on every sale. If `create` ever refuses for a missing
  weight/dimension, that is the safeguard working as intended — supply real
  numbers, never a placeholder just to get past validation. Same requirement
  and same flags on `create-auction` (`AuctionDraft` in `ebay/auction.py`) —
  that path goes live immediately with no draft step, so getting it right
  before the call matters even more there.

  **Shipping for comics and magazines: never free, never an envelope policy.**
  Use a calculated **USPS Parcel** fulfillment policy (not an eBay Standard
  Envelope or First Class Large Envelope policy, and not a free-shipping
  policy — those are for trading cards). Comics default to **12x10x1
  inches, 1 lb**; magazines default to **12x10x1 inches, 2 lb**. Adjust
  weight/dimensions up for an unusually thick single item or a multi-item
  lot. If a First-Class-Envelope policy is already on the offer, switch the
  `fulfillmentPolicyId` to a Parcel policy *before* raising the weight past
  ~13 oz — eBay validates the package weight against whatever policy is
  live at the time and will reject the update otherwise.

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
  form, no roster knowledge (training predates the current season). Never
  invent records, injuries or trends. Picks are honest guesses; market prices
  are the only real input.

  **ESPN is reachable; test before declaring a host blocked.** `nfl.com` and
  `api.nfl.com` really are 403 at the proxy, but `site.api.espn.com` AND plain
  `www.espn.com` both answer 200 (`site.web.api.espn.com` and `cdn.espn.com` are
  still 403). The stale claim that every ESPN host was blocked survived in this
  file for days and was repeated to Kiley as fact twice. A one-line curl settles
  it: `curl -s -o /dev/null -w '%{http_code}' <url>`.

  `www.espn.com/nfl/team/depth/_/name/<abbr>` gives the **depth chart**, which
  the API does not expose — the table reads Starter / 2nd / 3rd / 4th in document
  order, with the injury tag ("O") beside the name, so an injured starter still
  holds his slot and the next healthy name is the one playing. Strip the tags and
  read the row; the page's embedded `__espnfitt__` JSON does not carry it.

  On `site.api.espn.com`: `/scoreboard?seasontype=2&week=N` lists a week's games, and
  `/summary?event=<id>` carries the box score — per-player rushing, receiving,
  return and interception touchdowns, which is how the per-team TD leaders were
  built — **and an `injuries` block** with each side's status and the specific
  injury. So injuries are now checkable rather than unknowable; look before
  saying otherwise. Use curl sequentially with ~0.3s between calls: parallel
  urllib workers drew 403s, and a spoofed browser UA was rejected where curl's
  own default passed.

  None of this makes a pick better. An injury on a public report is already in
  the price days earlier, so quote it as context, never as an edge, and never
  as a reason to move off a coin flip.

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

  **Tennis and boxing are on the board too** (`atp`, `wta`, `boxing` leagues),
  and they post a match winner and nothing else. That makes a 50/50 there the
  one money line worth having: no spread to take instead, so it is not chalk.
  Their scores are games per set — "6-2, 4-6, 7-5", with the game in progress
  appended as "0-0:40-15" — so a match settles on **sets won**, never on summed
  games. `scores.py` handles this; NHL, by contrast, often posts only a money
  line with no puck line or total, so a quiet NHL slate is usually real.

  **Commands.** `python picks/card.py [leagues] [--max-per N] [--cap 0.53]`
  builds today's card; `score.py picks/<card>.json [--write]` scores it;
  `scores.py <league|card> [--write]` fetches exact final scores;
  `track.py <card> [--loop 900]` records the card's own markets until kickoff;
  `clv.py <card> [--write]` turns those snapshots into closing-line value.
  **Closing lines are captured by a routine, not a loop.** "Closing-line tick
  (pick'em)" (`trig_01FiUqtxvDfrMk7qBwY5wYFC`) fires hourly, 9 AM–9 PM PT, and
  runs `picks/tick.sh`: one snapshot of every card dated today, committed and
  pushed. A settled market quotes no price, so a close not captured before
  kickoff is gone for good, and CLV is the only measure that says anything at
  this sample size. `picks/lines-*.jsonl` is therefore **tracked in git**, not
  ignored — without that the snapshots die with the container. Do not go back to
  a `nohup track.py --loop`: both weekend loops were killed with their container
  after 10 and 22 minutes, which is why the CLV on the 2026-09-26 and -27 cards
  is measured minutes after pricing and must not be quoted. Build a card early
  enough that some ticks land before kickoff; a card logged eight minutes before
  the first game gets almost no closing line. Card JSON is
  player-oriented: each pick carries its own `market_slug`, `market_side` and
  `resolve` block, so money lines, spreads and totals score through one path
  and nothing depends on matching team names afterwards.

  **The band is not a lever. Never widen it to raise the hit rate.** Asked on
  2026-09-29 whether she wanted the 47-53% band widened so the hit rate would
  climb toward 60%, Kiley ruled it out: picking higher-probability markets to
  lift the number is cheating, because it moves the target instead of getting
  better. The rule is coin flips, and the hit rate that comes with them is ~50%
  by construction. Improvement is real but lands elsewhere: choosing the rung by
  differencing the ladder rather than by nearest-50%, capturing closing-line
  value, getting the card up in the morning so the close is measurable, and not
  repeating the logged errors (three picks priced at their own complement, spread
  sides read off titleShort, a team total taken for a game total, two trackers
  that never ran). Offer that list when asked to improve -- never a wider band,
  never a higher cap, never a quiet drift toward favorites.

  **A hit rate cannot judge these picks.** Telling a real 55% from 50% takes
  roughly 800 picks, about forty cards; a single day of 19 swings between 26%
  and 61% on variance alone (5/19 on 2026-09-25 was a 1-in-31 draw off a card
  whose mean implied probability was exactly 0.500). Report closing-line value
  beside the hit rate and treat 0 as the honest expectation.

  **Deal the directions, don't toss them.** Both sides of a market inside the
  band are coin flips, so an independent toss per market is unbiased but
  clusters: one card came out 7 unders in 10 totals, which is one bet on a
  quiet night placed seven times, and it lost as a block. `card.py` shuffles by
  the seed and then alternates over/under and take/lay, which keeps each side
  just as unbiased while forcing the counts even — same expected hit rate,
  much less swing.

  **Scores: use Polymarket, not the web.** NFL.com, CBS, Fox and team sites are
  403 at this environment's proxy (ESPN is not — see above — but Polymarket
  remains the settling source because it flags a finished game). Web-search summaries are worse than useless for
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
  spread markets are the sharpest trap of all. Every spread rung on an event
  belongs to **one ladder: YES is always `teams[0]` getting the signed `line`**
  — confirmed by the long side's `marketSides[].description`, which reads
  "+1.50" / "-1.50". `titleShort` names the *other* team whenever the line is
  positive, and always prints a minus sign, so "RUTG -41.5" is really Howard
  +41.5, the exact inversion of the pick. Never read the side off `titleShort`.
  This has bitten three times: dozens of fake 50-cent arbitrages, 85-cent fake
  NCAAF divergences, and three picks on the 2026-09-24 card logged at the
  complement of their own price.

  **Never quietly tilt a card.** An early spread card took the side nearest
  the 53% cap on every game, so 60 of 61 picks sat above even money and none
  below; Kiley spotted it. Choosing a rung is fine, but pick the *side* by
  seeded coin toss and record the seed so it is auditable.

  Kiley's picks are hers to log as given: log them, price them, and say
  plainly whether each one clashes with Claude's, agrees, or sits on an
  adjacent rung. She decides before kickoff and may send picks after, so
  `live_when_logged` records when Claude wrote a pick down, never when she
  chose it.

- **Laptop (Sep 2026).** Kiley's new 14" MacBook Pro (M5, 32GB, 1TB) is due
  2026-09-25; the old M1 MacBook Pro was kept on purpose as an always-on
  Remote Control host, not traded in. Purchase reasoning, AppleCare+ note
  and the M1 setup checklist are in `notes/macbook.md`. Tick items off there
  as they get done.
