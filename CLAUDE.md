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

  See `README.md` in this repo for the full command reference.
