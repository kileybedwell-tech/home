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
| `locations` | Inventory locations an offer can ship from |
| `listings [--with-offers]` | Your inventory items, optionally with price and live status |
| `item SKU` | One SKU plus its offers, as JSON |
| `orders [--unshipped] [--since ISO]` | Recent orders |
| `order ORDER_ID` | One order in full, as JSON |
| `policies` | Business policy IDs that offers must reference |
| `price SKU --price 24.99 --quantity 3` | Change price and/or stock |
| `pending` | Offers created but not yet live — the approval queue |
| `publish OFFER_ID...` | Take one or many offers live |
| `withdraw OFFER_ID` | End a live listing, keeping the offer |
| `ship ORDER_ID --tracking 92... --carrier USPS` | Mark an order shipped |

Global flags: `--sandbox`, `--marketplace EBAY_GB`, `--env-file path`.

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
live in one go:

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

Two things eBay requires but will not infer: the three **business policy ids**
and a **merchant location**. `create` resolves each automatically when your
account has exactly one; with several it stops and lists them so you can pass
`--payment-policy`, `--location` and friends. With none it tells you what to
create in Seller Hub rather than failing halfway through.

Re-running `create` for a SKU that already has an offer **updates** that offer
instead of erroring, so a corrected draft can just be submitted again.

Validation happens locally first — title length, price, condition, https image
URLs — so a typo fails before a half-created listing exists on eBay.

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
tests/
  test_ebay.py
```

## Tests

```bash
python -m unittest discover -s tests -v
```

129 tests, no network — the transport is stubbed at the seam, so the OAuth
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
  `--shipping-service`.
- **Publishing needs business policies and a location.** A seller account
  without payment, return and fulfillment policies, or without an inventory
  location, cannot publish an offer — run `policies` and `locations` to check.
- **The Media API is on a different host.** Image uploads go to
  `apim.ebay.com`, not the `api.ebay.com` every other Sell API uses. The
  client handles this; `EBAY_MEDIA_HOST` overrides it if eBay moves it.
- **Categories must be leaves.** A parent category id is rejected at publish
  time; `categories` returns only listable leaves.
- Sandbox and production tokens are stored in separate files, so you can stay
  logged into both.
