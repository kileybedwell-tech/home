# eBay seller connection

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
  client.py   Sell Inventory / Fulfillment / Account wrappers
  cli.py      argparse front end
tests/
  test_ebay.py
```

## Tests

```bash
python -m unittest discover -s tests -v
```

37 tests, no network — the transport is stubbed at the seam, so the OAuth
grants, pagination, header rules and error parsing are all covered offline.

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
- **Publishing needs business policies.** A seller account without payment,
  return and fulfillment policies cannot publish an offer — run `policies` to
  check, and create them in Seller Hub if the lists come back empty.
- Sandbox and production tokens are stored in separate files, so you can stay
  logged into both.
