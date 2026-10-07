"""Pull finished games from ESPN and report the distribution of final totals.

This is the one honest research move available: the posted total is a number, and
the base rate of games clearing it is measurable from actual results. No roster
knowledge required, nothing invented.
"""
import json, subprocess, sys, time

BASE = "https://site.api.espn.com/apis/site/v2/sports"

def fetch(url):
    for _ in range(3):
        p = subprocess.run(["curl","-s","--compressed","--max-time","40",url], capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip().startswith("{"):
            try: return json.loads(p.stdout)
            except ValueError: pass
        time.sleep(1.0)
    return None

def days(sport, league, dates, seasontype=None):
    """dates is a YYYYMMDD-YYYYMMDD range ESPN accepts."""
    url = f"{BASE}/{sport}/{league}/scoreboard?dates={dates}&limit=400"
    if seasontype: url += f"&seasontype={seasontype}"
    d = fetch(url)
    out = []
    for e in (d or {}).get("events", []):
        c = e["competitions"][0]
        if (c.get("status",{}).get("type",{}).get("name") or "") != "STATUS_FINAL":
            continue
        try:
            sc = [int(x["score"]) for x in c["competitors"]]
        except (KeyError, ValueError, TypeError):
            continue
        if len(sc) != 2: continue
        out.append({"name": e.get("shortName") or e.get("name"),
                    "date": e.get("date","")[:10],
                    "total": sum(sc), "margin": abs(sc[0]-sc[1]), "scores": sc})
    return out

def report(label, games, lines):
    if not games:
        print(f"{label}: no finished games found"); return
    t = sorted(g["total"] for g in games)
    n = len(t)
    mean = sum(t)/n
    med = t[n//2]
    print(f"\n{label}  n={n}  mean total {mean:.2f}  median {med}  range {t[0]}-{t[-1]}")
    for L in lines:
        over = sum(1 for x in t if x > L)
        under = sum(1 for x in t if x < L)
        push = n - over - under
        print(f"   vs {L:>6}:  over {over}/{n} = {over/n:.1%}   under {under/n:.1%}" +
              (f"   push {push}" if push else ""))
    return mean

if __name__ == "__main__":
    pass


def span(sport, league, start, end, seasontype=None, pause=0.3):
    """Every finished game on each calendar day from start to end (datetime.date)."""
    import datetime
    out, d = [], start
    while d <= end:
        url = f"{BASE}/{sport}/{league}/scoreboard?dates={d:%Y%m%d}&limit=400"
        if seasontype: url += f"&seasontype={seasontype}"
        j = fetch(url)
        for e in (j or {}).get("events", []):
            c = e["competitions"][0]
            if (c.get("status",{}).get("type",{}).get("name") or "") != "STATUS_FINAL":
                continue
            try: sc = [int(x["score"]) for x in c["competitors"]]
            except (KeyError, ValueError, TypeError): continue
            if len(sc) != 2: continue
            out.append({"name": e.get("shortName"), "date": f"{d}",
                        "total": sum(sc), "margin": abs(sc[0]-sc[1]), "scores": sc})
        time.sleep(pause)
        d += datetime.timedelta(days=1)
    return out
