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

### 3. Fill in `.env`

```bash
cp .env.example .env
$EDITOR .env
```

`.env` is gitignored. The client secret is only ever sent to eBay's token
endpoint, as an HTTP Basic credential.

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
| `login [--readonly] [--force]` | Authorize against your seller account |
| `status` | Connection state, token expiry, selling limits |
| `logout` | Delete the saved tokens |
| `create SKU --title ... --price ... --category ...` | Create a listing end to end |
| `categories QUERY` | Find the leaf category id `create` needs |
| `locations` | Inventory locations an offer can ship from |
| `listings [--with-offers]` | Your inventory items, optionally with price and live status |
| `item SKU` | One SKU plus its offers, as JSON |
| `orders [--unshipped] [--since ISO]` | Recent orders |
| `order ORDER_ID` | One order in full, as JSON |
| `policies` | Business policy IDs that offers must reference |
| `price SKU --price 24.99 --quantity 3` | Change price and/or stock |
| `publish OFFER_ID` / `withdraw OFFER_ID` | Take an offer live / end the listing |
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
  client.py   Sell Inventory / Fulfillment / Account / Taxonomy wrappers
  listing.py  the inventory-item -> offer -> publish sequence
  cli.py      argparse front end
tests/
  test_ebay.py
```

## Tests

```bash
python -m unittest discover -s tests -v
```

69 tests, no network — the transport is stubbed at the seam, so the OAuth
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
- **Publishing needs business policies and a location.** A seller account
  without payment, return and fulfillment policies, or without an inventory
  location, cannot publish an offer — run `policies` and `locations` to check.
- **Categories must be leaves.** A parent category id is rejected at publish
  time; `categories` returns only listable leaves.
- Sandbox and production tokens are stored in separate files, so you can stay
  logged into both.
