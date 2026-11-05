"""LizPilot: the comma's recent errors, sent to the developers when the owner taps "Enviar para análise" (ERR-01).

Nothing leaves by itself: the phone shows what goes (build(), through /api/errors/preview), the owner confirms, and the comma sends this small JSON at once,
signed with the same random share key as the clips (share.Sharer.report). It carries:
- the errors of the "Erros" card (the newest REPORT_ERRORS), each with its process, message and exception line, cleaned (clean());
- how long ago each happened, in minutes, and only when the comma's clock was right then;
- the version, the edition, the car's brand and model, the last disengagement and the processes that are down.

Never the dongle id, the serial, the VIN, a position or a route: paths under /data/media/0/realdata/<route> become "<rota>",
route names, long hex ids, addresses, e-mails and coordinates are taken out, and the whole package is refused if the dongle id, the
serial or the VIN are still anywhere in it (check()).

Some errors are known and harmless (KNOWN): the phone shows them as "normal, sem ação", behind "ver todos". An error logged before
the comma's clock was set (right after it turns on, without network) has a false date: clock_ok marks it.
"""
import json
import re
from datetime import datetime

from openpilot.selfdrive.hexa import share
from openpilot.selfdrive.hexa.phone import wall_time

FORMAT_VERSION = 1
REPORT_ERRORS = 20
TEXT_MAX = 200
# all cars (CTO's proposal, ERR-01: the testers' errors are the ones we need most); the owner confirms before the channel.
# To keep it on the Haval only: BRANDS = edition.GWM_ONLY.
BRANDS: tuple[str, ...] = ()

# the comma's clock: an error before this, or more than a day ahead, was logged before the clock was set
PLAUSIBLE_AFTER = 1767225600  # 2026-01-01 UTC: no LizPilot error is older
CLOCK_NEAR_S = 3600.  # errors this close to one that says the clock is wrong share its wrong clock

# (code, processes it comes from or None for any, what the error says): the phone explains each code (index.html KNOWN_TEXT)
KNOWN = (
  # the comma's cleanup deleted an old segment while the uploader was going through it: the uploader skips it [carro, 05/11]
  ("limpeza", ("uploader", "deleter"), re.compile(r"FileNotFoundError|No such file or directory")),
  # athenad's connection to comma dropped (a network change, turning off): it reconnects by itself [carro, 05/11]
  ("conexao", None, re.compile(r"WebSocketConnectionClosedException|socket is already closed|Connection to remote host was lost")),
  # right after turning on, before the clock is set: the certificates look "not yet valid" [carro, 05/11]
  ("relogio", None, re.compile(r"certificate is not yet valid|System time is not valid")),
)

_ROUTE_PATH = re.compile(r"/data/media/0/realdata/[^\s'\",)\]]*")
_ROUTE = re.compile(r"\b[0-9a-f]{8}--[0-9a-f]{10}(?:--\d+)?\b|\b\d{4}-\d{2}-\d{2}--\d{2}-\d{2}-\d{2}(?:--\d+)?\b")
_HEX_ID = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_VIN = re.compile(r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b")
_POS = re.compile(r"-?\d{1,3}\.\d{3,}\s*[,; /]\s*-?\d{1,3}\.\d{3,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_URL = re.compile(r"\b((?:https?|wss?)://[A-Za-z0-9.-]+)[^\s'\"]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")


def clean(text, hide=()) -> str:
  """One line of an error, without anything that says whose comma, which car or where."""
  s = _CONTROL.sub(" ", str(text or ""))
  for h in hide:
    if h and len(h) >= share.MIN_SECRET:
      s = re.sub(re.escape(h), "<id>", s, flags=re.I)
  s = _ROUTE_PATH.sub("<rota>", s)
  s = _ROUTE.sub("<rota>", s)
  s = _URL.sub(r"\1/…", s)
  s = _EMAIL.sub("<email>", s)
  s = _POS.sub("<posição>", s)
  s = _IP.sub("<ip>", s)
  s = _VIN.sub("<vin>", s)
  s = _HEX_ID.sub("<id>", s)
  return re.sub(r"\s+", " ", s).strip()[:TEXT_MAX]


def known(err: dict) -> str | None:
  text = f"{err.get('msg') or ''} {err.get('error') or ''}"
  for code, processes, pattern in KNOWN:
    if (processes is None or str(err.get("process") or "") in processes) and pattern.search(text):
      return code
  return None


def _as_ts(v) -> float | None:
  if isinstance(v, datetime):
    return v.timestamp()
  if isinstance(v, bytes | bytearray):
    v = v.decode("utf-8", "ignore")
  if isinstance(v, str) and v.strip():
    try:
      return datetime.fromisoformat(v.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
      return None
  return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def clock_floor(store, now: float) -> float:
  """Before this, the comma's clock was surely wrong: the install date (when it isn't itself ahead of now), or PLAUSIBLE_AFTER."""
  try:
    install = _as_ts(store.get("InstallDate"))
  except Exception:
    install = None
  return max(PLAUSIBLE_AFTER, install) if install is not None and install <= now else PLAUSIBLE_AFTER


def annotate(errors: list[dict], floor: float, now: float) -> list[dict]:
  """Each error with known (a KNOWN code or None) and clock_ok (False: logged before the clock was set, its date is false)."""
  out = [dict(e, known=known(e)) for e in errors if isinstance(e, dict)]
  wrong = [float(e.get("t") or 0) for e in out if e["known"] == "relogio"]
  for e in out:
    t = float(e.get("t") or 0)
    e["clock_ok"] = floor <= t <= now + 86400 and not any(abs(t - w) <= CLOCK_NEAR_S for w in wrong)
  return out


def _ago_min(t, ok: bool, now: float) -> int | None:
  return max(0, round((now - float(t)) / 60)) if ok and isinstance(t, int | float) and not isinstance(t, bool) else None


def _hidden(store) -> list[str]:
  _, vin = share.car_info(store)
  return [h for h in (share._text_param(store, "DongleId"), share._text_param(store, "HardwareSerial"), vin) if h]


def build(store, health: dict, edition_name: str, now: float | None = None) -> dict:
  """What goes: exactly what the phone shows before the owner confirms."""
  now = wall_time() if now is None else now  # the errors' dates are wall time too
  hide = _hidden(store)
  errors = annotate(list(health.get("errors") or [])[:REPORT_ERRORS], clock_floor(store, now), now)
  off = health.get("last_off") if isinstance(health.get("last_off"), dict) else None
  down = health.get("down")
  info, _ = share.car_info(store)
  return {
    "format": FORMAT_VERSION,
    "software": {"edition": edition_name, "branch": share._text_param(store, "GitBranch")[:40],
                 "commit": share._text_param(store, "GitCommit")[:12], "version": share._text_param(store, "Version")[:20]},
    "car": {"brand": info["brand"], "model": info["model"]},
    "errors": [{"ago_min": _ago_min(e.get("t"), e["clock_ok"], now), "clock_ok": e["clock_ok"], "known": e["known"],
                "process": clean(e.get("process"), hide)[:40], "msg": clean(e.get("msg"), hide), "error": clean(e.get("error"), hide)}
               for e in errors],
    "last_off": {"ago_min": _ago_min(off.get("t"), True, now) if (off.get("t") or 0) >= clock_floor(store, now) else None,
                 "why": [clean(w, hide)[:40] for w in (off.get("why") or [])][:10]} if off else None,
    "down": [clean(p, hide)[:40] for p in down][:30] if isinstance(down, list) else None,
  }


def encode(report: dict) -> bytes:
  return json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def check(store, data: bytes) -> None:
  """The last look before it leaves: the dongle id, the serial or the VIN anywhere in it, and nothing goes."""
  low = data.lower()
  for h in _hidden(store):
    if len(h) >= share.MIN_SECRET and h.lower().encode() in low:
      raise share.ShareError("O relatório ainda tinha um dado do comma ou do carro: não foi enviado.")
