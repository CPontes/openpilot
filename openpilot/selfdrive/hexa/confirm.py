"""Confirm on the comma: the phone asks, the driver slides on the comma's screen, the same 4 digits on both.

A paired phone can't, by itself, open the device (SSH) or change how openpilot behaves: someone at the comma has to
agree. hexaweb writes the request; the comma's main layout shows it (selfdrive/ui/mici/layouts/main.py) and writes
the answer; hexaweb applies it only if the answer matches and the request is still fresh.

Times are monotonic (shared by all processes until reboot) and both params are cleared on manager start.
"""
import json
import secrets
import time

REQUEST_PARAM = "HexaConfirmRequest"
ANSWER_PARAM = "HexaConfirmAnswer"
CONFIRM_S = 60.


def new_request(action: dict, label: str, who: str, now: float | None = None) -> dict:
  now = time.monotonic() if now is None else now
  return {"id": secrets.token_hex(8), "code": f"{secrets.randbelow(10000):04d}", "action": action, "label": label[:60],
          "who": who[:40], "expires": now + CONFIRM_S}


def parse(raw) -> dict | None:
  """The stored request, or None if missing or malformed."""
  if isinstance(raw, (bytes, str)):
    try:
      raw = json.loads(raw)
    except ValueError:
      return None
  if not isinstance(raw, dict) or not all(k in raw for k in ("id", "code", "label", "expires")):
    return None
  return raw


def fresh(req: dict | None, now: float | None = None) -> bool:
  now = time.monotonic() if now is None else now
  return req is not None and now <= float(req["expires"])


def confirmed(req: dict | None, answer, now: float | None = None) -> bool:
  if isinstance(answer, bytes):
    answer = answer.decode(errors="replace")
  return fresh(req, now) and isinstance(answer, str) and answer == req["id"]


def seconds_left(req: dict | None, now: float | None = None) -> int:
  now = time.monotonic() if now is None else now
  return max(0, int(float(req["expires"]) - now)) if req else 0
