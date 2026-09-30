#!/bin/sh
# One closing-line tick, safe to run from a scheduled routine in a fresh container.
#
# CLV needs the price a pick's market settled into, and a settled market quotes no
# price -- so the close has to be captured before kickoff or it is gone. A background
# loop cannot do this: it dies with the container. This script takes one snapshot of
# every card dated today and commits it, so the record survives to the next container.
# Runs clean when there is no card, and when every game has already started.
set -e
cd "$(dirname "$0")/.."
BRANCH=claude/sports-picking-routine-9e65n7
TODAY=$(TZ=America/Los_Angeles date +%F)
STAMP=$(TZ=America/Los_Angeles date '+%Y-%m-%d %H:%M %Z')

git fetch -q origin "$BRANCH"
git checkout -q -B "$BRANCH" "origin/$BRANCH"

found=0
for f in picks/"$TODAY"-*.json; do
    [ -e "$f" ] || continue
    found=1
    python3 picks/track.py "$f"
done
if [ "$found" = 0 ]; then
    echo "no card dated $TODAY — nothing to track"
    exit 0
fi

git add -A picks
if git diff --cached --quiet; then
    echo "no price moved — nothing to commit"
    exit 0
fi
git commit -q -m "picks: closing-line snapshot, $STAMP"
git push -q origin "$BRANCH"
echo "committed a snapshot at $STAMP"
