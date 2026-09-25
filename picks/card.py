"""Build today's coin-flip card from Polymarket: spreads and totals, side by seeded toss."""
import json, hashlib, urllib.request
from datetime import datetime, timezone, timedelta

PM="https://gateway.polymarket.us"; PT=timezone(timedelta(hours=-7))
SEED="20260925"
def get(u): return json.load(urllib.request.urlopen(urllib.request.Request(
    u,headers={"User-Agent":"card/1.0","Accept":"application/json"}),timeout=45))
def q(x): return float(x["value"]) if isinstance(x,dict) and x.get("value") is not None else None

def toss(key):
    """Deterministic, auditable: sha256(seed|market) -> first side or second."""
    return int(hashlib.sha256(f"{SEED}|{key}".encode()).hexdigest(),16) % 2

def games(league, today):
    out=[]
    for e in get(f"{PM}/v2/leagues/{league}/events?limit=100&offset=0&type=sport&section=general").get("events") or []:
        ts=e.get("eventDate") or e.get("startTime")
        if not ts: continue
        dt=datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(PT)
        if dt.date()==today and len(e.get("teams") or [])==2:
            e["_start"]=dt; out.append(e)
    return sorted(out,key=lambda e:e["_start"])

def flips(e, cap=0.53):
    floor=1-cap
    full=get(f"{PM}/v1/events/{e['id']}").get("event") or e
    ab=[(t.get("displayAbbreviation") or "").upper() for t in full.get("teams",[])]
    best={}
    for m in full.get("markets") or []:
        smt=(m.get("sportsMarketType") or "").lower()
        if not (smt.endswith("_team_full_game_spread") or smt.endswith("_team_full_game_total")):
            continue
        bid,ask=q(m.get("bestBidQuote")),q(m.get("bestAskQuote"))
        if bid is None or ask is None or ask-bid>0.04: continue
        mid=(bid+ask)/2
        line=float(m["line"]); short=(m.get("titleShort") or "").strip()
        if smt.endswith("spread"):
            # YES is ALWAYS teams[0] getting the signed line; titleShort names the
            # other team on positive lines and always prints a minus sign.
            if len(ab)!=2: continue
            a,b=ab
            sides=[(f"{a} {line:+g}",{"kind":"spread","team":a,"line":line},mid),
                   (f"{b} {-line:+g}",{"kind":"spread","team":b,"line":-line},1-mid)]
            kind="spread"
        else:
            sides=[(f"OVER {line:g}",{"kind":"total","side":"over","line":line},mid),
                   (f"UNDER {line:g}",{"kind":"total","side":"under","line":line},1-mid)]
            kind="total"
        pick=sides[toss(m.get("slug") or short)]
        if not (floor<=pick[2]<=cap): continue
        if kind not in best or abs(pick[2]-.5)<abs(best[kind]["pct"]-.5):
            best[kind]={"kind":kind,"label":pick[0],"resolve":pick[1],"pct":pick[2],
                        "slug":m.get("slug"),"bid":bid,"ask":ask,
                        "side":"YES" if pick[1]==sides[0][1] else "NO"}
    return [best[k] for k in ("spread","total") if k in best]

LEAGUE={"cfb":"NCAAF","mlb":"MLB"}
now=datetime.now(PT); rows=[]
for lg in ("cfb","mlb"):
    for e in games(lg,now.date()):
        if e["_start"]<=now: continue            # no in-play
        for f in flips(e):
            cost=round(f["ask"] if f["side"]=="YES" else 1-f["bid"],4)
            rows.append({"game":e.get("title"),"league":LEAGUE[lg],
                "kickoff_pt":f"{e['_start']:%Y-%m-%d %H:%M}","live_when_logged":False,
                "decided_pregame":True,"pick":f["label"],"type":f["kind"],
                "market_slug":f["slug"],"market_side":f["side"],"resolve":f["resolve"],
                "implied_win_pct":round(f["pct"],3),"cost":cost,
                "pays_per_100":round(100/cost,1)})
json.dump(rows,open("/tmp/claude-0/-home-user-home/1308a091-cdd0-5722-9977-1fa752b80e70/scratchpad/rows.json","w"),indent=1)
for r in rows: print(f"{r['league']:<6}{r['kickoff_pt'][11:]}  {r['pick']:<20}{r['implied_win_pct']:.0%}  {r['game'][:34]}")
print(len(rows),"candidates")
