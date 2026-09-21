"""Record how a game's spread and total ladders move before kickoff.

A single snapshot says nothing about direction. Sampling the same ladders every few
minutes shows which way the market drifts as information arrives, and gives the
closing line to measure a pick against.

    python picks/track.py picks/2026-09-20-nfl.json            # one snapshot
    python picks/track.py picks/2026-09-20-nfl.json --loop 900 # every 15 min until kickoff

Snapshots append to picks/lines-<date>.jsonl, one JSON object per poll.
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

KB = "https://external-api.kalshi.com/trade-api/v2"
PT = timezone(timedelta(hours=-7))


def get(url, tries=3):
    for i in range(tries):
        try:
            rq = urllib.request.Request(url, headers={"User-Agent": "track/1.0",
                                                      "Accept": "application/json"})
            with urllib.request.urlopen(rq, timeout=45) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))


def upcoming(card):
    """Game event tickers whose kickoff is still ahead, mapped to their title."""
    now = datetime.now(PT)
    out = {}
    for who in card.get("players", {}).values():
        for p in who:
            kick = datetime.strptime(p["kickoff_pt"], "%Y-%m-%d %H:%M").replace(tzinfo=PT)
            if kick <= now:
                continue
            # KXNFLSPREAD-26SEP20INDKC-KC7 -> the game's own slug, 26SEP20INDKC
            slug = p["settle_ticker"].split("-")[1]
            out[slug] = p["game"]
    return out


def ladders(slug):
    """Every spread and total rung Kalshi is quoting for this game."""
    rows = []
    for kind in ("SPREAD", "TOTAL"):
        d = get(f"{KB}/events/KXNFL{kind}-{slug}?with_nested_markets=true")
        for m in (((d or {}).get("event") or {}).get("markets") or []):
            if m.get("status") != "active":
                continue
            try:
                bid, ask = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append({"kind": kind, "ticker": m["ticker"], "means": m.get("yes_sub_title"),
                         "strike": m.get("floor_strike"), "bid": bid, "ask": ask,
                         "mid": round((bid + ask) / 2, 4)})
    return rows


def snapshot(card_path):
    card = json.load(open(card_path))
    games = upcoming(card)
    if not games:
        return 0, 0
    stamp = datetime.now(PT).isoformat(timespec="seconds")
    out = os.path.join(os.path.dirname(card_path) or ".",
                       f"lines-{card.get('date', 'card')}.jsonl")
    n = 0
    with open(out, "a") as fh:
        for slug, title in games.items():
            rows = ladders(slug)
            if not rows:
                continue
            fh.write(json.dumps({"at": stamp, "slug": slug, "game": title, "rungs": rows}) + "\n")
            n += len(rows)
    return len(games), n


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "picks/2026-09-20-nfl.json"
    every = 0
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        every = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 900
    while True:
        g, n = snapshot(path)
        ts = datetime.now(PT).strftime("%-I:%M %p")
        print(f"[{ts}] {g} game(s), {n} rungs recorded", flush=True)
        if not every or g == 0:
            if g == 0:
                print("nothing left before kickoff — stopping", flush=True)
            break
        time.sleep(every)
