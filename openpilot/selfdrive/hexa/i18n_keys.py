"""Every Portuguese text the comma's LizPilot page and the phone panel can show: what the catalogs must translate.

Used by tests/test_i18n.py, and by hand to list what a catalog is missing:
  python -m openpilot.selfdrive.hexa.i18n_keys [en|es]
"""
import ast
import json
import re
import sys
from pathlib import Path

from openpilot.selfdrive.hexa import comma_ctl, i18n, live, marks, missions, options, versions

HEXA = Path(__file__).parent
ROOT = HEXA.parents[1]  # openpilot/openpilot
PY_SOURCES = [ROOT / "system/hexaweb/hexaweb.py", HEXA / "phone.py", HEXA / "updates.py", HEXA / "marks.py", HEXA / "comma_ctl.py", HEXA / "missions.py",
              HEXA / "share.py", HEXA / "report.py",
              HEXA / "nav.py",
              HEXA / "speed_limit.py",
              ROOT / "selfdrive/ui/mici/layouts/settings/hexa.py", ROOT / "selfdrive/ui/layouts/settings/hexa.py"]
PAGES = [ROOT / "system/hexaweb/static" / n for n in ("index.html", "viagens.html", "replay.html", "lp-logic.js")]
# objects in the pages whose values are passed through t() by name
PAGE_TABLES = ("UPDATE_ACTIONS", "EVENTS", "RADAR_ACTIONS", "RESUME", "SIDES", "SIDE_CHIPS", "POINT", "KINDS",
               "COMMA_ACTIONS", "LEVEL_TEXT", "SAFETY_TEXT", "SAFETY_NAMES", "MARK_POINTS", "HOW_TEXT", "HOW_LONG", "MOMENT_TEXT", "MARK_RESULT",
               "EVENT_UI", "STAGE_TEXT", "EV_BUTTONS", "EV_BY", "MARK_WIN", "TILE_TEXT", "SHARE_TEXT", "KNOWN_TEXT", "GO_TEXT", "GO_SUB")
FIELD = re.compile(r"\{[^{}]*\}")
PT = re.compile(r"[a-zà-ú]", re.I)


def norm(text: str) -> str:
  """Templates match whatever their fields are called."""
  return FIELD.sub("{}", text)


def _fstring(node: ast.JoinedStr) -> str:
  return "".join(v.value if isinstance(v, ast.Constant) else "{x}" for v in node.values)


def _text(node) -> str | None:
  if isinstance(node, ast.Constant) and isinstance(node.value, str):
    return node.value
  if isinstance(node, ast.JoinedStr):
    return _fstring(node)
  if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
    a, b = _text(node.left), _text(node.right)
    return a + b if a is not None and b is not None else None
  return None


def python_texts() -> set[str]:
  out: set[str] = set()
  for path in PY_SOURCES:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
      if isinstance(node, ast.Dict):  # {"error": "..."} and {"message": "..."}
        for k, v in zip(node.keys, node.values, strict=True):
          if isinstance(k, ast.Constant) and k.value in ("error", "message") and (txt := _text(v)):
            out.add(txt)
      elif isinstance(node, ast.Call):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name in ("tr",) and node.args and (txt := _text(node.args[0])):  # comma screen
          out.add(txt)
        if name in ("ValueError", "ShareError", "NavError", "InstallError") and node.args and (txt := _text(node.args[0])) and PT.search(txt) and " " in txt:
          out.add(txt)
        for kw in node.keywords:  # share.py: a queue item's error=, shown on the phone
          if kw.arg == "error" and (txt := _text(kw.value)) and " " in txt and PT.search(txt):
            out.add(txt)
      elif isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):  # (False, "reason") from can_write / can_update
        for elt in node.value.elts:
          if (txt := _text(elt)) and " " in txt and PT.search(txt):
            out.add(txt)
  return {t for t in out if PT.search(t)}


def option_texts() -> set[str]:
  out = {title for _, title in options.GROUPS}
  for o in options.OPTIONS:
    out |= {o.title, o.description, *o.choices}
    if o.blocked:
      out.add(o.blocked)
  out |= {label for _, label in marks.DEFAULT_REASONS + marks.AUTO_REASONS + marks.ROAD_REASONS + (marks.NOTE_REASON,)}
  out |= comma_ctl_texts()
  out |= set(live.EVENT_TEXT.values())
  out |= mission_texts()
  out |= {x for r in versions.RELEASES for x in (r.title, *r.changes)}
  return out


def mission_texts() -> set[str]:
  """The missions as the phone shows them (missions.py)."""
  out = {title for _, title in missions.TRAILS} | set(missions.RESULTS.values()) | set(missions.TRY_RESULTS.values())
  for m in dict.fromkeys(missions.CATALOG + missions.APPROVAL):  # the approval of a new car shows in the generic edition (MISS-01)
    out |= {m.title, m.note, m.why, m.proof, m.lock, m.goal, *m.steps}
  out |= {t.measured for tries in missions.TRIES.values() for t in tries}
  pk = missions.ROUTE
  out |= {pk["intro"], pk["during"], pk["back"], pk["after"], *pk["before"], *pk["stop_if"]}  # "old" holds commits, not text
  return {x for x in out if x}


def comma_ctl_texts() -> set[str]:
  """The comma's own settings as the phone shows them (comma_ctl.py)."""
  out = {title for _, title in comma_ctl.GROUPS} | {label for _, label in comma_ctl.LONG_STEPS}
  out |= set(comma_ctl.AEB_STEP.values()) | set(comma_ctl.HAVAL_LONG_WARNINGS)  # both editions (edition.py)
  for it in comma_ctl.ITEMS:
    out |= {it.title, it.description, *it.warnings, comma_ctl.effect(it)}
    if it.blocked:
      out.add(it.blocked)
  for _, title, text in comma_ctl.DEVICE_ACTIONS:
    out |= {title, text}
  return out


_STR = r'"((?:[^"\\]|\\.)*)"'


def _js_unescape(s: str) -> str:
  return json.loads(f'"{s}"')


def _calls(script: str):
  """The source inside every t(...), T(...), tf(...) and TF(...) call, strings and nested calls included."""
  for m in re.finditer(r"(?<![\w.$])(?:t|T|tf|TF)\(", script):
    depth, i, quote = 1, m.end(), None
    while i < len(script) and depth:
      c = script[i]
      if quote:
        if c == "\\":
          i += 1
        elif c == quote:
          quote = None
      elif c in "\"'`":
        quote = c
      elif c == "(":
        depth += 1
      elif c == ")":
        depth -= 1
      i += 1
    yield script[m.end():i - 1]


def _table_values(body: str) -> set[str]:
  out = set()
  for a, b in re.findall(_STR + r",\s*" + _STR + r"\]", body):  # ["code", "label"] or ["label", "--color"]
    out.add(a if b.startswith("--") else b)
  for v in re.findall(r"(?:\w+|" + _STR + r"):\s*" + _STR, body):  # key: "label"
    out.add(v[-1])
  return out


def page_texts() -> set[str]:
  out: set[str] = set()
  for path in PAGES:
    src = path.read_text()
    script = src if path.suffix == ".js" else src[src.find("<script"):]  # lp-logic.js marks its texts with T()
    for m in re.finditer(r"<[a-z0-9]+(?=[^>]*\sdata-t[\s>])[^>]*>([^<]+)<", src):  # <x data-t>texto</x>
      out.add(m.group(1).strip())
    for m in re.finditer(r'data-t-(?:ph|aria)="([^"]+)"', src):
      out.add(m.group(1))
    for call in _calls(script):
      out |= {_js_unescape(s) for s in re.findall(_STR, call)}
    for name in PAGE_TABLES:
      m = re.search(r"const " + name + r" = ([\[{].*?[\]}]);\n", script, re.S)
      if m:
        out |= {_js_unescape(v) for v in _table_values(m.group(1))}
  return {t for t in out if t and PT.search(t) and not t.startswith("--")}


# shown as is in every language: names, units and codes
SAME = {"2-digit", "LizPilot", "openpilot", "Replay", "ACC", "km", "km/h", "m", "min", "PT", "EN", "ES", "1 s", "2 s", "3 s", "GPS ok"}


def all_texts() -> set[str]:
  return {t for t in python_texts() | option_texts() | page_texts() if t not in SAME}


def missing(lang: str) -> list[str]:
  have = {norm(k) for k, v in i18n.catalog(lang).items() if v}
  return sorted(t for t in all_texts() if norm(t) not in have)


if __name__ == "__main__":
  if len(sys.argv) > 1:
    print(json.dumps(missing(sys.argv[1]), ensure_ascii=False, indent=1))
  else:
    print(json.dumps(sorted(all_texts()), ensure_ascii=False, indent=1))
