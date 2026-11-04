#!/usr/bin/env bash
# Takes a new snapshot of the Haval canal (CPontes/openpilot, h6gt) into this repository's "canal-h6gt" branch.
# The snapshots carry no history (openpilot's is too big): each one is a commit whose tree is the canal's, on top of the
# previous snapshot, so "lizpilot" can merge them. Nothing here touches the canal. See docs/LIZPILOT.md.
set -euo pipefail

CANAL_URL="${CANAL_URL:-https://github.com/CPontes/openpilot}"
CANAL_BRANCH="${CANAL_BRANCH:-h6gt}"
MIRROR="${MIRROR:-canal-h6gt}"

git fetch --depth=1 "$CANAL_URL" "$CANAL_BRANCH"
canal="$(git rev-parse FETCH_HEAD)"
tree="$(git rev-parse "$canal^{tree}")"

parent=""
if git fetch origin "$MIRROR" 2>/dev/null; then
  parent="$(git rev-parse "origin/$MIRROR")"
  if [ "$(git rev-parse "$parent^{tree}")" = "$tree" ]; then
    echo "$MIRROR already has the canal at ${canal:0:7}"
    exit 0
  fi
fi

snap="$(git commit-tree "$tree" ${parent:+-p "$parent"} -m "canal: $CANAL_BRANCH @ ${canal:0:7}

Snapshot of $CANAL_URL $CANAL_BRANCH, commit $canal.")"
# --no-verify: the large files are git-lfs pointers to commaai's own store (.lfsconfig), nothing to upload from here
git push --no-verify origin "$snap:refs/heads/$MIRROR"
echo "$MIRROR -> ${snap:0:7} (canal ${canal:0:7}). Next: merge origin/$MIRROR into a branch off lizpilot, test, open a PR."
