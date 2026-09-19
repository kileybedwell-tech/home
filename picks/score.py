"""Score a pick'em card against Kalshi's settled game markets.

The card (picks/YYYY-MM-DD-ncaaf.json) stores each game's Kalshi event ticker, so
scoring never depends on matching team names after the fact. A game's winner is the
market in that event whose result settled to "yes".

    python picks/score.py picks/2026-09-19-ncaaf.json          # tally
    python picks/score.py picks/2026-09-19-ncaaf.json --write  # also save winners back
"""
import json, sys, time, urllib.request

KB = "https://external-api.kalshi.com/trade-api/v2"


def get(url, tries=3):
    for i in range(tries):
        try:
            rq = urllib.request.Request(url, headers={"User-Agent": "picks/1.0",
                                                      "Accept": "application/json"})
            with urllib.request.urlopen(rq, timeout=45) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))


def winner(event_ticker, codes):
    """Canonical school name of the winner, or None while the game is unsettled."""
    d = get(f"{KB}/events/{event_ticker}?with_nested_markets=true")
    markets = ((d or {}).get("event") or {}).get("markets") or []
    for m in markets:
        if (m.get("result") or "").lower() == "yes":
            suf = m["ticker"].split("-")[-1].upper()
            for code, name in codes.items():
                if suf.startswith(code):
                    return name
    return None


def main(path, write=False):
    card = json.load(open(path))
    games, players = card["games"], ("claude", "kiley")
    score = {p: 0 for p in players}
    decided = pending = 0

    for g in games:
        w = g.get("winner") or winner(g["kalshi_event"], g["kalshi_codes"])
        if w:
            g["winner"] = w
            decided += 1
        else:
            pending += 1
        marks = []
        for p in players:
            pick = g.get(f"{p}_pick")
            if not pick:
                marks.append(f"{p}: --")
            elif not w:
                marks.append(f"{p}: {pick} (pending)")
            else:
                hit = pick == w
                score[p] += hit
                marks.append(f"{p}: {pick} {'HIT ' if hit else 'miss'}")
        print(f"{g['game'][:40]:<40} winner: {(w or 'pending'):<18} " + " | ".join(marks))

    print(f"\n{decided} decided, {pending} pending")
    for p in players:
        picked = sum(1 for g in games if g.get(f"{p}_pick") and g.get("winner"))
        print(f"  {p}: {score[p]}/{picked}")
    if score["claude"] != score["kiley"] and not pending:
        lead = max(players, key=lambda p: score[p])
        print(f"  -> {lead} wins")

    if write:
        json.dump(card, open(path, "w"), indent=2)
        print(f"\nwrote winners back to {path}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "picks/2026-09-19-ncaaf.json", "--write" in sys.argv)
