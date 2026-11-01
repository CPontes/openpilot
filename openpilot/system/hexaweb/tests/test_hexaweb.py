import json
import threading
import urllib.error
import urllib.request

import pytest

from openpilot.selfdrive.hexa import marks, phone
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
    self.logged: list[dict] = []
    self.backend = hexaweb.PanelBackend(self.store, lambda: self.vs, clock=lambda: self.now, mono=lambda: self.now,
                                        log_event=self.logged.append)
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
  assert {"LaneOffsetEnabled", "HexaWebEnabled"} <= keys
  assert s["audit"][0]["event"] == "paired"
  assert s["titles"]["LaneOffsetEnabled"] == "posição na faixa pelo volante"


def test_set_option_parked_and_audited(panel):
  token = panel.pair()
  panel.vs = VehicleState(onroad=True, parked=True, standstill=True, engaged=False)
  status, body = panel.json("/api/option", {"key": "LaneOffsetEnabled", "value": True}, token=token)
  assert status == 200 and "próximo engate" in body["message"]
  assert panel.store.get("LaneOffsetEnabled") is True
  last = panel.store.get(phone.AUDIT_PARAM)[-1]
  assert last["event"] == "set" and last["key"] == "LaneOffsetEnabled" and last["old"] is False and last["new"] is True


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


# *** marks ***

def test_marks_need_pairing(panel):
  assert panel.json("/api/marks")[0] == 401
  assert panel.json("/api/mark", {"reason": "curva"})[0] == 401


def test_phone_mark_while_driving(panel):
  token = panel.pair("passageiro")
  panel.vs = VehicleState(onroad=True, parked=False, standstill=False, engaged=True)  # marks never change driving
  status, body = panel.json("/api/mark", {"reason": "balancou", "note": " no  trânsito "}, token=token)
  assert status == 200
  m = body["mark"]
  assert (m["reason"], m["label"], m["note"], m["source"], m["who"]) == ("balancou", "balançou", "no trânsito", "celular", "passageiro")
  assert "_req_mono_ns" not in m
  assert panel.store.get(marks.BOOKMARK_REQUEST_PARAM) is True  # the comma raises its own bookmark
  assert panel.logged[-1]["reason"] == "balancou" and panel.logged[-1]["mono_ns"] is None


def test_mark_rate_limit_and_unknown_reason(panel):
  token = panel.pair()
  assert panel.json("/api/mark", {"reason": "nope"}, token=token)[0] == 400
  assert panel.json("/api/mark", {"reason": "curva"}, token=token)[0] == 200
  assert panel.json("/api/mark", {"reason": "curva"}, token=token)[0] == 429
  panel.now += marks.MIN_MARK_INTERVAL
  assert panel.json("/api/mark", {"reason": "curva"}, token=token)[0] == 200


def test_bookmark_gets_the_phone_reason_with_the_exact_log_time(panel):
  token = panel.pair()
  assert panel.json("/api/mark", {"reason": "freada"}, token=token)[0] == 200
  mono_ns = int(panel.now * 1e9) + 300_000_000  # the comma's bookmark, 0.3 s later
  panel.backend.on_bookmark(mono_ns)
  _, s = panel.json("/api/marks", token=token)
  assert s["pending"] == 0 and len(s["marks"]) == 1
  assert s["marks"][0]["mono_ns"] == mono_ns and s["marks"][0]["reason"] == "freada"
  assert panel.logged[-1]["mono_ns"] == mono_ns


def test_comma_flag_is_classified_later(panel):
  token = panel.pair("dono")
  flag = panel.backend.on_bookmark(123_000_000_000)  # pressed on the comma while driving
  _, s = panel.json("/api/marks", token=token)
  assert s["pending"] == 1 and s["marks"][0]["reason"] is None
  status, body = panel.json("/api/mark/classify", {"id": flag["id"], "reason": "soltou", "note": "saída da curva"}, token=token)
  assert status == 200 and body["mark"]["label"] == "soltou sozinho" and body["mark"]["who"] == "dono"
  _, s = panel.json("/api/marks", token=token)
  assert s["pending"] == 0
  assert panel.logged[-1] == {**panel.logged[-1], "mono_ns": 123_000_000_000, "reason": "soltou"}
  assert panel.json("/api/mark/classify", {"id": "nope", "reason": "soltou"}, token=token)[0] == 404
  assert panel.json("/api/mark/classify", {"id": flag["id"], "reason": "nope"}, token=token)[0] == 400


def test_custom_reasons(panel):
  token = panel.pair()
  status, body = panel.json("/api/reasons", {"label": "Buraco"}, token=token)
  assert status == 200 and body["reason"]["key"] == "c-buraco"
  assert panel.json("/api/reasons", {"label": "buraco"}, token=token)[0] == 400  # duplicate
  _, s = panel.json("/api/marks", token=token)
  assert s["reasons"][-1] == {"key": "c-buraco", "label": "Buraco", "custom": True}
  assert panel.json("/api/mark", {"reason": "c-buraco"}, token=token)[0] == 200
  assert panel.json("/api/reasons/remove", {"key": "balancou"}, token=token)[0] == 404  # defaults stay
  assert panel.json("/api/reasons/remove", {"key": "c-buraco"}, token=token)[0] == 200
  _, s = panel.json("/api/marks", token=token)
  assert all(r["key"] != "c-buraco" for r in s["reasons"])
  assert s["marks"][0]["label"] == "Buraco"  # old marks keep their label


def test_export_marks(panel):
  token = panel.pair()
  assert panel.json("/api/marks/export")[0] == 401
  panel.backend.on_bookmark(42_000_000_000)
  status, body = panel.json("/api/marks/export", token=token)
  assert status == 200 and body["exported_at"] == int(panel.now)
  assert len(body["marks"]) == 1 and body["marks"][0]["mono_ns"] == 42_000_000_000


# *** replay ***

@pytest.fixture
def replay_panel(panel, tmp_path):
  from openpilot.selfdrive.hexa.tests.test_timeline import write_rlog
  seg = tmp_path / "00000007--abc--9"
  seg.mkdir()
  write_rlog(seg / "rlog.zst", seconds=4.0, bookmark_at=2.0)
  panel.backend.log_root = str(tmp_path)
  return panel


def test_replay_page_and_list(replay_panel):
  status, _, body = replay_panel.request("/replay")
  assert status == 200 and b"<title>Hexa \xc2\xb7 Replay</title>" in body
  assert replay_panel.json("/api/replay/list")[0] == 401
  token = replay_panel.pair()
  status, body = replay_panel.json("/api/replay/list", token=token)
  assert status == 200 and body["segments"] == [{"id": "00000007--abc--9", "label": "00000007--abc--9"}]


def test_replay_only_with_the_car_off(replay_panel):
  token = replay_panel.pair()
  replay_panel.vs = VehicleState(onroad=True, parked=True, standstill=True, engaged=False)
  status, body = replay_panel.json("/api/replay/data?id=00000007--abc--9", token=token)
  assert status == 409 and "desligado" in body["error"]
  replay_panel.vs = VehicleState(onroad=False)
  status, body = replay_panel.json("/api/replay/data?id=00000007--abc--9", token=token)
  assert status == 200 and len(body["frames"]) >= 39 and any(e["kind"] == "mark" for e in body["events"])


def test_replay_ids_come_only_from_the_list(replay_panel):
  token = replay_panel.pair()
  for bad in ("nope", "../../etc/passwd", "00000007--abc--9/../x", ""):
    assert replay_panel.json("/api/replay/data?id=" + bad, token=token)[0] == 404, bad


def test_replay_cache_is_bounded(replay_panel, tmp_path):
  from openpilot.selfdrive.hexa.tests.test_timeline import write_rlog
  token = replay_panel.pair()
  for i in range(hexaweb.REPLAY_CACHE + 2):
    d = tmp_path / f"0000000{i}--x--{i}"
    d.mkdir(exist_ok=True)
    write_rlog(d / "rlog.zst", seconds=1.0)
    assert replay_panel.json(f"/api/replay/data?id=0000000{i}--x--{i}", token=token)[0] == 200
  assert len(replay_panel.backend._replay_cache) == hexaweb.REPLAY_CACHE


def test_replay_is_gzipped_when_the_phone_accepts_it(replay_panel):
  import gzip
  token = replay_panel.pair()
  url = replay_panel.url + "/api/replay/data?id=00000007--abc--9"
  plain = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
  with urllib.request.urlopen(plain, timeout=5) as r:
    assert r.headers.get("Content-Encoding") is None
    body = r.read()
  assert len(body) >= hexaweb.GZIP_MIN_BYTES
  zipped = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept-Encoding": "gzip, br"})
  with urllib.request.urlopen(zipped, timeout=5) as r:
    assert r.headers.get("Content-Encoding") == "gzip" and r.headers.get("Vary") == "Accept-Encoding"
    assert int(r.headers["Content-Length"]) < len(body) // 3
    assert gzip.decompress(r.read()) == body
  # small answers stay plain
  small = urllib.request.Request(replay_panel.url + "/api/replay/list", headers={"Authorization": "Bearer " + token, "Accept-Encoding": "gzip"})
  with urllib.request.urlopen(small, timeout=5) as r:
    assert r.headers.get("Content-Encoding") is None and json.loads(r.read())["segments"]


def test_options_missing_from_this_build_are_neither_shown_nor_writable(panel):
  def check_key(key):
    if key == "LaneOffsetEnabled":
      raise KeyError(key)
    return key
  panel.store.check_key = check_key
  token = panel.pair()
  status, s = panel.json("/api/state", token=token)
  keys = {o["key"] for g in s["groups"] for o in g["options"]}
  assert status == 200 and "LaneOffsetEnabled" not in keys and "HexaWebEnabled" in keys
  assert panel.json("/api/option", {"key": "LaneOffsetEnabled", "value": True}, token=token)[0] == 404
  assert panel.store.get("LaneOffsetEnabled") is None


def test_serve_survives_a_busy_port(monkeypatch):
  # a dead process disengages openpilot, so a failed server must keep trying
  calls = []

  def fail(backend):
    calls.append(backend)
    if len(calls) == 3:
      raise KeyboardInterrupt  # how the manager stops a process
    raise OSError("port busy")

  monkeypatch.setattr(hexaweb, "make_server", fail)
  with pytest.raises(KeyboardInterrupt):
    hexaweb.serve("backend", retry=0)
  assert len(calls) == 3


def updater(panel, **values):
  panel.store.d.update({"UpdaterAvailableBranches": "h6gt,h6gt-lat-04,master,release3", "UpdaterTargetBranch": "h6gt",
                        "UpdaterCurrentDescription": "0.11.3 / h6gt / 2fd5622 / Nov 01", "UpdaterState": "idle", **values})
  panel.signals = []
  panel.backend.signal_updater = panel.signals.append
  return panel.pair()


def test_update_state_offers_only_our_branches(panel):
  token = updater(panel, UpdaterNewDescription="0.11.3 / h6gt-lat-04 / 3653abc / Nov 01", UpdateAvailable=True)
  status, u = panel.json("/api/update", token=token)
  assert status == 200 and u["branches"] == ["h6gt", "h6gt-lat-04"] and u["allowed"] is True
  assert u["current"]["commit"] == "2fd5622" and u["new"]["branch"] == "h6gt-lat-04" and u["ready"] is True


def test_update_steps_with_the_car_off(panel):
  token = updater(panel, UpdateAvailable=True)
  assert panel.json("/api/update", {"action": "check"}, token=token)[0] == 200
  assert panel.json("/api/update", {"action": "download"}, token=token)[0] == 200
  assert panel.signals == ["SIGUSR1", "SIGHUP"]
  assert panel.json("/api/update", {"action": "branch", "branch": "h6gt-lat-04"}, token=token)[0] == 200
  assert panel.store.get("UpdaterTargetBranch") == "h6gt-lat-04" and panel.signals[-1] == "SIGUSR1"
  assert panel.json("/api/update", {"action": "install"}, token=token)[0] == 200
  assert panel.store.get("DoReboot") is True
  assert panel.store.get(phone.AUDIT_PARAM)[-1]["event"] == "update"


def test_update_refused_with_the_car_on(panel):
  token = updater(panel, UpdateAvailable=True)
  for vs in (VehicleState(onroad=True), VehicleState(onroad=True, parked=True, standstill=True)):
    panel.vs = vs
    for body in ({"action": "check"}, {"action": "download"}, {"action": "install"}, {"action": "branch", "branch": "h6gt"}):
      assert panel.json("/api/update", body, token=token)[0] == 409
  assert panel.signals == [] and panel.store.get("DoReboot") is None
  assert panel.json("/api/update", token=token)[1]["allowed"] is False


def test_update_needs_pairing(panel):
  updater(panel, UpdateAvailable=True)
  assert panel.json("/api/update")[0] == 401
  assert panel.json("/api/update", {"action": "install"})[0] == 401
  assert panel.store.get("DoReboot") is None


def test_update_only_to_a_listed_branch_and_nothing_else(panel):
  token = updater(panel, UpdateAvailable=False)
  assert panel.json("/api/update", {"action": "branch", "branch": "master"}, token=token)[0] == 400
  assert panel.json("/api/update", {"action": "branch", "branch": "evil; reboot"}, token=token)[0] == 400
  assert panel.json("/api/update", {"action": "uninstall"}, token=token)[0] == 400
  assert panel.json("/api/update", {"action": "install"}, token=token)[0] == 409
  assert panel.store.get("UpdaterTargetBranch") == "h6gt" and panel.store.get("DoReboot") is None


def trip_logs(panel, tmp_path):
  from openpilot.selfdrive.hexa.tests.test_timeline import write_rlog
  for n in (0, 1):
    d = tmp_path / f"0000000f--8784034901--{n}"
    d.mkdir()
    write_rlog(d / "qlog.zst", bookmark_at=3.0)
  panel.backend.log_root = str(tmp_path)


def test_trips_are_listed_and_read_by_minute(panel, tmp_path):
  trip_logs(panel, tmp_path)
  token = panel.pair()
  status, t = panel.json("/api/trips", token=token)
  assert status == 200 and t["trips"][0]["id"] == "0000000f--8784034901" and t["trips"][0]["segments"] == [0, 1]
  status, d = panel.json("/api/trip/segment?route=0000000f--8784034901&n=1", token=token)
  assert status == 200 and d["points"] and d["summary"]["km"] > 0 and d["summary"]["marks"] == 1


def test_trips_only_with_the_car_off(panel, tmp_path):
  trip_logs(panel, tmp_path)
  token = panel.pair()
  panel.vs = VehicleState(onroad=True, parked=True, standstill=True)
  assert panel.json("/api/trips", token=token)[0] == 409
  assert panel.json("/api/trip/segment?route=0000000f--8784034901&n=0", token=token)[0] == 409


def test_trip_takes_only_ids_from_the_list(panel, tmp_path):
  trip_logs(panel, tmp_path)
  token = panel.pair()
  for q in ("route=../../etc&n=0", "route=0000000f--8784034901&n=9", "route=0000000f--8784034901&n=../0"):
    assert panel.json("/api/trip/segment?" + q, token=token)[0] == 404
  assert panel.json("/api/trips")[0] == 401


def test_trip_mark_shows_the_reason(panel, tmp_path):
  trip_logs(panel, tmp_path)
  token = panel.pair()
  mono = next(e for e in panel.backend.trip_data("0000000f--8784034901", "0")[1]["events"] if e["kind"] == "mark")
  assert mono["text"] == "bandeira" and "mono_ns" not in mono
  panel.backend._trip_cache.clear()
  segment = hexaweb.timeline.trip_segment(hexaweb.timeline.read_rlog(tmp_path / "0000000f--8784034901--0" / "qlog.zst"))
  ns = next(e for e in segment["events"] if e["kind"] == "mark")["mono_ns"]
  panel.store.put(marks.MARKS_PARAM, [{"id": "a", "mono_ns": ns, "label": "curva difícil", "note": "rotatória"}])
  ev = panel.json("/api/trip/segment?route=0000000f--8784034901&n=0", token=token)[1]["events"]
  assert next(e for e in ev if e["kind"] == "mark")["text"] == "curva difícil: rotatória"


def test_mark_text_can_be_written_changed_and_cleared(panel):
  token = panel.pair()
  mark = panel.json("/api/mark", {"reason": "outro"}, token=token)[1]["mark"]
  body = {"id": mark["id"], "reason": "outro", "note": "moto colou na lateral", "replace_note": True}
  assert panel.json("/api/mark/classify", body, token=token)[1]["mark"]["note"] == "moto colou na lateral"
  body = {"id": mark["id"], "reason": "curva", "note": "buraco na curva", "replace_note": True}
  edited = panel.json("/api/mark/classify", body, token=token)[1]["mark"]
  assert edited["note"] == "buraco na curva" and edited["label"] == "curva difícil"
  body = {"id": mark["id"], "reason": "curva", "note": "", "replace_note": True}
  assert panel.json("/api/mark/classify", body, token=token)[1]["mark"]["note"] == ""


def test_classifying_without_text_keeps_the_note(panel):
  token = panel.pair()
  mark = panel.json("/api/mark", {"reason": "outro", "note": "antes"}, token=token)[1]["mark"]
  body = {"id": mark["id"], "reason": "curva", "note": ""}
  assert panel.json("/api/mark/classify", body, token=token)[1]["mark"]["note"] == "antes"
  # only a real true replaces it
  body = {"id": mark["id"], "reason": "curva", "note": "", "replace_note": "sim"}
  assert panel.json("/api/mark/classify", body, token=token)[1]["mark"]["note"] == "antes"
