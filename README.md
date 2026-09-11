# eBay seller connection

[![tests](https://github.com/kileybedwell-tech/home/actions/workflows/test.yml/badge.svg)](https://github.com/kileybedwell-tech/home/actions/workflows/test.yml)

A small, dependency-free Python client and CLI for managing your own eBay
listings, inventory and orders through eBay's Sell APIs.

Everything runs on the Python 3.9+ standard library — no `pip install`, no
virtualenv required.

```
python -m ebay login       # one-time browser consent
python -m ebay listings    # your inventory
python -m ebay orders --unshipped
```

## Why there is no "connect" button

eBay has no consumer OAuth app you can just click through, and Claude has no
eBay connector. Any programmatic access to your account goes through an app
you register yourself at [developer.ebay.com](https://developer.ebay.com), and
then authorize against your own seller account. That is what `login` below
does. It takes about five minutes once.

## Setup

### 1. Get your keys

Sign in at developer.ebay.com and open **Hi \<you\> → Application Keys**. You
will see two keysets, Sandbox and Production. From the one you want:

| eBay's label  | What this project calls it |
| ------------- | -------------------------- |
| App ID        | `EBAY_CLIENT_ID`           |
| Cert ID       | `EBAY_CLIENT_SECRET`       |

Start with **Sandbox** if you would rather not touch live listings while
trying this out — pass `--sandbox` to every command.

### 2. Create a redirect entry (RuName)

On the same page, open the **User Tokens** tab and click **Get a Token from
eBay via Your Application → Add eBay Redirect URL**. Fill in:

- **Your auth accepted URL** — any https URL you control. It never has to
  render anything useful; you only copy the address bar out of it.
- **Your auth declined URL** — same, or anything valid.
- **Privacy policy URL** — required by the form.

eBay then shows a **RuName** that looks like `Kiley_B-KileyApp-PRD-a1b2c3d4e`.

> **This is the single most common thing to get wrong.** `EBAY_REDIRECT_URI`
> must be that RuName, *not* the https URL you typed in. Passing the URL gets
> you `invalid_request` from the authorize endpoint.

### 3. Enter your keys

```bash
python -m ebay setup
```

It prompts for each value in turn — the Cert ID is read without echoing — and
writes a `.env` with mode `0600`. It refuses to overwrite an existing one
without `--force`, and flags the two mix-ups that produce unhelpful errors
later: a RuName pasted as a URL, and a Sandbox keyset paired with
`production` (or the reverse).

Prefer to do it by hand? `cp .env.example .env` and edit it; the format is the
same. Either way `.env` is gitignored, and the client secret is only ever sent
to eBay's token endpoint, as an HTTP Basic credential.

### 4. Authorize

```bash
python -m ebay login
```

It prints a URL. Open it, sign in as the seller account, approve the
permissions, and eBay bounces you to your accepted URL with `?code=...` in
the address bar. Copy that entire URL and paste it back at the prompt.

Tokens land in `~/.config/ebay-connect/production.json` with mode `0600`.
Alternatively set `EBAY_REFRESH_TOKEN` and no consent step is needed at all —
the refresh token is the durable half of the pair, so a machine with the three
credentials plus that variable connects on its own. That is what lets an
ephemeral container work without a browser.
The access token lasts about two hours and is refreshed automatically; the
refresh token lasts about 18 months, after which `login` runs again.

Prefer to look before you touch anything?

```bash
python -m ebay login --readonly
```

## Commands

| Command | What it does |
| ------- | ------------ |
| `setup [--force]` | Prompt for your keys and write `.env` |
| `login [--readonly] [--force]` | Authorize against your seller account |
| `status` | Connection state, token expiry, selling limits |
| `logout` | Delete the saved tokens |
| `create SKU --title ... --price ... --category ...` | Create a listing end to end |
| `images FILE...` | Upload photos to eBay Picture Services, print their URLs |
| `categories QUERY` | Find the leaf category id `create` needs |
| `condition-policy CATEGORY_ID` | Valid condition ids/descriptors for a category (trading cards, coins, ...) |
| `locations [--create]` | List, or create, the inventory location offers ship from |
| `listings [--with-offers]` | SKUs *this tool* created, optionally with price and live status |
| `find QUERY [--limit N]` | Search **every** active listing (however it was made) for a possible duplicate |
| `duplicates [--limit N]` | Scan all active listings for likely accidental re-listings |
| `item SKU` | One SKU plus its offers, as JSON |
| `orders [--unshipped] [--since ISO]` | Recent orders |
| `order ORDER_ID` | One order in full, as JSON |
| `policies` | Business policy IDs that offers must reference |
| `price SKU --price 24.99 --quantity 3` | Change price and/or stock |
| `pending [--limit N]` | Offers created but not yet live — the approval queue |
| `publish OFFER_ID...` | Take one or many offers live |
| `withdraw OFFER_ID` | End a live listing, keeping the offer |
| `ship ORDER_ID --tracking 92... --carrier USPS` | Mark an order shipped |
| `backlog-add DESCRIPTION [--category ...] [--notes ...] [--mercari-url ...]` | Log a physical item you haven't listed yet |
| `backlog-list [--status ...] [--mercari unlisted\|drafted\|listed\|sold]` | See your backlog, filtered by eBay or Mercari status |
| `backlog-update ID [--status ...] [--sku ...] [--item-id ...] [--mercari ...] [--mercari-url ...]` | Update a backlog item, or link it to an eBay or Mercari listing |
| `backlog-remove ID` | Remove a backlog item |
| `mercari-draft DRAFT.json [--photos-dir ...] [--hashtag ...] [--backlog ID]` | Print a paste-ready Mercari listing from the same draft JSON `create` takes |

Global flags: `--sandbox`, `--marketplace EBAY_GB`, `--env-file path`.

The `backlog-*` and `mercari-draft` commands are pure local bookkeeping (a
JSON file, default `inventory.json`) - no eBay auth or network access
needed. They exist for the gaps eBay's own APIs can't fill: knowing what
you physically have that isn't listed yet, and what is on Mercari (see
"Mercari" below). `find`/`duplicates` cover the other end (what's *already*
live on eBay, listed however it was made) - see "Notes and gotchas" below.

`pip install -e .` is optional and only shortens `python -m ebay` to `ebay`.

Most read commands take `--json` for the raw eBay payload, so you can pipe
into `jq`.

## Creating a listing

eBay has no single "create listing" call. A live listing is three resources in
sequence: an **inventory item** (what the thing is), an **offer** (what it
costs on one marketplace), and a **publish** of that offer. `create` drives all
three:

```bash
python -m ebay categories "35mm film camera"     # find the category id

python -m ebay create VINTAGE-CAM-01 \
  --title "Canon AE-1 35mm Film Camera with 50mm f/1.8" \
  --price 189.00 --category 15230 --condition USED_EXCELLENT \
  --image https://i.ebayimg.com/images/g/abc/s-l1600.jpg \
  --aspect Brand=Canon --aspect Model=AE-1
```

```
Created offer OF-99812 for SKU VINTAGE-CAM-01.
Published as listing 110598234771.
```

### From photos

eBay's Inventory API takes image *URLs*, never file uploads, so local photos
have to be hosted first. `--photo` does that for you — each file is uploaded
to eBay Picture Services and the resulting URL attached to the listing:

```bash
python -m ebay create LP-BOWIE-01 \
  --title "David Bowie Hunky Dory LP 1971 RCA" \
  --price 32.50 --category 176985 --condition USED_VERY_GOOD \
  --photo front.jpg --photo back.jpg
```

`--image` still takes URLs you already host elsewhere; the two combine.
`python -m ebay images front.jpg back.jpg` uploads without listing anything and
just prints the URLs.

Note EPS deletes pictures that are not attached to a listing within 30 days,
so treat it as part of listing rather than as a photo store.

### The approval loop

Nothing has to go live the moment it is created. `create --draft` stops after
the offer exists, `pending` shows everything waiting, and `publish` takes them
live in one go. eBay only serves offers per SKU, so `pending` makes one
`getOffers` call for every SKU this tool has created; it runs them a few at a
time and counts progress on stderr, so a few hundred SKUs take seconds rather
than minutes of silence:

```bash
python -m ebay create LOT-1 --from-file lot1.json --photo a.jpg --draft
python -m ebay create LOT-2 --from-file lot2.json --photo b.jpg --draft

python -m ebay pending
```

```
OFFER   SKU    TITLE                     PRICE       STATUS
------  -----  ------------------------  ----------  -----------
OF-1    LOT-1  2026 Topps 75 Rookie ...  14.99 USD   UNPUBLISHED
OF-2    LOT-2  1971 Bowie Hunky Dory...   9.99 USD   UNPUBLISHED

2 awaiting approval. To publish them all:

  python -m ebay publish OF-1 OF-2
```

`publish` attempts every id given and reports each one, so a single rejected
offer never strands the rest of an approved batch; the failures come back as a
retry command.

### Drafting a listing with Claude

The fields eBay needs — title, description, condition, item specifics — are
exactly what a person (or Claude) can read off a photograph. The split that
works: Claude looks at your photos and writes a draft, you review it, the CLI
does the eBay mechanics.

```bash
# Claude writes bowie-lp.json from your photos, then:
python -m ebay create LP-BOWIE-01 --from-file bowie-lp.json \
  --photo front.jpg --photo back.jpg --dry-run
```

`--from-file` takes the same fields as the flags:

```json
{
  "sku": "LP-BOWIE-01",
  "title": "David Bowie Hunky Dory LP 1971 RCA Victor LSP-4623 Vinyl",
  "description": "Original 1971 RCA pressing. Vinyl shows light surface marks...",
  "price": "32.50",
  "category_id": "176985",
  "condition": "USED_VERY_GOOD",
  "condition_description": "Sleeve has ring wear; vinyl plays clean.",
  "quantity": 1,
  "aspects": {"Artist": ["David Bowie"], "Release Year": ["1971"]}
}
```

Useful flags:

- `--dry-run` prints both payloads and calls nothing. Worth running first.
- `--draft` creates the offer but stops before publishing, so you can review it
  in Seller Hub and `publish` later.
- `--from-file listing.json` takes the same fields as JSON instead of flags.
  Flags override the file, so a value the file cannot know — a category id
  looked up against the live account — needs no edit to the file.

Two things eBay requires but will not infer: the three **business policy ids**
and a **merchant location**. `create` resolves each automatically when your
account has exactly one; with several it stops and lists them so you can pass
`--payment-policy`, `--location` and friends. With none it tells you what to
create in Seller Hub rather than failing halfway through.

Re-running `create` for a SKU that already has an offer **updates** that offer
instead of erroring, so a corrected draft can just be submitted again.

Validation happens locally first — title length, price, condition, https image
URLs — so a typo fails before a half-created listing exists on eBay.

## Mercari

There is no Mercari equivalent of `login`, and this tool cannot grow one:
Mercari US has no seller API. The only official Mercari API (Mercari Shops)
is for Japanese business sellers under contract, and the crosslisting apps
that "connect" to Mercari do it by driving a logged-in Chrome tab through a
browser extension. So the Mercari side of this tool is two things that need
no connection at all: a listing you paste into the app, and a record of
what is listed where.

### A listing you can paste

`mercari-draft` takes the same draft JSON that `create --from-file` takes
and prints what Mercari's listing form asks for, inside Mercari's limits:

```bash
python -m ebay mercari-draft drafts/chatot-ar-sv5k.json
```

```
Mercari listing from drafts/chatot-ar-sv5k.json (SKU PKMN-CHATOT-AR-081-SV5K)

TITLE (70/80 characters)
Chatot Perap AR 081/071 sv5K Wild Force Japanese Pokemon Card Art Rare

DESCRIPTION (929/1000 characters)
Pokemon Japanese - Chatot (Perap) Art Rare 081/071 - sv5K Wild Force
...

CATEGORY   pick in the app
BRAND      The Pokémon Company   (from the Manufacturer item specific; pick the closest brand the app offers)
CONDITION  Like New   (eBay: Ungraded, Near mint or better)
PRICE      $5.99

PHOTOS (2 of 12 max, in this order)
  1. photos/chatot-ar-sv5k/01-front.jpg
  2. photos/chatot-ar-sv5k/02-back.jpg

CHECK BEFORE POSTING
  - Mercari's categories are its own; pick one in the app (eBay category was 183454)
```

What happens to each field:

- **Title** - Mercari's limit is 80 characters, the same as eBay's, so it
  passes through; a longer override is cut at a word.
- **Description** - Mercari allows 1,000 characters where eBay allows far
  more. HTML is stripped, hand-wrapped lines are joined back into
  paragraphs (Mercari renders every newline), then the eBay description,
  the condition note, item specifics not already mentioned, and any
  `--hashtag`s are added in that order until the budget runs out. Anything
  that did not fit is listed under CHECK BEFORE POSTING rather than
  dropped quietly. If the eBay description alone is too long it is cut at
  a paragraph or sentence and the cut point shown; when that happens,
  write a shorter Mercari version in the draft's `mercari` block (below)
  rather than letting the tool decide what goes.
- **Condition** - eBay's enum, or the trading-card condition id plus
  descriptors, mapped to Mercari's New / Like New / Good / Fair / Poor:

  | eBay | Mercari |
  | ---- | ------- |
  | NEW, NEW_OTHER | New |
  | LIKE_NEW, USED_EXCELLENT, refurbished (certified/excellent) | Like New |
  | USED_VERY_GOOD, USED_GOOD, other refurbished, NEW_WITH_DEFECTS | Good |
  | USED_ACCEPTABLE | Fair |
  | FOR_PARTS_OR_NOT_WORKING | Poor |
  | Ungraded card: near mint / lightly played / moderately played / heavily played | Like New / Good / Fair / Poor |
  | Graded card: 9+ / 7+ / 4+ / below | Like New / Good / Fair / Poor |

  Mercari has no "refurbished" or "new with defects", so those map to the
  nearest step and a warning says to mention it in the description.
- **Price** - the same number, flagged if outside Mercari's $1-$2,000
  range ($5,000 for Authenticate-eligible items after extra ID checks).
- **Photos** - for `drafts/NAME.json`, the files in `photos/NAME/` in name
  order, 12 at most. `--photo` (repeatable) or `--photos-dir` override.
- **Category and brand** - Mercari's own trees. Brand comes from the Brand
  (or Manufacturer) item specific; category has to be picked in the app
  unless the draft says.

Mercari-specific values live in an optional `"mercari"` block in the same
JSON file, which `create` ignores:

```json
{
  "sku": "PKMN-CHATOT-AR-081-SV5K",
  "title": "...",
  "mercari": {
    "category": "Toys & Collectibles > Trading Cards > Pokémon",
    "brand": "Pokémon",
    "description": "A shorter description written for Mercari's 1,000 characters.",
    "hashtags": ["pokemon", "pokemoncards", "wildforce"]
  }
}
```

The flags `--title`, `--description`, `--condition`, `--price`, `--brand`,
`--category` and `--hashtag` override the block. `--json` prints the draft
as JSON instead of the paste-ready text.

### Tracking what is where

Every backlog item has two sides: `status` with `--sku`/`--item-id` is
eBay, and `--mercari` with `--mercari-url` is Mercari, each one of
`unlisted`, `drafted`, `listed` or `sold`. Since nothing can read Mercari,
the Mercari side is only ever what you tell it:

```bash
python -m ebay mercari-draft drafts/chatot-ar-sv5k.json --backlog 3   # marks #3 drafted for Mercari
python -m ebay backlog-update 3 --mercari-url m12345678901              # it's up: marks #3 listed, keeps the link
python -m ebay backlog-list --mercari unlisted                          # what still isn't on Mercari
```

`--mercari-url` takes the full URL or just the `m...` item id from the
app's share link. When something sells on one site, say so and the
command tells you if it is still live on the other:

```bash
python -m ebay backlog-update 3 --mercari sold
```

```
#3: Chatot Perap AR 081/071 [listed]
  Mercari: sold https://www.mercari.com/us/item/m12345678901/
reminder: sold on Mercari but still listed on eBay (https://www.ebay.com/itm/178451166492) - end the eBay listing
```

`backlog-list` repeats any outstanding reminders at the bottom. Ending the
other listing is still a separate step: eBay through this tool (`withdraw`
for a SKU it created, Seller Hub or a Trading API `EndItem` otherwise),
Mercari in the app.

## Hotel finder

A second, separate tool in the same repo: `python -m hotels` finds the
cheapest hotel for a stay. Same rules as `ebay` — standard library only,
credentials in `.env`, no network in the tests.

```
python -m hotels search Seattle 2026-10-03 2026-10-05 --adults 2
python -m hotels search LAS 2026-11-20 2026-11-23 --refundable --max-price 400
python -m hotels compare examples/hotel-quotes.json --check-in 2026-10-03 --check-out 2026-10-05
```

### `search` — live prices

`search` prices every hotel near a city for the dates given and prints them
cheapest first, with per-night cost, distance from the centre, room type and
whether the rate can be cancelled. It uses the
[Amadeus Self-Service](https://developers.amadeus.com) Hotel Search API,
which is the one hotel-pricing API with a free tier that does not require a
travel-agency contract or a paid aggregator subscription.

Setup, once:

1. Sign up at developers.amadeus.com, open **My Self-Service Workspace →
   Create new app**, and copy the app's **API Key** and **API Secret**.
2. Put them in `.env` as `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET`
   (see `.env.example`).

New apps live in Amadeus's **test** environment, which serves a cached
sample of hotels rather than live rates — good for seeing the tool work,
not for deciding where to book. The output says so on every run. To get
real prices, promote the app to production in the portal and set
`AMADEUS_ENVIRONMENT=production`; production is free up to a monthly quota
and pay-per-call after that.

Options: `--adults`, `--rooms`, `--currency`, `--radius KM` (default 10),
`--max-hotels` (default 60, nearest first), `--refundable`,
`--max-price TOTAL`, `--limit`, `--json`. The place can be a city name or a
three-letter city/airport code (`SEA`, `NYC`, `LAS`), which skips the lookup.

### `compare` — prices you collected yourself

No API covers Booking, Expedia, Hotels.com and hotels' own sites at once,
and the cheapest rate for a given hotel is often on one of them. `compare`
takes a JSON or CSV file of quotes you jotted down and ranks them the same
way. Each quote needs a hotel name and either `total` for the stay or
`per_night`; `nights`, `currency`, `source`, `room`, `url`, `refundable`,
`wifi`, `stars`, `safety`, `area`, `sportsbook`, `rewards` and the
hidden-fee fields below are optional. `examples/hotel-quotes.json` shows the shape.

```json
[
  {"hotel": "Hotel Theodore", "source": "Booking.com", "total": 418.00, "refundable": true},
  {"hotel": "Hotel Theodore", "source": "hotel website", "per_night": 199.00, "refundable": true},
  {"hotel": "The Maxwell Hotel", "source": "Hotels.com", "per_night": 174.00}
]
```

`--check-in`/`--check-out` (or `--nights`) turn per-night quotes into stay
totals; `--refundable`, `--max-price`, `--limit` and `--json` work as in
`search`.

**Hidden fees.** The headline price on a booking site is rarely what you
pay in Las Vegas: resort fees of $35-50 a night and 13.38% room tax are
added at the desk. Give each quote `fee_per_night` (or `fees` for the whole
stay), an optional `fee_note`, and `tax_pct` for whatever the quote leaves
out, and the tool ranks on the **all-in** total, shows the quoted price and
the hidden extra side by side, and names the biggest gap. `--max-price`
applies to the all-in figure. Leave the fields out for a quote that already
includes everything. Live searches do the same with the taxes and fees
Amadeus marks as not included in the rate.

`wifi` is `true`/`false` (or `yes`/`no`/`free`/`paid`) and `--wifi` keeps
only hotels with free WiFi. Unknown is dropped, since "probably" is not
free.

`stars` is the hotel's class as booking sites list it, 1 to 5 in half
steps, shown as its own column and filtered with `--min-stars 3.5`. Live
searches fill it in when Amadeus's hotel list carries a rating for the
property.

`safety` is a 1-5 rating of how comfortable the hotel and its
surroundings are on your own (1: drive in, don't walk; 5: busy, lit,
security at every door), and `area` says where it is and what the walk is
like. Both are your call; the Vegas example rates the north Strip low and
the center Strip high, which is what every solo-travel guide says. `--min-safety 4`
keeps only hotels rated at least that.

`sportsbook` is a 1-5 rating of the hotel's own sportsbook (1 a kiosk, 5
Circa-level) that you assign. It shows up as its own column when any quote
has one, and `--min-sportsbook 3` drops hotels rated lower or not at all,
so "cheapest room with a book worth sitting in" is one command:

```
python -m hotels compare examples/vegas-quotes.json --nights 2 --min-sportsbook 3
```

`rewards` is the loyalty programme the stay earns in, as free text
("Caesars Rewards", "MGM Rewards (Marriott Bonvoy partner)"). It gets its
own column when any quote has one, and `--rewards caesars` keeps only
hotels whose programme name contains that text, so points-chasing is a
filter too:

```
python -m hotels compare examples/vegas-quotes.json --nights 2 --rewards mgm
```

`examples/vegas-quotes.json` is a Strip line-up with all three filled in
to start from, with 2026 resort fees. The filters stack, so the cheapest
room that is safe on your own, has free WiFi and a decent book, all-in, is:

```
python -m hotels compare examples/vegas-quotes.json --nights 2 --wifi --min-safety 4 --min-sportsbook 3
```

Quotes in different currencies are never converted: the ranking groups them
by currency, most common first, and says so, rather than calling a €200 room
cheaper than a $210 one.

### `travel` — drive or fly?

The hotel is not the whole trip. `travel` totals a round trip by car
(fuel from miles, mpg and gas price, or a flat `--per-mile 0.70` to count
wear at the IRS rate, plus hotel parking) against flying (`--flight`, a
one-way fare per person times travelers, plus `--flight-extras` for
airport parking, rides and bags) and the bus (`--bus`, `--bus-extras`),
and prints which is cheapest and by how much. Fares are one way and the
return is assumed to match unless `--flight-return` / `--bus-return` says
otherwise; `0` means someone else is driving you home. Without a fare it
prints the break-even one-way fare instead, so you know what to look for:

```
python -m hotels travel --miles 270 --hours 4 --gas 5.86 --parking 25 --nights 2 --travelers 2
python -m hotels travel --miles 270 --gas 5.86 --parking 25 --nights 2 --travelers 2 --flight 70 --flight-extras 90 --hotel 219.96
python -m hotels travel --miles 270 --gas 5.86 --nights 2 --bus 45 --bus-return 0 --bus-extras 40 --hotel 190.48
```

`--hotel` takes the all-in total from `compare` and prints a trip total
per option. Going the other way, `compare --travel 85` adds a fixed
getting-there cost to every hotel and shows the trip total as a column:

```
python -m hotels compare examples/vegas-quotes.json --nights 2 --travel 85 --min-safety 4
```

## Using it as a library

```python
from ebay import Config, EbayClient
from ebay.config import load_dotenv

load_dotenv()
client = EbayClient(Config.from_env())

for order in client.orders(order_filter="orderfulfillmentstatus:{NOT_STARTED}"):
    print(order["orderId"], order["pricingSummary"]["total"]["value"])

client.update_price_quantity("VINTAGE-CAM-01", price="175.00", quantity=1)
```

`inventory_items()` and `orders()` are generators that page through eBay's
offset pagination for you.

## Layout

```
ebay/
  config.py   endpoints, scopes, EBAY_* environment loading
  http.py     stdlib JSON transport; retries 429/5xx with backoff
  auth.py     OAuth grants, token refresh, 0600 on-disk token store
  client.py   Sell Inventory / Fulfillment / Account / Taxonomy / Media wrappers
  listing.py  the inventory-item -> offer -> publish sequence
  policies.py business policy payloads and creation
  cli.py      argparse front end
hotels/
  search.py   Quote model and the cheapest-first ranking
  amadeus.py  Amadeus Hotel Search client (token, city lookup, offers)
  travel.py   drive-vs-fly totals and the break-even fare
  cli.py      search / compare / travel commands
examples/
  hotel-quotes.json   sample input for `python -m hotels compare`
  vegas-quotes.json   the same with sportsbook ratings
tests/
  test_ebay.py
  test_hotels.py
```

## Tests

```bash
python -m unittest discover -s tests -v
```

150 tests, no network — the transport is stubbed at the seam, so the OAuth
grants, pagination, header rules, listing payloads, policy resolution and error
parsing are all covered offline.

CI runs the same suite plus a CLI smoke test against Python 3.9 through 3.13
on every push and pull request.

## Notes and gotchas

- **Scope strings always use `api.ebay.com`**, even for sandbox apps. Pointing
  them at `api.sandbox.ebay.com` yields `invalid_scope`.
- **The authorization code is percent-encoded** in the redirect. It has to be
  decoded before the token exchange or you get `invalid_grant`; `login`
  handles this whether you paste the URL or a bare code.
- **Inventory writes need `Content-Language`.** The client sends it on every
  request that carries a body.
- **Price lives on the offer, quantity on the inventory item.** `price` looks
  up the SKU's offers when a price change is requested, which is why a SKU
  with no offer yet can take a quantity but not a price.
- **Business policies are opt-in.** Every policy call returns error 20403,
  "User is not eligible for Business Policy", until the seller enrols in
  `SELLING_POLICY_MANAGEMENT`. Run `programs --opt-in` once; the error carries
  that hint wherever it surfaces.
- **A new account has no business policies.** `policies --create` makes the
  three an offer needs (immediate payment, 30 day returns, flat-rate domestic
  shipping), skipping any that already exist. eBay validates shipping service
  codes server-side, so if it rejects one, pass another with
  `--shipping-service`, though it first walks a list of known-good codes on
  its own. Note eBay never minted a `USPSGroundAdvantage` code — it kept
  `USPSParcel` and remapped it when USPS renamed the service.
- **A location needs only a postal code.** `locations --create --postal-code
  93401` is enough for eBay to rate shipping; a street address is optional and
  is not shown to buyers either way.
- **Publishing needs business policies and a location.** A seller account
  without payment, return and fulfillment policies, or without an inventory
  location, cannot publish an offer — run `policies` and `locations` to check.
- **The Media API is on a different host.** Image uploads go to
  `apim.ebay.com`, not the `api.ebay.com` every other Sell API uses. The
  client handles this; `EBAY_MEDIA_HOST` overrides it if eBay moves it.
- **Categories must be leaves.** A parent category id is rejected at publish
  time; `categories` returns only listable leaves.
- **A SKU with no offers yet 404s instead of returning an empty list.**
  `GET /sell/inventory/v1/offer?sku=` returns `404 [25713] "This Offer is not
  available"` for a brand new SKU rather than `200` with `[]` - a documented
  eBay quirk, not a real error. `offers_for_sku` swallows exactly that case.
- **Trading card categories (183454 CCG Individual Cards and others) reject
  the plain condition enum outright.** They need a conditionId + descriptors
  from `condition-policy CATEGORY_ID` instead - see `ListingDraft.condition_id`
  /`condition_descriptors`. The one non-obvious part: eBay repurposes
  `LIKE_NEW` to mean Graded and `USED_VERY_GOOD` to mean Ungraded on the wire;
  sending the raw numeric id directly fails with "Could not serialize field
  [condition]" (`CONDITION_ID_TO_ENUM` documents the mapping).
- **`listings` only sees SKUs this tool itself created.** The Sell Inventory
  API has no idea about listings made on eBay's website, through Seller Hub
  bulk tools, File Exchange, or a crosslisting tool - a seller with a large
  pre-existing store can have `listings` report a handful of items while
  hundreds or thousands more are actually live. `find`/`duplicates` go
  through the classic Trading API's `GetMyeBaySelling` instead (`ebay/
  trading.py`), which sees everything regardless of how it was listed -
  always check with `find` before drafting something new, not `listings`.
- Sandbox and production tokens are stored in separate files, so you can stay
  logged into both.
