"""Sending a mark's moment to the developers (share.py): only the window, only what the car did, nothing that identifies."""
import hashlib
import json
import os

import pytest

from openpilot.selfdrive.hexa import share

NS = int(1e9)
VIN = "LGWEF4A59RH123456"
DONGLE = "0123456789abcdef"
SERIAL = "a1b2c3d4e5"
T0 = 1_000 * NS


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


def car_params(vin=VIN, brand="gwm", model="GWM_HAVAL_H6"):
  from opendbc.car.structs import car
  return car.CarParams.new_message(brand=brand, carFingerprint=model, carVin=vin)


def drive(seconds=120., start=T0, vin_on_can=True):
  """A drive as the comma records it: the car at 100 Hz, CAN with a VIN broadcast and a diagnostic answer, GPS, the
  device's identity in initData and logMessage. Returns the raw bytes of the events."""
  from openpilot.cereal import log
  out = bytearray()

  def add(service, t, fill=None):
    ev = log.Event.new_message(logMonoTime=t, valid=True)
    if service == "carParams":
      ev.carParams = car_params()
    elif service == "logMessage":
      ev.logMessage = json.dumps({"msg": "hello", "ctx": {"dongle_id": DONGLE}})
    elif fill is not None:
      fill(ev.init(service))
    else:
      ev.init(service)
    out.extend(ev.to_bytes())

  def init_data(d):
    d.dongleId = DONGLE
    d.deviceType = "mici"
    d.gitBranch = "h6gt"

  add("initData", start, init_data)
  vin_frames = [bytes([i]) + VIN.encode()[i * 7:i * 7 + 7].ljust(7, b"\0") for i in range(3)]
  n = int(seconds * 100)
  for i in range(n):
    t = start + i * NS // 100
    add("carState", t, lambda c, i=i: setattr(c, "vEgo", 10. + i / 1000))
    add("carControl", t, lambda c: setattr(c, "latActive", True))

    frames = [(0x1A0, bytes([i % 256] * 8), 0), (0x2B0, b"\x01\x02\x03\x04", 1)]
    if vin_on_can:
      frames.append((0x3E1, vin_frames[i % 3], 0))  # a car that broadcasts its VIN, 7 characters per frame
    if i == 50:
      frames.append((0x7E8, b"\x10\x14\x49\x02\x01" + VIN.encode()[:3], 0))  # the VIN asked by fingerprinting
    ev = log.Event.new_message(logMonoTime=t, valid=True)
    lst = ev.init("can", len(frames))
    for dst, (a, d, src) in zip(lst, frames, strict=True):
      dst.address, dst.dat, dst.src = a, d, src
    out.extend(ev.to_bytes())
    if i % 10 == 0:
      add("gpsLocationExternal", t, lambda g: (setattr(g, "latitude", -23.55), setattr(g, "longitude", -46.63), setattr(g, "hasFix", True)))
      add("logMessage", t)
    if i % 2000 == 0:
      add("carParams", t)
  return bytes(out)


def events(raw):
  from openpilot.cereal import log
  return list(log.Event.read_multiple_bytes(raw))


def decode(payload):
  import zstandard
  raw = zstandard.ZstdDecompressor().stream_reader(payload).read()
  return raw, events(raw)


@pytest.fixture(scope="module")
def recorded():
  return events(drive())


def test_cut_keeps_only_the_window_and_the_listed_services(recorded):
  center = T0 + 60 * NS
  payload, stats = share.cut(recorded, center, vin=VIN, hide=[DONGLE, SERIAL])
  raw, evs = decode(payload)
  times = [e.logMonoTime for e in evs]
  assert min(times) >= center - int(share.BEFORE_S * NS) and max(times) <= center + int(share.AFTER_S * NS)
  assert stats["seconds"] == pytest.approx(share.BEFORE_S + share.AFTER_S, abs=0.05)
  assert {e.which() for e in evs} <= share.SERVICES
  assert {"carState", "carControl", "can"} <= {e.which() for e in evs}
  assert {"gpsLocationExternal", "logMessage"} <= set(stats["left_out"])
  assert stats["services"]["carState"] == 4501


def test_cut_has_no_vin_no_gps_no_device(recorded):
  payload, stats = share.cut(recorded, T0 + 60 * NS, vin=VIN, hide=[DONGLE, SERIAL])
  raw, evs = decode(payload)
  for secret in (VIN, VIN[-8:], VIN[:8], DONGLE):
    assert secret.encode() not in raw
  for piece in share._vin_pieces(VIN):
    assert piece not in raw
  assert not any(e.which() in ("gpsLocationExternal", "gpsLocation", "initData", "logMessage") for e in evs)
  cps = [e.carParams for e in evs if e.which() == "carParams"]
  assert cps and all(cp.carVin == "" and cp.carFingerprint == "GWM_HAVAL_H6" for cp in cps)
  assert stats["can_addresses_with_vin"] == ["0:0x3e1"] and stats["vin_checked"]
  addresses = {c.address for e in evs if e.which() == "can" for c in e.can}
  assert addresses == {0x1A0, 0x2B0}  # the VIN broadcast and the diagnostic CAN are out, the rest stays


def test_the_diagnostic_can_is_dropped_even_without_a_vin_to_look_for():
  evs = events(drive(seconds=2., vin_on_can=False))
  payload, stats = share.cut(evs, T0 + NS // 2, vin="", before_s=1., after_s=1.)
  _, out = decode(payload)
  assert not any(c.address == 0x7E8 for e in out if e.which() == "can" for c in e.can)
  assert stats["vin_checked"] is False and stats["can_frames_dropped"] == 1


def test_a_cut_with_an_identifier_left_is_refused():
  from openpilot.cereal import log
  evs = events(drive(seconds=1., vin_on_can=False))
  ev = log.Event.new_message(logMonoTime=T0 + NS // 2, valid=True)
  ev.carParams = car_params(vin="", model=DONGLE)  # the dongle id where nothing should have it
  with pytest.raises(share.ShareError, match="Não foi enviado"):
    share.cut(evs + events(ev.to_bytes()), T0 + NS // 2, hide=[DONGLE], before_s=1., after_s=1.)
  assert share.cut(evs, T0 + NS // 2, hide=[DONGLE], before_s=1., after_s=1.)[0]  # without it, it goes
  assert share.scrubbed(b"xx" + DONGLE.upper().encode() + b"yy", [DONGLE]) == [DONGLE]
  assert share.scrubbed(b"short", ["abc"]) == []  # too short to look for


def test_scrubbed_refusal_is_raised(monkeypatch):
  evs = events(drive(seconds=1., vin_on_can=False))
  monkeypatch.setattr(share, "scrubbed", lambda raw, hide: ["x"])
  with pytest.raises(share.ShareError, match="Não foi enviado"):
    share.cut(evs, T0 + NS // 2, before_s=1., after_s=1.)


def test_nothing_of_the_car_in_the_window_is_refused(recorded):
  with pytest.raises(share.ShareError):
    share.cut(recorded, T0 + 500 * NS)


def test_pack_round_trip_and_bad_packages():
  m = {"format": 1, "mark": {"id": "abcd1234", "note": "curva à direita"}}
  data = share.pack(m, b"\x28\xb5payload")
  assert share.unpack(data) == (m, b"\x28\xb5payload")
  for bad in (b"", b"LZS1", b"XXXX\0\0\0\0", b"LZS1\xff\xff\xff\xff{}", b"LZS1\0\0\0\x02[]"):
    with pytest.raises(ValueError):
      share.unpack(bad)


def test_signature_matches_the_receivers_vector():
  # the same vector is in tools/lizpilot/receiver/handler.test.ts: both sides sign the same way
  key = "11" * 32
  body = b"hello"
  assert share.canonical(1700000000, "post", "/v1/share", body) == \
    b"1700000000\nPOST\n/v1/share\n" + hashlib.sha256(body).hexdigest().encode()
  assert share.sign(key, 1700000000, "POST", "/v1/share", body) == VECTOR_SIG


VECTOR_SIG = "a9fec4e84c31022756b5670c1c1c18733ae1fc303d5d1d3d10efb616cc42e834"  # HMAC-SHA256, key 0x11 * 32


def test_device_identity_is_random_and_kept():
  s = FakeStore(DongleId=DONGLE)
  d = share.device(s)
  assert len(d["id"]) == 32 and len(d["key"]) == 64 and d["id"] != DONGLE and not d["registered"]
  assert share.device(s) == d
  s.put(share.DEVICE_PARAM, {"id": "bad"})
  assert share.device(s)["id"] != "bad"


# *** where a mark is in the logs ***

def write_segments(root, route="00000010--abcdef1234", minutes=3, end0=10_000.):
  import zstandard
  for n in range(minutes):
    d = root / f"{route}--{n}"
    d.mkdir(parents=True)
    f = d / "rlog.zst"
    f.write_bytes(zstandard.ZstdCompressor().compress(drive(seconds=60., start=T0 + n * 60 * NS)))
    end = end0 + (n + 1) * 60
    os.utime(f, (end, end))
  return root


def test_cut_mark_takes_the_minute_before_when_the_window_crosses_into_it(tmp_path):
  root = write_segments(tmp_path / "realdata")
  # a bookmark 10 s into minute 1: the window starts 20 s into minute 0
  mono = T0 + 70 * NS
  payload, stats = share.cut_mark(str(root), {"id": "m1", "t": 10_000 + 60 + 10, "mono_ns": mono}, vin=VIN)
  _, evs = decode(payload)
  times = [e.logMonoTime for e in evs]
  assert min(times) == pytest.approx(mono - 30 * NS, abs=NS // 50) and max(times) == pytest.approx(mono + 15 * NS, abs=NS // 50)


def test_cut_mark_without_its_bookmark_uses_the_wall_time(tmp_path):
  root = write_segments(tmp_path / "realdata")
  payload, _ = share.cut_mark(str(root), {"id": "m1", "t": 10_000 + 60 + 40, "mono_ns": None, "delay_s": 0.}, vin=VIN)
  _, evs = decode(payload)
  center = T0 + 100 * NS
  assert min(e.logMonoTime for e in evs) == pytest.approx(center - 30 * NS, abs=NS // 50)


def test_cut_mark_goes_back_by_the_phones_delay(tmp_path):
  root = write_segments(tmp_path / "realdata")
  mono = T0 + 100 * NS
  payload, _ = share.cut_mark(str(root), {"id": "m1", "t": 10_000 + 60 + 40, "mono_ns": mono, "delay_s": 5.}, vin=VIN)
  _, evs = decode(payload)
  assert max(e.logMonoTime for e in evs) == pytest.approx(mono - 5 * NS + 15 * NS, abs=NS // 50)


def test_cut_mark_without_the_log_says_so(tmp_path):
  root = write_segments(tmp_path / "realdata", minutes=1)
  with pytest.raises(share.ShareError, match="não está mais"):
    share.cut_mark(str(root), {"id": "m1", "t": 99_999})
  with pytest.raises(share.ShareError):
    share.cut_mark(str(root), {"id": "m1", "t": None})


# *** the queue and the worker ***

class Receiver:
  """The receiver as the comma sees it: checks the signature like handler.ts does."""

  def __init__(self, now):
    self.now = now
    self.devices: dict[str, str] = {}
    self.shares: list[tuple[str, dict]] = []
    self.answer: int | None = None
    self.calls: list[str] = []
    self.clock_off = False

  def __call__(self, url, body, hdrs):
    path = url.split("https://r.example", 1)[1]
    self.calls.append(path)
    if self.answer is not None:
      return self.answer, {}
    ts = int(hdrs[share.TIME_HEADER])
    if abs(ts - self.now()) > 300:
      return 401, {"error": "clock", "now": int(self.now())}
    dev = hdrs[share.DEVICE_HEADER]
    if path == "/v1/register":
      reg = json.loads(body)
      key = self.devices.setdefault(reg["device"], reg["key"])
      if key != reg["key"]:
        return 409, {}
    key = self.devices.get(dev)
    if key is None or share.sign(key, ts, "POST", path, body) != hdrs[share.SIG_HEADER]:
      return 401, {"error": "signature"}
    if path == "/v1/share":
      m, _ = share.unpack(body)
      self.shares.append((dev, m))
      return 201, {"id": f"s{len(self.shares)}"}
    if path == "/v1/forget":
      self.shares = [s for s in self.shares if s[0] != dev]
      self.devices.pop(dev)
      return 200, {}
    return 200, {}


class World:
  def __init__(self, tmp_path):
    self.t = 50_000.
    self.store = FakeStore(**{share.ENABLED_PARAM: True, "CarParamsPersistent": car_params().to_bytes(), "DongleId": DONGLE.encode(),
                              "HardwareSerial": SERIAL.encode(), "GitBranch": b"h6gt", "GitCommit": b"0123456789abcdef", "Version": b"0.10.1"})
    self.receiver = Receiver(lambda: self.t)
    self.cuts: list[dict] = []
    self.sharer = share.Sharer(self.store, str(tmp_path / "realdata"), url="https://r.example", post=self.receiver, clock=lambda: self.t,
                               cutter=self.cutter, share_dir=tmp_path / "share")
    self.cut_error: str | None = None

  def cutter(self, log_root, mark, vin="", hide=()):
    self.cuts.append({"mark": mark, "vin": vin, "hide": list(hide)})
    if self.cut_error:
      raise share.ShareError(self.cut_error)
    return b"\x28\xb5\x2f\xfdcut", {"services": {"carState": 1}}


MARK = {"id": "ab12cd34", "t": 49_000, "mono_ns": 5 * NS, "reason": "curva", "label": "curva difícil", "note": "na serra", "delay_s": 2.,
        "source": "celular", "who": "celular do dono"}


def test_nothing_happens_with_the_option_off(tmp_path):
  w = World(tmp_path)
  w.store.put(share.ENABLED_PARAM, False)
  assert w.sharer.enqueue(MARK)[0] == 409
  assert w.sharer.step(True, True, [MARK]) == "desligado"


def test_a_mark_is_cut_parked_and_sent_on_wifi(tmp_path):
  w = World(tmp_path)
  assert w.sharer.enqueue(MARK) == (200, {"ok": True, "item": {"id": MARK["id"], "state": "fila", "tries": 0, "added": 50_000}})
  assert w.sharer.step(offroad=False, network_ok=True, marks=[MARK]) == "nada"  # driving: no cut
  assert w.sharer.step(True, False, [MARK]) == "cortado"
  assert w.cuts[0]["vin"] == VIN and set(w.cuts[0]["hide"]) == {DONGLE, SERIAL}
  assert w.sharer.step(True, False, [MARK]) == "esperando rede"
  assert w.sharer.step(True, True, [MARK]) == "enviado"
  assert w.receiver.calls == ["/v1/register", "/v1/share"]
  dev, m = w.receiver.shares[0]
  assert dev == w.store.get(share.DEVICE_PARAM)["id"] != DONGLE
  assert m["mark"] == {k: MARK[k] for k in ("id", "reason", "label", "note", "delay_s", "source")}  # never "who" nor the time
  assert m["car"] == {"brand": "gwm", "model": "GWM_HAVAL_H6"} and m["month"] == "1970-01"
  assert m["software"] == {"edition": "h6gt", "branch": "h6gt", "commit": "0123456789ab", "version": "0.10.1"}
  assert VIN not in json.dumps(m) and DONGLE not in json.dumps(m)
  item = w.sharer.state()["items"][0]
  assert item["state"] == "enviado" and item["sent_at"] == 50_000
  assert not list((tmp_path / "share").iterdir())  # the cut is gone from the comma once sent
  assert w.sharer.enqueue(MARK)[1]["item"]["state"] == "enviado"  # twice is once


def test_send_failures_back_off_then_give_up(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  w.sharer.step(True, True, [MARK])
  w.receiver.answer = 503
  assert w.sharer.step(True, True, [MARK]) == "tentar depois"
  assert w.sharer.step(True, True, [MARK]) == "nada"  # waiting for the back off
  for _ in range(share.MAX_TRIES):
    w.t += 10_000
    w.sharer.step(True, True, [MARK])
  item = w.sharer.state()["items"][0]
  assert item["state"] == "erro" and item["tries"] == share.MAX_TRIES


def test_a_refused_share_is_an_error_at_once(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  w.sharer.step(True, True, [MARK])
  w.sharer._register()
  w.receiver.answer = 413
  assert w.sharer.step(True, True, [MARK]) == "erro"
  assert w.sharer.state()["items"][0]["error"] == "O receptor recusou este trecho."
  w.receiver.answer = None
  assert w.sharer.enqueue(MARK)[1]["item"]["state"] == "fila"  # an error can be asked again


def test_a_cut_that_cant_be_made_says_why(tmp_path):
  w = World(tmp_path)
  w.cut_error = "O log deste momento não está mais no comma."
  w.sharer.enqueue(MARK)
  assert w.sharer.step(True, True, [MARK]) == "erro"
  assert w.sharer.state()["items"][0]["error"] == w.cut_error
  w.sharer.enqueue({**MARK, "id": "gone0000"})
  assert w.sharer.step(True, True, [MARK]) == "erro"  # the mark was deleted


def test_a_comma_clock_off_is_corrected_by_the_receiver(tmp_path):
  w = World(tmp_path)
  w.sharer.clock = lambda: w.t - 3600  # no GPS nor NTP yet
  w.sharer.enqueue(MARK)
  w.sharer.step(True, True, [MARK])
  assert w.sharer.step(True, True, [MARK]) == "enviado"
  assert w.sharer.skew == 3600


def test_the_receiver_forgetting_the_key_registers_again(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  w.sharer.step(True, True, [MARK])
  w.sharer._register()
  w.receiver.devices.clear()
  assert w.sharer.step(True, True, [MARK]) == "tentar depois"
  w.t += 100
  assert w.sharer.step(True, True, [MARK]) == "enviado"


def test_cancel_before_sending_and_not_after(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  w.sharer.step(True, False, [MARK])
  assert list((tmp_path / "share").iterdir())
  assert w.sharer.cancel(MARK["id"]) == (200, {"ok": True})
  assert not list((tmp_path / "share").iterdir()) and w.sharer.state()["items"] == []
  assert w.sharer.cancel(MARK["id"])[0] == 404
  w.sharer.enqueue(MARK)
  w.sharer.step(True, True, [MARK])
  w.sharer.step(True, True, [MARK])
  assert w.sharer.cancel(MARK["id"])[0] == 409


def test_cancelled_while_being_cut_leaves_nothing(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  real = w.cutter

  def cancel_during(*a, **k):
    out = real(*a, **k)
    w.sharer.cancel(MARK["id"])
    return out
  w.sharer.cutter = cancel_during
  assert w.sharer.step(True, True, [MARK]) == "cancelado"
  assert not list((tmp_path / "share").iterdir()) and w.sharer.state()["items"] == []


def test_forget_deletes_here_and_there_and_starts_a_new_key(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  w.sharer.step(True, True, [MARK])
  w.sharer.step(True, True, [MARK])
  old = w.store.get(share.DEVICE_PARAM)["id"]
  assert w.receiver.shares
  w.sharer.forget()
  assert w.sharer.state()["items"] == [] and w.sharer.state()["forgetting"]
  assert w.sharer.step(True, False, [MARK]) == "esperando rede"
  assert w.sharer.step(True, True, [MARK]) == "apagado"
  assert w.receiver.shares == [] and old not in w.receiver.devices
  assert share.device(w.store)["id"] != old


def test_forget_before_ever_sending_needs_no_network(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  w.sharer.forget()
  assert w.store.get(share.DEVICE_PARAM) is None and w.receiver.calls == []


def test_the_queue_has_a_limit(tmp_path):
  w = World(tmp_path)
  for i in range(share.MAX_QUEUE):
    assert w.sharer.enqueue({**MARK, "id": f"m{i:07d}"})[0] == 200
  assert w.sharer.enqueue({**MARK, "id": "toomany0"})[0] == 429


def test_without_a_receiver_the_queue_waits(tmp_path):
  w = World(tmp_path)
  w.sharer.url = ""
  w.sharer.enqueue(MARK)
  w.sharer.step(True, True, [MARK])
  assert w.sharer.step(True, True, [MARK]) == "esperando rede" and not w.sharer.state()["receiver"]


def test_a_step_never_raises(tmp_path):
  w = World(tmp_path)
  w.sharer.enqueue(MARK)
  w.sharer.cutter = lambda *a, **k: 1 / 0
  assert w.sharer.step(True, True, [MARK]) == "falhou"


def test_the_real_cut_through_the_worker(tmp_path):
  root = write_segments(tmp_path / "realdata")
  w = World(tmp_path)
  w.sharer.cutter = share.cut_mark
  mark = {**MARK, "t": 10_000 + 60 + 30, "mono_ns": T0 + 90 * NS, "delay_s": 0.}
  w.sharer.enqueue(mark)
  assert w.sharer.step(True, True, [mark]) == "cortado"
  data = next((tmp_path / "share").iterdir()).read_bytes()
  m, payload = share.unpack(data)
  assert m["sha256"] == hashlib.sha256(payload).hexdigest() and m["bytes"] == len(payload) < share.MAX_BYTES
  raw, _ = decode(payload)
  assert VIN.encode() not in raw and DONGLE.encode() not in raw and VIN not in data.decode("latin-1")
  assert m["cut"]["can_addresses_with_vin"] == ["0:0x3e1"]
  assert w.sharer.step(True, True, [mark]) == "enviado"
  assert root.is_dir()
