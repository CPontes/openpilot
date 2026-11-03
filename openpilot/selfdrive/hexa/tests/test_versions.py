"""Versions (versions.py): one entry per channel version, newest first, in the summaries' 7-character form."""
import re

from openpilot.selfdrive.hexa import versions as V


def test_the_releases_hold_together():
  commits = [r.commit for r in V.RELEASES]
  assert len(commits) == len(set(commits))
  for r in V.RELEASES:
    assert re.fullmatch(r"[0-9a-f]{7}", r.commit) and re.fullmatch(r"[0-9a-f]{7}", r.opendbc), r.commit
    assert re.fullmatch(r"\d\d/\d\d", r.date) and r.title and r.changes, r.commit
  days = [tuple(map(int, r.date.split("/")[::-1])) for r in V.RELEASES]
  assert days == sorted(days, reverse=True)  # newest first
  assert V.RELEASES[-1].commit == "7d2bedb"  # the first version (STATUS, Histórico do canal)
