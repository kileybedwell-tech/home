"""Measure whether the book's posted number is biased, rung by rung.

Yesterday's NBA-preseason trap was comparing a league-wide scoring average to a
single game's posted total: the line already knows who is resting, so the gap is
manufactured. The valid version conditions on the line itself -- among games where
the book posted exactly 6.5, how often did the over land? If that is 50% the number
is honest and there is nothing to take. If it is 56% at some rung, that rung is
mispriced and the edge is real and mechanical.

ESPN drops odds from the scoreboard once a game is final but keeps them on
summary?event=<id> under `pickcenter`, so the posted total is recoverable per game.

    python picks/lineaudit.py nhl 2025-10-07 2026-04-16
    python picks/lineaudit.py cfb 2025-08-23 2026-01-20 --seasontype 2
"""
import json, subprocess, sys, time, datetime, os

BASE = "https://site.api.espn.com/apis/site/v2/sports"
SPORT = {"nhl": "hockey/nhl", "nba": "basketball/nba", "mlb": "baseball/mlb",
         "cfb": "football/college-football", "nfl": "football/nfl",
         "wnba": "basketball/wnba"}


def fetch(url, tries=3):
    for i in range(tries):
        p = subprocess.run(["curl", "-s", "--compressed", "--max-time", "40", url],
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip().startswith("{"):
            try: return json.loads(p.stdout)
            except ValueError: pass
        time.sleep(1.0 * (i + 1))
    return None


def finals(league, start, end, seasontype=None, pause=0.25):
    """[(event_id, total_points)] for every finished game in the range."""
    out, d = [], start
    while d <= end:
        url = f"{BASE}/{SPORT[league]}/scoreboard?dates={d:%Y%m%d}&limit=400"
        if seasontype: url += f"&seasontype={seasontype}"
        j = fetch(url)
        for e in (j or {}).get("events", []):
            c = e["competitions"][0]
            if (c.get("status", {}).get("type", {}).get("name") or "") != "STATUS_FINAL":
                continue
            try: sc = [int(x["score"]) for x in c["competitors"]]
            except (KeyError, ValueError, TypeError): continue
            if len(sc) == 2:
                out.append((e["id"], sum(sc), f"{d}"))
        time.sleep(pause)
        d += datetime.timedelta(days=1)
    return out


def posted(league, eid, pause=0.25):
    """The book's total and spread for a finished game, or (None, None)."""
    j = fetch(f"{BASE}/{SPORT[league]}/summary?event={eid}")
    time.sleep(pause)
    for o in (j or {}).get("pickcenter") or []:
        ou = o.get("overUnder")
        if ou is not None:
            return float(ou), o.get("spread")
    return None, None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    league, s, e = args[0], args[1], args[2]
    st = sys.argv[sys.argv.index("--seasontype") + 1] if "--seasontype" in sys.argv else None
    cache = f"/tmp/lineaudit-{league}-{s}-{e}.json"
    if os.path.exists(cache):
        rows = json.load(open(cache))
    else:
        games = finals(league, datetime.date.fromisoformat(s),
                       datetime.date.fromisoformat(e), st)
        print(f"{len(games)} finished games; pulling posted lines", flush=True)
        rows = []
        for i, (eid, total, day) in enumerate(games, 1):
            ou, sp = posted(league, eid)
            if ou is not None:
                rows.append({"id": eid, "date": day, "total": total, "posted": ou})
            if i % 100 == 0:
                print(f"  {i}/{len(games)}  ({len(rows)} with a line)", flush=True)
        json.dump(rows, open(cache, "w"))
    report(rows)


def report(rows):
    from collections import defaultdict
    import math
    by = defaultdict(list)
    for r in rows:
        by[r["posted"]].append(r["total"])
    print(f"\n{len(rows)} games carrying a posted total\n")
    print(f"{'posted':>8}{'n':>6}{'over':>7}{'under':>7}{'push':>6}{'over %':>9}{'95% band':>18}")
    tot_o = tot_n = 0
    for L in sorted(by):
        v = by[L]
        o = sum(1 for x in v if x > L); u = sum(1 for x in v if x < L)
        pu = len(v) - o - u
        g = o + u
        if g == 0: continue
        p = o / g; se = math.sqrt(p * (1 - p) / g)
        tot_o += o; tot_n += g
        flag = ""
        if g >= 60 and abs(p - 0.5) > 1.96 * se:
            flag = "  <-- off 50% beyond error"
        print(f"{L:>8}{len(v):>6}{o:>7}{u:>7}{pu:>6}{p:>8.1%}"
              f"   [{max(0,p-1.96*se):.1%}, {min(1,p+1.96*se):.1%}]{flag}")
    if tot_n:
        p = tot_o / tot_n; se = math.sqrt(p * (1 - p) / tot_n)
        print(f"\n  pooled: over {tot_o}/{tot_n} = {p:.2%}  "
              f"+-{1.96*se:.2%}   (50% is an honest line)")


if __name__ == "__main__":
    main()
