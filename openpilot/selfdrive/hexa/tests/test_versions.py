"""Versions (versions.py): one entry per channel version, newest first, in the summaries' 7-character form."""
import re

from openpilot.selfdrive.hexa import versions as V


def test_the_releases_hold_together():
  commits = [r.commit for r in V.RELEASES]
  assert len(commits) == len(set(commits))
  assert "" not in commits[1:]  # only the version being promoted, on top, waits for its own hash
  for r in V.RELEASES:
    assert re.fullmatch(r"([0-9a-f]{7})?", r.commit) and re.fullmatch(r"[0-9a-f]{7}", r.opendbc), r.commit
    assert re.fullmatch(r"\d\d/\d\d", r.date) and r.title and r.changes, r.commit
  days = [tuple(map(int, r.date.split("/")[::-1])) for r in V.RELEASES]
  assert days == sorted(days, reverse=True)  # newest first
  assert V.RELEASES[-1].commit == "7d2bedb"  # the first version (STATUS, Histórico do canal)


def test_the_version_on_top_takes_the_running_commit():
  out = V.public("abcdef123456")["releases"]
  assert out[0]["commit"] == ("abcdef1" if V.RELEASES[0].commit == "" else V.RELEASES[0].commit)
  assert [r["commit"] for r in out[1:]] == [r.commit for r in V.RELEASES[1:]]
  assert V.public("")["releases"][0]["commit"]  # never blank on the screen


def test_off_options_only_where_a_version_brought_one():
  by = {r["commit"]: r for r in V.public("x")["releases"]}
  assert by["00e5ba7"]["options"] and not by["7d2bedb"]["options"] and not by["1bd6c53"]["options"]


def test_the_panda_flag_follows_the_safety_changes():
  # gwm.h changed between each of these and the version before (opendbc diff, review of claude-app-09, 03/11)
  panda = {r.commit for r in V.RELEASES if r.panda}
  assert {"dbb532e", "5be82e8", "544108f", "00a40b8", "3b759f6", "17554ba", "48ecad6", "dea997c"} <= panda
