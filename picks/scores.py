"""Fetch exact final scores from Polymarket's event state.

No scoreboard site is reachable from this environment, and web-search summaries
hand back live scores labelled as finals. Polymarket's league feed is reachable and
carries the real thing: per-quarter scores, a combined "score" string, and an
`ended` / `period: FT` flag that distinguishes a finished game from one in play.

    python picks/scores.py nfl                              # every game's state
    python picks/scores.py picks/2026-09-20-nfl.json --write # fill final_scores
"""
import json, sys, urllib.request

PM = "https://gateway.polymarket.us"
LEAGUE_OF = {"nfl": "nfl", "NFL": "nfl", "cfb": "cfb", "NCAAF": "cfb"}


def get(url):
    rq = urllib.request.Request(url, headers={"User-Agent": "scores/1.0",
                                             "Accept": "application/json"})
    with urllib.request.urlopen(rq, timeout=45) as r:
        return json.load(r)


def by_slug(league, away, home, date):
    """A finished game drops out of the league listing, but its slug still resolves."""
    for a in _variants(away):
        for h in _variants(home):
            try:
                d = get(f"{PM}/v1/events/slug/{league}-{a}-{h}-{date}")
            except Exception:
                continue
            e = (d or {}).get("event")
            if e:
                return e
    return None


def _variants(abbr):
    """Polymarket's slug uses its own abbreviation; LA teams are the ambiguous ones."""
    a = abbr.lower()
    return {"la": ["lac", "lar", "la"], "lv": ["lv", "lvr"], "was": ["was", "wsh"],
            "jac": ["jac", "jax"], "ne": ["ne", "nwe"],
            "ny": ["nyj", "nyg", "ny"], "sf": ["sf", "sfo"], "tb": ["tb", "tbb"]}.get(a, [a])


def games(league):
    out, off = [], 0
    while True:
        d = get(f"{PM}/v2/leagues/{league}/events?limit=100&offset={off}"
                f"&type=sport&section=general")
        ev = d.get("events") or []
        out += ev
        if len(ev) < 100:
            break
        off += 100
    return out


def state(e):
    """{'title','final','score':{ABBR:pts}} — score is None until the game ends."""
    es = e.get("eventState") or {}
    teams = [(t.get("displayAbbreviation") or "").upper() for t in e.get("teams", [])]
    ids = [str(t.get("id")) for t in e.get("teams", [])]
    final = bool(es.get("ended")) and (es.get("period") or "").upper() == "FT"
    pts = {}
    for per in (es.get("periodScores") or []):
        for s in (per.get("scores") or []):
            cid = str(s.get("competitorId"))
            if cid in ids:
                pts[teams[ids.index(cid)]] = pts.get(teams[ids.index(cid)], 0) + int(s.get("score") or 0)
    # cross-check against the flat "score" string, which is away-home
    flat = es.get("score")
    if final and pts and isinstance(flat, str) and "-" in flat:
        try:
            a, h = (int(x) for x in flat.split("-", 1))
            if sorted(pts.values()) != sorted([a, h]):
                pts = {}                      # quarters disagree with the summary: trust neither
        except ValueError:
            pass
    return {"title": e.get("title"), "final": final, "in_play": bool(es.get("live")),
            "period": es.get("period"), "score": pts if (final and pts) else None}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = args[0] if args else "nfl"
    if target.endswith(".json"):
        card = json.load(open(target))
        league = LEAGUE_OF.get(card.get("sport", "nfl"), "nfl")
        want = {p["game"] for who in card.get("players", {}).values() for p in who}
        found, unfinished = {}, []
        for title in sorted(want):
            # "LV Raiders vs LA Chargers" -> away LV, home LA
            halves = title.split(" vs ")
            if len(halves) != 2:
                continue
            away, home = (h.strip().split()[0] for h in halves)
            e = by_slug(league, away, home, card["date"])
            if e is None:
                unfinished.append((title, "slug not found"))
                continue
            st = state(e)
            if st["score"]:
                found[title] = st["score"]
            else:
                unfinished.append((title, st["period"] or ("live" if st["in_play"] else "scheduled")))
        for t, s in sorted(found.items()):
            print(f"  {t:<34} " + " - ".join(f"{k} {v}" for k, v in s.items()))
        for t, why in unfinished:
            print(f"  ....  {t:<34} {why}")
        print(f"\n{len(found)} final of {len(want)} games on the card")
        if "--write" in sys.argv:
            card.setdefault("final_scores", {}).update(found)
            json.dump(card, open(target, "w"), indent=2)
            print(f"wrote {len(found)} scores to {target}")
    else:
        for e in games(LEAGUE_OF.get(target, target)):
            st = state(e)
            tag = "FINAL" if st["final"] else ("live " if st["in_play"] else "sched")
            sc = " - ".join(f"{k} {v}" for k, v in (st["score"] or {}).items()) or ""
            print(f"  {tag}  {st['title']:<34} {sc}")


if __name__ == "__main__":
    main()
