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
    1 in, 1 oz, magazines to 15 × 10 × 2 in, 2 lb, and single comics to 12 × 10 × 1 in,
    1 lb (Kiley, Oct 9 2026). Never give those packages to anything else. Anything else needs
    `"package": {"weight_oz": ..., "length_in": ..., "width_in": ...,
    "height_in": ...}` in the draft (or `--weight-oz` and `--dimensions
    LxWxH`). Estimate from the item and similar live listings (mugs 8 × 8 ×
    8 in, 2 lb; CDs 7 × 5 × 3 in, 1 lb; video games 12 × 7 × 1 in, 8 oz),
    and show the package in the draft table for approval.
  - **Weights in pounds, never ounces** (Kiley, Oct 10 2026: "Don't use
    ounces"). Write `"weight_lb"` in drafts and show the package as lb in
    every draft table (e.g. 12 x 10 x 2 in, 2 lb). Lots of comics: 2 lb.
  - **Magazines are always complete with centerfold** (Kiley, Sep 30 2026:
    "Everything is complete so stop asking that"). Put "Complete w/
    Centerfold" in the title and description from the first draft; never
    ask. "Complete" covers posters and other inserts too (Oct 1 2026: "I
    thought I told you already that everything is complete"): when a cover
    advertises a poster, list it as included, price it that way, and
    never ask whether it is there.
  - **Never put "Erotic" in a title** (Kiley, Sep 30 2026: it makes a
    takedown more likely). Pick other cover lines for the title. Keep the
    descriptions clean too: no erotic, orgasm, nude, sex or porn, even
    when quoting the cover.
  - **Dating Playboy newsstand specials.** The cover code "38580 0MY" gives
    month and last digit of the year (38580 034 = 1984, 016 = 1986, 075 =
    1985, 093 = 1983; Kiley confirmed the first three). Use it instead of
    asking her for the year.
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
  policy — those are for trading cards). Magazines use **15x10x2 inches,
  2 lb** (Kiley, Oct 1 2026: "Only magazines and comics should have the
  15x10x2 and 2 lbs"); single comics use **12x10x1 inches, 1 lb** (Kiley,
  Oct 9 2026: "1 lb"). Nothing else gets those packages:
  dolls, CDs, books and programs keep their own size and weight. Adjust up
  only for a multi-item lot. If a First-Class-Envelope policy is already on the offer, switch the
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

  **Reading the ladder is the one genuinely useful trick, and `picks/ladder.py`
  now does it.** A spread ladder is a cumulative distribution: difference
  adjacent rungs and you get the implied probability of each exact margin. In
  the NFL that shows up plainly in live prices -- a real Buccaneers/Cowboys
  ladder put 8.0 points on +6.5..+7.5 and 7.0 on +2.5..+3.5 against a ~2-point
  median -- so half-points are not interchangeable. `card.py` picks the spread
  and total rung through `ladder.choose()`, preferring a rung that already
  collects a nearby spike over one that gives it up, and records the reason on
  the pick as `rung_why`. `mass()` reads the ladder's direction off its ends, so
  the same differencing serves spreads (rising) and totals (falling).

  **Measured honestly, it changes almost nothing at this band**, and that is
  worth knowing before anyone credits it: across the NFL board it chose the same
  rung as plain nearest-50% in 11 of 11 games, because 47-53% admits only ~1.4
  rungs per game and there is nothing to choose between. It starts to bite at
  2.0 rungs (differs on 2 of 15 at 45-55%) and clearly at 3.5 rungs (8 of 15 at
  42-58%). Basketball and hockey have no key numbers at all and correctly fall
  through to nearest even money. So it is a correct tie-break that rarely
  triggers, not an improvement to claim.

  **Soccer is on the board as well** (`epl`, `ucl`, `mls`; La Liga, Serie A,
  Bundesliga and Ligue 1 list nothing). Spreads and totals use the same
  structure as every other sport -- `soccer_team_full_game_spread` on `teams[0]`
  with the signed line, `soccer_team_full_game_total` Over/Under -- so the
  builder needed no new logic, only the league names. Lines are all half-goals,
  so nothing pushes. The **winner market is three-way** (team / Tie / team, each
  quoted separately as its own Yes/No), which means NO on one side is not the
  opposite of YES: it is excluded automatically because it is named
  `soccer_team_full_time_winner`, not `full_game_winner`. Keep it that way.
  Settlement has NOT been verified against a finished match yet -- check the
  first one that scores before trusting a soccer result.

  **Tennis and boxing are on the board too** (`atp`, `wta`, `boxing` leagues),
  and they post a match winner and nothing else. That makes a 50/50 there the
  one money line worth having: no spread to take instead, so it is not chalk.
  Their scores are games per set — "6-2, 4-6, 7-5", with the game in progress
  appended as "0-0:40-15" — so a match settles on **sets won**, never on summed
  games. `scores.py` handles this; NHL, by contrast, often posts only a money
  line with no puck line or total, so a quiet NHL slate is usually real.

  **Commands.** `python picks/card.py [leagues] [--max-per N] [--cap 0.53]`
  builds today's card; **`show.py [card]` prints it back from the file and must
  be what a card is reported from** -- never the builder's stdout. Reading a
  truncated terminal view has misreported reality three times: a `tail` hid an
  NFL game and Kiley was told there was none, a `head` truncated a script before
  its write and the wrong file got committed, and a `tail` dropped two tennis
  picks so a 20-pick card was reported as 18. The file is the record; `score.py picks/<card>.json [--write]` scores it;
  `scores.py <league|card> [--write]` fetches exact final scores;
  `track.py <card> [--loop 900]` records the card's own markets until kickoff;
  `clv.py <card> [--write]` turns those snapshots into closing-line value.
  **Closing lines are captured by a routine, not a loop, and that routine must
  fire into a session that owns the repo.** "Closing-line tick (pick'em)"
  (`trig_01SuWa2ESpqLVd96N2bkMDwZ`) fires hourly, 9 AM–9 PM PT, into the
  dedicated session `session_01LbnuLFL9ta4heESMHu2GZC` ("Pick'em closing-line
  ticker"), which runs `picks/tick.sh`: one snapshot of every card dated today,
  committed and pushed. The first version of this routine spawned a FRESH
  session each firing and it never once pushed, for three days, while reporting
  SUCCEEDED every time — `create_trigger` cannot attach a repository, so those
  sessions had no checkout and no push credentials. `create_session` *can*
  (`source_url`), so the fix is: create a session with the repo attached, then
  bind the routine to it with `persistent_session_id`. If this ever needs
  rebuilding, do it in that order and verify by watching `main` for the commit —
  a routine's own SUCCEEDED status says the session ran, not that the work
  landed. A settled market quotes no price, so a close not captured before
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

  **SUPERSEDED 2026-10-10: the band is lifted and the hit rate is no longer the
  target.** Kiley: "no more 47-53%. You have access to the whole board. The goal
  is to get back to even so winning percentage no longer matters" -- and then,
  crucially, "not in one day but slowly over time". The old ruling below is kept
  because its *reasoning* still holds: she banned widening the band to flatter
  the hit rate, and that ban is only lifted because the hit rate stopped being
  the scoreboard, not because flattering it became acceptable. Never drift toward
  favorites to make a number look better.
  What the new goal actually implies, measured rather than assumed:
    - **Favorites are the WORST recovery vehicle**, not the safest: at zero edge
      over 30 days they recover $1,033 only 3.4% of the time, because the payout
      cannot cover the hole however often they win. Coin flips 29.7%, longshots
      at 0.10 52.2% (with a 20.1% chance of being $3,000 down instead).
    - **"Slowly over time" needs an edge to work at all.** At ZERO edge a longer
      horizon makes the loss MORE certain: median end -161, -521, -1043, -2085,
      -5002 at 15/30/60/120/240 days. With a real +2.5 point edge the same
      horizons give +39, +279, +557, +915, +1830. Time is the friend of whoever
      has the edge. Do not read the "ever reached +$1,033" figure as recovery --
      it rises with time even at zero edge, because a random walk eventually
      touches a level on the way down.
    - So the only honest lever is cost, not selection. See the maker/taker note.

  **The fee is a TAKER fee, and this is the single biggest thing in the file.**
  Over 320 settled picks the record is +$56.89 gross against $1,089.75 of fees
  -- the entire loss IS the fee. It was charged on every pick because the
  0.0695 `feeCoefficient` on each market was assumed to apply to every order.
  Web search on 2026-10-10 (Kiley: "You can Google it", after I had told her it
  was unknowable) says otherwise: 0.0695 is the TAKER coefficient, and every
  third-party guide found -- they conflict on rates, quoting 0.06 to 0.0695 and
  differing US-vs-global -- agrees that MAKERS PAY ZERO and earn a rebate on
  resting orders near a 0.0125 coefficient. Break-even at 50c: taker 51.74%,
  maker 49.69%, i.e. BELOW a coin flip, because the rebate pays for the quote.
  NOT VERIFIED and must not be stated as fact: polymarket.com and
  docs.polymarket.com are both blocked by this environment's network policy, the
  gateway serves no fee endpoint (`/v1/fees` is 404) and no maker/taker field.
  `picks/fees.py` takes `maker=True` as a hypothesis; taker stays the default.
  One real fill from Kiley's own account settles it. **Do not retroactively zero
  the fee on the old record** -- every one of those picks was logged at the ASK,
  which IS a taker order, so they were never maker fills.

  **Resting a limit instead of crossing the spread is the one measured positive.**
  Per $100 at zero edge: crossing -$4.43, resting at mid -$3.48, at the bid
  -$2.50. `picks/fillsim.py` replays the stored bid/ask snapshots: a mid limit
  would have filled on 56 of 171 picks (33%), worth +$1.11 a pick from price
  alone (+$43.80 against -$18.52 on the same 56). With a zero maker fee those 56
  go to +$239.04, about +$4.27 a pick -- the sign flips. It is n=56 at 51.8%
  +-13.1%, so NOT an edge yet, but it is the first positive thing measured and
  it is about order placement rather than predicting games.
  **Adverse selection is real and decides the whole thing**: bid fills hit 24/51
  = 47.1% against 50.8% for the orders that never filled, because a deeper limit
  only fills once the price has moved against you. Mid fills hit 51.8%. So post
  at MID, never at the bid. `pick.py` takes `limit: "mid"|"rest"|"cross"`,
  `picks/fills.py` resolves fills from the snapshots, and `pnl.py` excludes an
  unfilled resting order entirely -- **an unfilled order is NO BET: no stake, no
  payout, no fee.** Scoring one as a loss is the most expensive bookkeeping error
  available here.

  **Every research avenue tried this week came back empty. Do not re-run them
  from scratch.** Cross-venue: ESPN's scoreboard carries DraftKings lines, and
  they matched Polymarket on every market every day -- identical spreads
  including NBA preseason, small-school CFB and NHL. Ladder arbitrage:
  `picks/arb.py` found 0 executable locks board-wide (its first version reported
  166 by testing the ladder direction backwards -- a lock is buying the MORE
  likely rung below the sell price of the less likely one). Prop arithmetic:
  `picks/propaudit.py` checked 189 constraints that hold by arithmetic (each gte
  ladder must fall, a home run is four bases so hr>=1 cannot beat tb>=4, k hits
  give k bases) across 183 quoted props -- 0 executable violations. Handicapping:
  injuries on public reports are priced days earlier (Baker Mayfield OUT with
  Jalon Daniels next up is exactly WHY the number was DAL -9.5), and announced
  starters are the most heavily priced input on a baseball board.

  **`picks/lineaudit.py` and the pre-registration habit.** It recovers the book's
  own posted number for FINISHED games -- ESPN drops odds from the scoreboard
  once final but keeps them on `summary?event=<id>` under `pickcenter` -- and
  asks the only version of the base-rate question that is not a conditioning
  error: among games where the book posted exactly 6.5, how often did the over
  land? Result: DraftKings closing NHL 6.5 went over 282/563 = 50.1%. The
  closing line is honest to a tenth of a point. `pickcenter` holds the CLOSE,
  not the open -- verified twice (it matched live odds on 8 unstarted games, and
  retained the later number on 5 of 16 that moved after a pre-kickoff reading).
  Coverage is erratic and the BOOK CHANGES BY SEASON: 2025-26 DraftKings, 2024-25
  nothing at all, 2023-24 Unibet. Never pool two books.
  `picks/PREREGISTERED.md` fixes what counts as actionable before the output is
  read, and it earned its keep the hard way: the 2023-24 sample PASSED all three
  original criteria (n=1253, over 57.3%, band excluding 50%, gap 7.3 points) and
  was still garbage, because Unibet's total reads 5.5 on 95.5% of games and
  57.3% is just the share of games clearing a FIXED 5.5 line when scoring
  averages 6.23. Hence a fourth required condition: no single posted number may
  exceed 80% of the sample, and the book must be one quoted on the venue being
  bet. **Pre-registration stops a threshold being fitted to noise; it does not
  check that the data means what it is assumed to mean.**

  **Two display/filter bugs found by building a card late in the day.** `card.py`
  filtered candidates by DATE only, so five games already in play were eligible
  -- an in-play price has moved on what happened in the game. It now requires the
  start to be in the future. And `show.py` printed a flat list with the game name
  cut to 30 characters, which put two different NCAAF games on adjacent rows so
  Kiley could not tell which total belonged to which matchup; it now groups by
  game with the full name. A card the person betting it cannot check is not a
  card that has been reported.

  **Do not run `tick.sh` in the same breath as building or editing a card.** It
  does `git add -A picks`, so it sweeps uncommitted work into a commit titled
  "closing-line snapshot". This has now happened twice (from_market(), then the
  in-play fix plus a whole card). Commit code first, then tick.

  **The superseded 2026-09-29 ruling, kept for its reasoning:** asked whether she
  wanted the 47-53% band widened so the hit rate would climb toward 60%, Kiley
  ruled it out: picking higher-probability markets to lift the number is
  cheating, because it moves the target instead of getting better. The rule is coin flips, and the hit rate that comes with them is ~50%
  by construction. Improvement is real but lands elsewhere: choosing the rung by
  differencing the ladder rather than by nearest-50%, capturing closing-line
  value, getting the card up in the morning so the close is measurable, and not
  repeating the logged errors (three picks priced at their own complement, spread
  sides read off titleShort, a team total taken for a game total, two trackers
  that never ran). Offer that list when asked to improve -- never a wider band,
  never a higher cap, never a quiet drift toward favorites.

  **Player props settle from the market, not from a box score.** Kiley started
  picking props on 2026-10-06 (Ohtani to homer, three Dodgers at 2+ total bases).
  Total bases cannot be reconstructed from ESPN: its MLB box score carries hits,
  runs, RBIs and home runs but not doubles or triples. The venue settles the prop
  itself, so a pick with `resolve {"kind": "market"}` is scored by
  `score.py from_market()`, which reads the closed market's `outcomes`
  (["Yes","No"]) against its `outcomePrices` (["0","1"]). While the market is
  still open it returns None, so an unsettled prop reads pending rather than
  lost. Verified against a settled market from the 2026-10-04 game.

  Note that `tick.sh` runs `git add -A picks`, so **anything edited under
  `picks/` gets swept into whatever snapshot commit runs next** -- that is how
  from_market() landed under a commit message about closing lines. Commit code
  changes before running a tick, or the history misdescribes them.

  **Track the money too: `python picks/pnl.py` (`--write` stores each card's
  line, `--both-sides` for the pessimistic fee).** The hit rate hides the thing
  that decides profit, which is the price paid -- 53% is a win at 51c and a loss
  at 55c. `score.py` now prints the card's own figure every time it scores. At
  $100 a pick across the first 210 settled picks: $21,000 staked, +$922 gross,
  +$212 net after fees, hit 53.3% against a 51.4% average price. That is
  break-even, not an edge: at +-3.5 points of margin on 210 picks, a true 50%
  sits well inside the error and would land near -$700 after fees.

  The venue publishes no fee schedule (`/v1/fees` is a 404), but every market
  carries `feeCoefficient` 0.0695, matching the usual regulated event-contract
  formula `fee = coefficient x contracts x price x (1 - price)`. Whether it is
  charged once or again at settlement is unknown and the two readings straddle
  zero, so quote the fee half as an assumed model, never as a quoted rate. Note
  the formula peaks at exactly 50c -- the coin-flip rule puts every pick on the
  maximum-fee point of the curve by construction, about 3.4c per $100.

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
