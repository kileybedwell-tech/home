"""Record the price of a card's own markets until kickoff, to get closing lines.

A single snapshot says nothing. Polling the exact markets a card bet on, right up to
kickoff, gives the closing price each pick can be measured against -- the only honest
read on pick quality at this sample size, since a hit rate needs hundreds of picks
before it can tell 50% from 55%.

    python picks/track.py picks/2026-09-26-mixed.json             # one snapshot
    python picks/track.py picks/2026-09-26-mixed.json --loop 900   # every 15 min

Snapshots append to picks/lines-<date>.jsonl, one object per market per poll. Run
clv.py afterwards to turn them into closing-line value.
"""
import json, os, re, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

PM = "https://gateway.polymarket.us"
PT = timezone(timedelta(hours=-7))
EVENT_SLUG = re.compile(r"(nfl|mlb|wnba|cfb|nba|nhl|atp|wta|boxing)"
                        r"-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}")


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


def quote(q):
    return float(q["value"]) if isinstance(q, dict) and q.get("value") is not None else None


def pending(card):
    """Picks whose game has not started yet, grouped by the event they belong to."""
    now, out = datetime.now(PT), {}
    for who in card.get("players", {}).values():
        for p in who:
            slug = p.get("market_slug")
            kick = p.get("kickoff_pt")
            if not slug or not kick:
                continue
            if datetime.strptime(kick, "%Y-%m-%d %H:%M").replace(tzinfo=PT) <= now:
                continue
            m = EVENT_SLUG.search(slug)
            if m:
                out.setdefault(m.group(0), []).append(p)
    return out


def snapshot(card_path):
    card = json.load(open(card_path))
    groups = pending(card)
    if not groups:
        return 0, 0
    stamp = datetime.now(PT).isoformat(timespec="seconds")
    out = os.path.join(os.path.dirname(card_path) or ".",
                       f"lines-{card.get('date', 'card')}.jsonl")
    n = 0
    with open(out, "a") as fh:
        for ev, picks in groups.items():
            e = ((get(f"{PM}/v1/events/slug/{ev}") or {}).get("event")) or {}
            by_slug = {m.get("slug"): m for m in e.get("markets") or []}
            for p in picks:
                m = by_slug.get(p["market_slug"])
                if not m:
                    continue
                bid, ask = quote(m.get("bestBidQuote")), quote(m.get("bestAskQuote"))
                if bid is None or ask is None:
                    continue
                fh.write(json.dumps({"at": stamp, "market_slug": p["market_slug"],
                                     "pick": p["pick"], "bid": bid, "ask": ask,
                                     "mid": round((bid + ask) / 2, 4)}) + "\n")
                n += 1
    return len(groups), n


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: track.py picks/<card>.json [--loop SECONDS]")
    every = 0
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        every = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 900
    while True:
        g, n = snapshot(args[0])
        print(f"[{datetime.now(PT):%-I:%M %p}] {g} event(s), {n} market(s) recorded", flush=True)
        if not every or g == 0:
            if g == 0:
                print("every game has started — stopping", flush=True)
            break
        time.sleep(every)
