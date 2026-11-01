import json
import threading
import urllib.error
import urllib.request

import pytest

from openpilot.selfdrive.hexa import phone
from openpilot.selfdrive.hexa.phone import VehicleState
from openpilot.system.hexaweb import hexaweb


class FakeStore:
  def __init__(self, **values):
    self.d = dict(values)

  def get(self, key):
    return self.d.get(key)

  def put(self, key, value):
    self.d[key] = value

  def get_bool(self, key):
    return bool(self.d.get(key, False))

  def put_bool(self, key, value):
    self.d[key] = bool(value)

  def remove(self, key):
    self.d.pop(key, None)


class Panel:
  def __init__(self):
    self.now = 1000.
    self.vs = VehicleState(onroad=False)
    self.store = FakeStore(GitBranch="h6gt-next", GitCommit="abcdef1234")
    self.backend = hexaweb.PanelBackend(self.store, lambda: self.vs, clock=lambda: self.now, mono=lambda: self.now)
    self.server = hexaweb.make_server(self.backend, "127.0.0.1", 0)
    self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
    threading.Thread(target=self.server.serve_forever, daemon=True).start()

  def request(self, path, body=None, token=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(self.url + path, data=data, method="GET" if data is None else "POST")
    if data is not None:
      req.add_header("Content-Type", "application/json")
    if token:
      req.add_header("Authorization", "Bearer " + token)
    try:
      with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
      return e.code, dict(e.headers), e.read()

  def json(self, path, body=None, token=None, raw=None):
    status, _, data = self.request(path, body, token, raw)
    return status, json.loads(data)

  def show_pin(self) -> str:
    self.store.put(phone.PIN_PARAM, phone.new_pin(self.now))
    return phone.parse_pin(self.store.get(phone.PIN_PARAM), self.now)

  def pair(self, name="celular de teste") -> str:
    status, body = self.json("/api/pair", {"pin": self.show_pin(), "name": name})
    assert status == 200
    return body["token"]


@pytest.fixture
def panel():
  p = Panel()
  yield p
  p.server.shutdown()
  p.server.server_close()


def test_page_is_served_with_security_headers(panel):
  status, headers, body = panel.request("/")
  assert status == 200 and b"<title>Hexa</title>" in body
  assert headers["X-Frame-Options"] == "DENY"
  assert headers["Cache-Control"] == "no-store"
  assert "default-src 'self'" in headers["Content-Security-Policy"]
  assert panel.request("/manifest.webmanifest")[0] == 200
  assert panel.request("/../hexaweb.py")[0] == 404


def test_api_needs_pairing(panel):
  assert panel.json("/api/state")[0] == 401
  assert panel.json("/api/state", token="made-up")[0] == 401
  assert panel.json("/api/option", {"key": "LaneOffsetEnabled", "value": True})[0] == 401
  assert panel.store.get("LaneOffsetEnabled") is None


def test_pairing_needs_a_code_on_the_comma(panel):
  status, body = panel.json("/api/pair", {"pin": "123456", "name": "x"})
  assert status == 403 and "Gere um código" in body["error"]


def test_pairing_code_is_single_use_and_expires(panel):
  pin = panel.show_pin()
  assert panel.json("/api/pair", {"pin": pin, "name": "a"})[0] == 200
  assert panel.json("/api/pair", {"pin": pin, "name": "b"})[0] == 403  # already used
  pin = panel.show_pin()
  panel.now += phone.PIN_TTL + 1
  assert panel.json("/api/pair", {"pin": pin, "name": "c"})[0] == 403  # expired


def test_wrong_codes_lock_the_code(panel):
  pin = panel.show_pin()
  wrong = "000000" if pin != "000000" else "111111"
  for _ in range(phone.MAX_PIN_FAILURES):
    assert panel.json("/api/pair", {"pin": wrong, "name": "x"})[0] == 403
  assert panel.store.get(phone.PIN_PARAM) is None
  assert panel.json("/api/pair", {"pin": pin, "name": "x"})[0] == 403
  assert panel.store.get(phone.AUDIT_PARAM)[-1]["event"] == "pin_locked"


def test_state_lists_options_and_device(panel):
  token = panel.pair("celular do dono")
  status, s = panel.json("/api/state", token=token)
  assert status == 200
  assert s["device"] == {"branch": "h6gt-next", "commit": "abcdef1", "version": ""}
  assert s["you"] == "celular do dono"
  keys = {o["key"] for g in s["groups"] for o in g["options"]}
  assert {"AlwaysOnLateral", "LaneOffsetEnabled", "HexaWebEnabled"} <= keys
  assert s["audit"][0]["event"] == "paired"
  assert s["titles"]["LaneOffsetEnabled"] == "posição na faixa pelo volante"


def test_set_option_parked_and_audited(panel):
  token = panel.pair()
  panel.vs = VehicleState(onroad=True, parked=True, standstill=True, engaged=False)
  status, body = panel.json("/api/option", {"key": "AlwaysOnLateral", "value": True}, token=token)
  assert status == 200 and "próxima partida" in body["message"]
  assert panel.store.get("AlwaysOnLateral") is True
  last = panel.store.get(phone.AUDIT_PARAM)[-1]
  assert last["event"] == "set" and last["key"] == "AlwaysOnLateral" and last["old"] is False and last["new"] is True


def test_driving_option_is_read_only_while_driving(panel):
  token = panel.pair()
  panel.vs = VehicleState(onroad=True, parked=False, standstill=False, engaged=True)
  status, body = panel.json("/api/option", {"key": "LaneOffsetEnabled", "value": True}, token=token)
  assert status == 409 and "só leitura" in body["error"]
  assert panel.store.get("LaneOffsetEnabled") is None
  assert panel.store.get(phone.AUDIT_PARAM)[-1]["event"] == "denied"
  _, s = panel.json("/api/state", token=token)
  lane = next(o for g in s["groups"] for o in g["options"] if o["key"] == "LaneOffsetEnabled")
  assert lane["writable"] is False and lane["reason"]


def test_unknown_vehicle_state_is_read_only(panel):
  token = panel.pair()

  def broken():
    raise RuntimeError("no messages")
  panel.backend._vehicle_state = broken
  assert panel.json("/api/option", {"key": "LaneOffsetEnabled", "value": True}, token=token)[0] == 409


def test_phone_cannot_turn_on_the_panel(panel):
  token = panel.pair()
  status, _ = panel.json("/api/option", {"key": "HexaWebEnabled", "value": False}, token=token)
  assert status == 409


def test_bad_requests(panel):
  token = panel.pair()
  assert panel.json("/api/option", {"key": "Nope", "value": True}, token=token)[0] == 404
  assert panel.json("/api/option", {"key": "LaneOffsetEnabled", "value": "yes"}, token=token)[0] == 400
  assert panel.json("/api/option", raw=b"not json", token=token)[0] == 400
  assert panel.json("/api/option", raw=b"[1, 2]", token=token)[0] == 400
  assert panel.json("/api/option", raw=b"{" + b" " * (hexaweb.MAX_BODY + 1) + b"}", token=token)[0] == 413
  assert panel.json("/api/nope", {}, token=token)[0] == 404


def test_logout_revokes_only_that_phone(panel):
  a, b = panel.pair("a"), panel.pair("b")
  assert panel.json("/api/logout", {}, token=a)[0] == 200
  assert panel.json("/api/state", token=a)[0] == 401
  assert panel.json("/api/state", token=b)[0] == 200


def test_only_local_network(panel, monkeypatch):
  monkeypatch.setattr(phone, "is_local_address", lambda ip: False)
  assert panel.request("/")[0] == 403
  assert panel.json("/api/pair", {"pin": "123456"})[0] == 403
