"""Fetch exact final scores from Polymarket's event state.

No scoreboard site is reachable from this environment, and web-search summaries
hand back live scores labelled as finals. Polymarket's league feed is reachable and
carries the real thing: per-quarter scores, a combined "score" string, and an
`ended` / `period: FT` flag that distinguishes a finished game from one in play.

    python picks/scores.py nfl                              # every game's state
    python picks/scores.py picks/2026-09-20-nfl.json --write # fill final_scores
"""
import json, re, sys, urllib.request

PM = "https://gateway.polymarket.us"
LEAGUE_OF = {"nfl": "nfl", "NFL": "nfl", "cfb": "cfb", "NCAAF": "cfb",
             "ATP": "atp", "WTA": "wta", "BOXING": "boxing"}


def get(url):
    rq = urllib.request.Request(url, headers={"User-Agent": "scores/1.0",
                                             "Accept": "application/json"})
    with urllib.request.urlopen(rq, timeout=45) as r:
        return json.load(r)


EVENT_SLUG = re.compile(r"(nfl|mlb|wnba|cfb|nba|nhl|atp|wta|boxing)-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}")


def by_exact_slug(slug):
    """A market slug carries its event slug: asc-nfl-nyg-lar-2026-09-21-pos-6pt5."""
    m = EVENT_SLUG.search(slug or "")
    if not m:
        return None
    try:
        return (get(f"{PM}/v1/events/slug/{m.group(0)}") or {}).get("event")
    except Exception:
        return None


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


def sets_won(score, teams):
    """Tennis reports games per set, not points: "6-2, 4-6, 7-5", with the game in
    progress appended to the last set as "0-0:40-15". A match is won on sets, so
    count sets rather than adding games -- 6-2, 4-6, 7-5 is 2-1, not 17-13."""
    if not isinstance(score, str) or len(teams) != 2:
        return None
    won = [0, 0]
    for chunk in score.split(","):
        pair = chunk.split(":")[0].strip().split("-")
        if len(pair) != 2:
            return None
        try:
            a, b = int(pair[0]), int(pair[1])
        except ValueError:
            return None
        if a > b:
            won[0] += 1
        elif b > a:
            won[1] += 1
    return dict(zip(teams, won)) if any(won) else None


def state(e):
    """{'title','final','score':{ABBR:pts}} — score is None until the game ends."""
    es = e.get("eventState") or {}
    teams = [(t.get("displayAbbreviation") or "").upper() for t in e.get("teams", [])]
    ids = [str(t.get("id")) for t in e.get("teams", [])]
    # a finished game reports FT or VFT (verified full time), and an overtime game
    # reports its own marker, so trust the flags rather than matching a period string
    final = bool(es.get("ended")) and not es.get("live")
    if (es.get("type") or "") == "tennis":
        return {"title": e.get("title"), "final": final, "in_play": bool(es.get("live")),
                "period": es.get("period"), "score": sets_won(es.get("score"), teams)
                if final else None}
    pts = {}
    for per in (es.get("periodScores") or []):
        for s in (per.get("scores") or []):
            cid = str(s.get("competitorId"))
            if cid in ids:
                pts[teams[ids.index(cid)]] = pts.get(teams[ids.index(cid)], 0) + int(s.get("score") or 0)
    # The flat "score" string runs in teams-array order. Baseball and basketball
    # events arrive with periodScores empty, so it is the only source there; where
    # both exist it cross-checks the per-period sum.
    flat = es.get("score")
    pair = None
    if isinstance(flat, str) and "-" in flat:
        try:
            pair = [int(x) for x in flat.split("-", 1)]
        except ValueError:
            pair = None
    if final and pair and len(teams) == 2:
        if not pts:
            pts = dict(zip(teams, pair))
        elif sorted(pts.values()) != sorted(pair):
            pts = {}                          # the two disagree: trust neither
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
        slugs = {pk["game"]: pk.get("market_slug")
                 for who in card.get("players", {}).values() for pk in who if pk.get("market_slug")}
        for title in sorted(want):
            # the event slug embedded in a market slug is exact; the title is a fallback
            # and its separator varies by league ("A vs B" in the NFL, "A vs. B" elsewhere)
            e = by_exact_slug(slugs.get(title))
            if e is None:
                halves = re.split(r"\s+vs\.?\s+", title, maxsplit=1)
                if len(halves) == 2:
                    away, home = (h.strip().split()[0] for h in halves)
                    e = by_slug(league, away, home, card["date"])
            if e is None:
                unfinished.append((title, "not found"))
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
