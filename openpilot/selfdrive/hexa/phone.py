"""Security rules for the phone panel (system/hexaweb), kept free of I/O so they can be tested.

- Pairing: the comma shows a 6-digit code for PIN_TTL seconds; a phone that sends it gets a random token.
  Only the token's SHA-256 is stored. Codes are single use, and too many wrong codes clear the code.
- Writes: an option that changes how the car drives can only be written with the car off, or parked
  (P, standstill, openpilot not engaged). Some options are never writable from the phone.
- Network: only private, loopback and link-local addresses may talk to the panel.
"""
import hashlib
import hmac
import ipaddress
import secrets
import socket
import time
from dataclasses import dataclass

from openpilot.selfdrive.hexa.options import HexaOption

PIN_TTL = 120.  # s
MAX_TOKENS = 5
MAX_AUDIT = 100
MAX_PIN_FAILURES = 5
PORT = 8088
HOTSPOT_IP = "192.168.43.1"

PIN_PARAM = "HexaWebPin"
TOKENS_PARAM = "HexaWebTokens"
AUDIT_PARAM = "HexaWebAudit"


def wall_time() -> float:
  return time.time()  # noqa: TID251 - token and audit times are shown to the user as dates


@dataclass(frozen=True)
class VehicleState:
  onroad: bool  # ignition on and openpilot started
  parked: bool = False  # gear in P
  standstill: bool = False
  engaged: bool = False


def can_write(option: HexaOption, state: VehicleState) -> tuple[bool, str]:
  if not option.phone_writable:
    return False, "só pelo comma"
  if option.driving and state.onroad and not (state.parked and state.standstill and not state.engaged):
    return False, "carro andando: só leitura"
  return True, ""


def is_local_address(remote: str | None) -> bool:
  if not remote:
    return False
  try:
    ip = ipaddress.ip_address(remote.split("%")[0])
  except ValueError:
    return False
  return ip.is_private or ip.is_loopback or ip.is_link_local


# *** pairing code ***

def new_pin(now: float | None = None) -> str:
  """Returns the value to store in PIN_PARAM: '<6 digits>:<expiry, monotonic clock>'.
  The monotonic clock is shared by all processes until reboot, and PIN_PARAM is cleared on manager start."""
  now = time.monotonic() if now is None else now
  return f"{secrets.randbelow(10**6):06d}:{int(now + PIN_TTL)}"


def parse_pin(stored: str | None, now: float | None = None) -> str | None:
  """The current code, or None if there is none or it expired."""
  now = time.monotonic() if now is None else now
  if not stored or ":" not in stored:
    return None
  pin, expiry = stored.split(":", 1)
  try:
    if now > float(expiry):
      return None
  except ValueError:
    return None
  return pin if len(pin) == 6 and pin.isdigit() else None


def pin_matches(stored: str | None, attempt: str, now: float | None = None) -> bool:
  pin = parse_pin(stored, now)
  return pin is not None and hmac.compare_digest(pin.encode(), str(attempt).strip().encode())


# *** tokens ***

def hash_token(token: str) -> str:
  return hashlib.sha256(token.encode()).hexdigest()


def issue_token(tokens: list[dict] | None, name: str, now: float | None = None) -> tuple[str, list[dict]]:
  """Returns (token for the phone, new token list to store). Keeps at most MAX_TOKENS, dropping the oldest."""
  now = wall_time() if now is None else now
  token = secrets.token_urlsafe(32)
  entry = {"id": secrets.token_hex(4), "hash": hash_token(token), "name": (name or "").strip()[:40] or "celular", "created": int(now)}
  tokens = list(tokens or [])[-(MAX_TOKENS - 1):] + [entry]
  return token, tokens


def find_token(tokens: list[dict] | None, token: str | None) -> dict | None:
  if not token:
    return None
  h = hash_token(token)
  for entry in tokens or []:
    if hmac.compare_digest(str(entry.get("hash", "")), h):
      return entry
  return None


def revoke_token(tokens: list[dict] | None, token_id: str) -> list[dict]:
  return [t for t in tokens or [] if t.get("id") != token_id]


# *** audit ***

def add_audit(audit: list[dict] | None, entry: dict, now: float | None = None) -> list[dict]:
  now = wall_time() if now is None else now
  return (list(audit or []) + [{"t": int(now), **entry}])[-MAX_AUDIT:]


# *** network ***

def local_ip() -> str:
  """Best guess of the address a phone on the same network should open. No packets are sent."""
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
      s.connect(("8.8.8.8", 80))
      ip = s.getsockname()[0]
      if ip and not ip.startswith("127."):
        return ip
  except OSError:
    pass
  return HOTSPOT_IP


def panel_url(ip: str | None = None) -> str:
  return f"http://{ip or local_ip()}:{PORT}"


def pair_url(ip: str | None = None) -> str:
  """What the QR on the comma opens: the panel, straight on the pairing screen. It carries no secret:
  the 6-digit code shown on the comma is still needed."""
  return panel_url(ip) + "/#parear"
