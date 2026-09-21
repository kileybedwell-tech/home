"""Score a pick'em card against Kalshi's settled markets.

Every pick stores its own settle_ticker plus hits_on ("yes"/"no"), so scoring never
depends on re-matching team names, and spreads/totals/moneylines all score the same way.

    python picks/score.py picks/2026-09-19-ncaaf.json
    python picks/score.py picks/2026-09-19-ncaaf.json --write
"""
import json, re, sys, time, urllib.request

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


def result(ticker):
    """'yes' / 'no' once the market settles, else None."""
    event = ticker.rsplit("-", 1)[0]
    d = get(f"{KB}/events/{event}?with_nested_markets=true")
    for m in (((d or {}).get("event") or {}).get("markets") or []):
        if m["ticker"] == ticker:
            return (m.get("result") or "").lower() or None
    return None


def from_score(pick, scores):
    """Resolve a pick from a final score when its Kalshi market has not settled.

    scores maps a team code to its points, e.g. {"CIN": 20, "HOU": 6}. The line and
    the team come from settle_means, which Kalshi writes in a fixed shape:
      total   "Over 45.5 points scored"
      spread  "Houston wins by over 2.5 points"
      money   "Houston"
    """
    means = pick.get("settle_means") or ""
    total = sum(scores.values())
    yes = None
    m = re.match(r"Over\s+([\d.]+)\s+points", means, re.I)
    if m:
        yes = total > float(m.group(1))
    if yes is None:
        m = re.match(r"(.+?)\s+wins by over\s+([\d.]+)\s+points", means, re.I)
        if m:
            team = _match_team(m.group(1), scores)
            if team is None:
                return None
            other = sum(v for k, v in scores.items() if k != team)
            yes = (scores[team] - other) > float(m.group(2))
    if yes is None and pick.get("type") == "moneyline":
        team = _match_team(means, scores)
        if team is None:
            return None
        other = max(v for k, v in scores.items() if k != team)
        yes = scores[team] > other
    if yes is None:
        return None
    return "hit" if (("yes" if yes else "no") == pick["hits_on"]) else "miss"


def _match_team(text, scores):
    """Map Kalshi's team wording onto one of the score keys."""
    t = re.sub(r"[^a-z]", "", text.lower())
    for code in scores:
        c = code.lower()
        if t.startswith(c) or c in t:
            return code
    return None


def settle(p, scores=None):
    """(outcome, detail) where outcome is 'hit' / 'miss' / 'push' / None if unsettled."""
    r = result(p["settle_ticker"])
    if r is None:
        # Kalshi can lag well past the final whistle; fall back to a reported score.
        if scores:
            out = from_score(p, scores)
            if out:
                return out, "from final score"
        return None, "pending"
    hit = r == p["hits_on"]
    # A whole-number spread can push. push_ticker is the rung one point lower: when the
    # pick lost outright but that rung also settled the other way, the margin landed
    # exactly on the number.
    if not hit and p.get("push_ticker"):
        if result(p["push_ticker"]) == "yes" and r == "no":
            return "push", "landed exactly on the number"
    if not hit and p.get("push_note") and not p.get("push_ticker"):
        return "miss", "check final score: could be a push, no market at the half-point"
    return ("hit" if hit else "miss"), r


def main(path, write=False):
    card = json.load(open(path))
    totals = {}
    for player, picks in card["players"].items():
        hits = misses = pushes = pending = 0
        print(f"\n=== {player} ({len(picks)} picks)")
        for p in picks:
            out, detail = settle(p, (card.get('final_scores') or {}).get(p['game']))
            p["result"] = out
            flag = " [live when logged]" if p.get("live_when_logged") else ""
            if out is None:
                pending += 1; mark = "pending"
            elif out == "push":
                pushes += 1; mark = "PUSH"
            elif out == "hit":
                hits += 1; mark = "HIT "
            else:
                misses += 1; mark = "miss"
            print(f"  {mark}  {p['pick']:<38}{flag}  ({detail})")
        graded = hits + misses
        totals[player] = (hits, graded, pushes, pending)
        pct = f" ({hits/graded:.0%})" if graded else ""
        print(f"  -> {hits}/{graded}{pct}, {pushes} push, {pending} pending")

    print("\n=== tally")
    for player, (h, g, pu, pe) in totals.items():
        print(f"  {player}: {h}/{g}" + (f" ({h/g:.0%})" if g else "") +
              (f", {pu} push" if pu else "") + (f", {pe} pending" if pe else ""))
    done = all(pe == 0 for _, _, _, pe in totals.values())
    if done and len(totals) == 2:
        (a, (ha, ga, *_)), (b, (hb, gb, *_)) = totals.items()
        ra, rb = (ha / ga if ga else 0), (hb / gb if gb else 0)
        print(f"  -> {'tie' if ra == rb else (a if ra > rb else b) + ' wins on rate'}")

    if write:
        json.dump(card, open(path, "w"), indent=2)
        print(f"\nwrote results to {path}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "picks/2026-09-19-ncaaf.json", "--write" in sys.argv)
