"""Hexa marks: a bookmark plus the reason, so the logs can be searched by what happened.

Every mark is tied to a real bookmark (userBookmark) in the log:
- marked from the phone: the comma raises its own bookmark, and the reason is attached to it;
- flagged on the comma while driving: it shows up on the phone as "sem motivo", to be classified later, parked.

The reason is recorded in the log as a "hexa_mark" event with the bookmark's logMonoTime, and kept in
the MARKS_PARAM list on the device. tools/analise/marcas.py groups the cases by reason.

To add a default reason, add a line to DEFAULT_REASONS. The owner can also add his own from the phone.
"""
import re
import secrets
import unicodedata

from openpilot.selfdrive.hexa.phone import wall_time

MARKS_PARAM = "HexaMarks"
REASONS_PARAM = "HexaMarkReasons"
BOOKMARK_REQUEST_PARAM = "HexaBookmarkRequest"

# (key, label) in display order. Keys never change once in use: old marks keep pointing to them.
DEFAULT_REASONS: tuple[tuple[str, str], ...] = (
  ("balancou", "balançou sem motivo"),
  ("estrada", "balançou: estrada ruim"),
  ("curva", "curva difícil"),
  ("soltou", "soltou sozinho"),
  ("freada", "freada estranha"),
  ("faixa", "saiu da faixa"),
  ("alerta", "alerta sem motivo"),
  ("outro", "outro"),
)

# On the road (owner, 03/11): what was there, not something openpilot did wrong. Radar, signal and bump are points with a place
# (Pontos); these are marks. "nota" is the copilot's Nota: only the text.
ROAD_REASONS: tuple[tuple[str, str], ...] = (
  ("buraco", "buraco"),
  ("obra", "obra na pista"),
  ("placa", "placa de velocidade"),
)
NOTE_REASON = ("nota", "anotação")

# Marks the comma makes by itself from a drive's sensors (selfdrive/hexa/sensors.py). Not offered on the Marcar sheet.
AUTO_REASONS: tuple[tuple[str, str], ...] = (("brusca", "freada brusca"), ("missao", "missão feita"))  # "missao": the phone's "Fiz agora"
AUTO_REASON = {"hardbrake": "brusca"}
AUTO_SAME_S = 30.  # an automatic mark this close to another of the same kind is the same event

MAX_MARKS = 300
MAX_CUSTOM_REASONS = 20
MAX_LABEL = 24
MAX_NOTE = 500  # a dictated note runs long
ATTACH_WINDOW_NS = 5_000_000_000  # a phone mark adopts the bookmark that arrives within 5 s
MIN_MARK_INTERVAL = 2.0  # s between phone marks from the same phone
MAX_MARK_DELAY = 300.  # s between the tap on the reason and Salvar


def _slug(label: str) -> str:
  text = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
  return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:MAX_LABEL] or "motivo"


def reasons(custom: list[dict] | None) -> list[dict]:
  out = [{"key": k, "label": label, "custom": False} for k, label in DEFAULT_REASONS]
  mine = {str(c.get("label", "")).lower() for c in custom or []}  # a reason of his own with the same name stays the only one
  out += [{"key": k, "label": label, "custom": False, "group": "estrada"} for k, label in ROAD_REASONS if label.lower() not in mine]
  out += [{"key": NOTE_REASON[0], "label": NOTE_REASON[1], "custom": False, "group": "nota"}]
  out += [{"key": c["key"], "label": c["label"], "custom": True} for c in custom or [] if c.get("key") and c.get("label")]
  return out


def reason_label(custom: list[dict] | None, key: str) -> str | None:
  for r in reasons(custom):
    if r["key"] == key:
      return r["label"]
  return dict(AUTO_REASONS).get(key)


def auto_marks(marks: list[dict] | None, found: list[tuple[float, str]]) -> list[dict]:
  """found: (wall time, sensors kind). Adds each as a mark from the comma, once."""
  marks = list(marks or [])
  for t, kind in found:
    reason = AUTO_REASON.get(kind)
    if reason is None or any(m.get("source") == "auto" and abs(m.get("t", 0) - t) < AUTO_SAME_S for m in marks):  # the owner may have reclassified it
      continue
    marks = append(marks, new_mark("auto", "comma", reason=reason, label=dict(AUTO_REASONS)[reason], now=t))
  return marks


def add_reason(custom: list[dict] | None, label: str) -> tuple[list[dict], dict]:
  """Returns (new custom list, the reason). Raises ValueError on a bad or duplicate label."""
  label = " ".join(str(label or "").split())[:MAX_LABEL]
  if not label:
    raise ValueError("Escreva o motivo.")
  custom = list(custom or [])
  existing = reasons(custom)
  if any(r["label"].lower() == label.lower() for r in existing):
    raise ValueError("Esse motivo já existe.")
  if len(custom) >= MAX_CUSTOM_REASONS:
    raise ValueError(f"No máximo {MAX_CUSTOM_REASONS} motivos seus.")
  key, n = "c-" + _slug(label), 2
  keys = {r["key"] for r in existing}
  while key in keys:
    key, n = f"c-{_slug(label)}-{n}", n + 1
  reason = {"key": key, "label": label}
  return custom + [reason], reason


def remove_reason(custom: list[dict] | None, key: str) -> list[dict]:
  """Only custom reasons can be removed; marks that used it keep the key and the label they were given."""
  return [c for c in custom or [] if c.get("key") != key]


def clean_delay(delay) -> float:
  """Seconds between the tap and Salvar: the bookmark comes at Salvar, this says how long before it really happened."""
  if isinstance(delay, bool) or not isinstance(delay, int | float) or not 0 <= delay <= MAX_MARK_DELAY:
    return 0.
  return round(float(delay), 1)


# When it happened, around the tap (owner, 04/11): the instant, the last seconds, or from now on. Seconds before and after
# the mark's time, for whoever reads the log; the bookmark stays at the tap.
WINDOW_MAX_S = 60


def clean_window(win) -> list[int] | None:
  """[before, after] in whole seconds, each 0..WINDOW_MAX_S; None for the instant or anything that isn't two numbers."""
  if not isinstance(win, list | tuple) or len(win) != 2 or any(isinstance(x, bool) or not isinstance(x, int | float) for x in win):
    return None  # a string "12" isn't [1, 2]
  try:
    before, after = (max(0, min(WINDOW_MAX_S, int(x))) for x in win)
  except (OverflowError, ValueError):  # json.loads takes Infinity and NaN
    return None
  return [before, after] if before or after else None


def clean_note(note) -> str:
  return " ".join(str(note or "").split())[:MAX_NOTE]


def new_mark(source: str, who: str = "", mono_ns: int | None = None, reason: str | None = None, label: str | None = None,
             note: str = "", now: float | None = None) -> dict:
  return {
    "id": secrets.token_hex(4),
    "t": int(wall_time() if now is None else now),
    "mono_ns": mono_ns,  # logMonoTime of the bookmark in the log; None until it arrives
    "source": source,  # "celular", "comma" (the flag on the comma) or "auto" (found in the drive by the comma)
    "who": who,
    "reason": reason,
    "label": label,
    "note": note,
  }


def append(marks: list[dict] | None, mark: dict) -> list[dict]:
  return (list(marks or []) + [mark])[-MAX_MARKS:]


def attach_bookmark(marks: list[dict] | None, mono_ns: int) -> tuple[list[dict], dict, bool]:
  """A bookmark arrived from the log. Attach it to the latest phone mark still waiting for one (within
  ATTACH_WINDOW_NS of the previous bookmark-less mark), or create a mark without reason.
  Returns (new list, the mark, created)."""
  marks = list(marks or [])
  for m in reversed(marks):
    if m.get("source") == "celular" and m.get("mono_ns") is None and m.get("_req_mono_ns") is not None \
       and 0 <= mono_ns - m["_req_mono_ns"] <= ATTACH_WINDOW_NS:
      m = dict(m, mono_ns=mono_ns)
      m.pop("_req_mono_ns", None)
      idx = next(i for i, x in enumerate(marks) if x["id"] == m["id"])
      marks[idx] = m
      return marks, m, False
  m = new_mark("comma", mono_ns=mono_ns)
  return append(marks, m), m, True


def set_reason(marks: list[dict] | None, mark_id: str, reason: str, label: str, note: str = "",
               replace_note: bool = False) -> tuple[list[dict], dict]:
  marks = list(marks or [])
  for i, m in enumerate(marks):
    if m.get("id") == mark_id:
      marks[i] = dict(m, reason=reason, label=label, note=clean_note(note) if replace_note else clean_note(note) or m.get("note", ""))
      return marks, marks[i]
  raise KeyError(mark_id)


def log_fields(mark: dict) -> dict:
  """What goes to the log with the 'hexa_mark' event."""
  return {k: mark.get(k) for k in ("id", "t", "mono_ns", "source", "who", "reason", "label", "note", "delay_s", "win")}


def public(mark: dict) -> dict:
  return {k: v for k, v in mark.items() if not k.startswith("_")}
