from openpilot.selfdrive.hexa import phone
from openpilot.selfdrive.hexa.options import HexaOption
from openpilot.selfdrive.hexa.phone import VehicleState

DRIVING = HexaOption("A", "direcao", "a", "d", driving=True)
COMFORT = HexaOption("B", "avisos", "b", "d", driving=False)
DEVICE_ONLY = HexaOption("C", "celular", "c", "d", driving=False, phone_writable=False)

OFF = VehicleState(onroad=False)
PARKED = VehicleState(onroad=True, parked=True, standstill=True, engaged=False)
MOVING = VehicleState(onroad=True, parked=False, standstill=False, engaged=True)


def test_can_write_driving_option():
  assert phone.can_write(DRIVING, OFF)[0]
  assert phone.can_write(DRIVING, PARKED)[0]
  assert not phone.can_write(DRIVING, MOVING)[0]
  # every way of not being safely parked is read only
  for vs in (VehicleState(True, parked=True, standstill=False), VehicleState(True, parked=False, standstill=True),
             VehicleState(True, parked=True, standstill=True, engaged=True), VehicleState(onroad=True)):
    ok, reason = phone.can_write(DRIVING, vs)
    assert not ok and reason


def test_can_write_comfort_and_device_only():
  assert phone.can_write(COMFORT, MOVING)[0]
  for vs in (OFF, PARKED, MOVING):
    assert not phone.can_write(DEVICE_ONLY, vs)[0]


def test_local_addresses():
  for ip in ("192.168.43.20", "192.168.0.10", "10.0.0.5", "172.16.3.4", "127.0.0.1", "::1", "fe80::1%wlan0", "fd00::1"):
    assert phone.is_local_address(ip), ip
  for ip in ("8.8.8.8", "100.64.0.1", "2001:4860::8888", "", None, "not-an-ip"):
    assert not phone.is_local_address(ip), ip


def test_pin_lifecycle():
  stored = phone.new_pin(now=1000.)
  pin = phone.parse_pin(stored, now=1000.)
  assert pin is not None and len(pin) == 6 and pin.isdigit()
  assert phone.pin_matches(stored, pin, now=1000. + phone.PIN_TTL - 1)
  assert phone.pin_matches(stored, f" {pin} ", now=1001.)
  assert not phone.pin_matches(stored, pin, now=1000. + phone.PIN_TTL + 1)  # expired
  assert not phone.pin_matches(stored, "000000" if pin != "000000" else "111111", now=1001.)
  assert not phone.pin_matches(None, pin)
  assert phone.parse_pin("12:34") is None
  assert phone.parse_pin("abcdef:9999999999") is None


def test_tokens():
  tokens: list[dict] = []
  issued = []
  for i in range(phone.MAX_TOKENS + 2):
    token, tokens = phone.issue_token(tokens, f"phone {i}", now=float(i))
    issued.append(token)
  assert len(tokens) == phone.MAX_TOKENS
  assert phone.find_token(tokens, issued[0]) is None  # oldest dropped
  entry = phone.find_token(tokens, issued[-1])
  assert entry is not None and entry["name"] == f"phone {phone.MAX_TOKENS + 1}"
  assert all(issued[-1] not in str(t) for t in tokens), "only the hash is stored"
  assert phone.find_token(tokens, None) is None and phone.find_token(tokens, "wrong") is None
  tokens = phone.revoke_token(tokens, entry["id"])
  assert phone.find_token(tokens, issued[-1]) is None


def test_token_name_is_bounded():
  _, tokens = phone.issue_token([], "x" * 200)
  assert len(tokens[0]["name"]) == 40
  _, tokens = phone.issue_token([], "  ")
  assert tokens[0]["name"] == "celular"


def test_audit_is_capped():
  audit = None
  for i in range(phone.MAX_AUDIT + 10):
    audit = phone.add_audit(audit, {"event": "set", "n": i}, now=float(i))
  assert len(audit) == phone.MAX_AUDIT
  assert audit[-1]["n"] == phone.MAX_AUDIT + 9 and audit[-1]["t"] == phone.MAX_AUDIT + 9


def test_panel_url():
  assert phone.panel_url("192.168.0.10") == f"http://192.168.0.10:{phone.PORT}"
  assert phone.local_ip()  # never empty: falls back to the hotspot address


def test_pair_url_has_no_secret():
  url = phone.pair_url("192.168.0.10")
  assert url == f"http://192.168.0.10:{phone.PORT}/#parear"
