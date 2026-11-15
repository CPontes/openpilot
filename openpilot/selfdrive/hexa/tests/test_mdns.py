import random
import socket
import struct
import threading

import pytest

from openpilot.selfdrive.hexa import mdns


def query(name: str, qtype: int = mdns.TYPE_A, qclass: int = mdns.CLASS_IN, txid: int = 0, flags: int = 0) -> bytes:
  return struct.pack("!6H", txid, flags, 1, 0, 0, 0) + mdns.encode_name(name) + struct.pack("!HH", qtype, qclass)


def a_record(packet: bytes) -> tuple[int, str, int, int, str]:
  """(id, name, class, ttl, address) of the single answer in a response."""
  txid, flags, qd, an = struct.unpack("!4H", packet[:8])
  assert flags & 0x8000 and an == 1
  pos = 12
  for _ in range(qd):
    _, pos = mdns.read_name(packet, pos)
    pos += 4
  name, pos = mdns.read_name(packet, pos)
  rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", packet[pos:pos + 10])
  assert rtype == mdns.TYPE_A and rdlen == 4
  return txid, name, rclass, ttl, socket.inet_ntoa(packet[pos + 10:pos + 14])


def test_reads_a_query_for_the_name_any_case():
  txid, qs = mdns.questions(query("LizPilot.Local", txid=7))
  assert txid == 7 and qs == [("lizpilot.local", mdns.TYPE_A, mdns.CLASS_IN)]
  assert mdns.asked(qs) == (True, False)


def test_unicast_bit_and_any_type():
  _, qs = mdns.questions(query("lizpilot.local", mdns.TYPE_ANY, mdns.CLASS_IN | mdns.UNICAST_BIT))
  assert mdns.asked(qs) == (True, True)


def test_other_names_types_and_responses_are_ignored():
  assert mdns.asked(mdns.questions(query("other.local"))[1]) == (False, False)
  assert mdns.asked(mdns.questions(query("lizpilot.local", qtype=28))[1]) == (False, False)  # AAAA: no IPv6 here
  assert mdns.questions(query("lizpilot.local", flags=0x8400)) is None  # someone's answer, not a question
  assert mdns.questions(b"\x00" * 5) is None


def test_compressed_names_and_broken_packets():
  # two questions, the second pointing at the first name
  packet = struct.pack("!6H", 0, 0, 2, 0, 0, 0) + mdns.encode_name("lizpilot.local") + struct.pack("!HH", 28, 1) + \
    b"\xc0\x0c" + struct.pack("!HH", 1, 1)
  assert mdns.asked(mdns.questions(packet)[1]) == (True, False)
  loop = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + b"\xc0\x0c"
  with pytest.raises(ValueError):
    mdns.questions(loop)
  with pytest.raises(ValueError):
    mdns.questions(query("lizpilot.local")[:-3])


def test_multicast_answer_is_ours_alone_and_legacy_gets_its_question_back():
  txid, name, rclass, ttl, ip = a_record(mdns.answer("192.168.43.1"))
  assert (txid, name, rclass, ttl, ip) == (0, "lizpilot.local", mdns.CLASS_IN | mdns.UNICAST_BIT, mdns.TTL, "192.168.43.1")
  legacy = mdns.answer("10.0.0.5", txid=99, legacy=True)
  assert struct.unpack("!H", legacy[4:6])[0] == 1  # the question is repeated
  assert a_record(legacy) == (99, "lizpilot.local", mdns.CLASS_IN, mdns.LEGACY_TTL, "10.0.0.5")


def test_address_for_picks_the_route_without_sending():
  assert mdns.address_for("127.0.0.1") == "127.0.0.1"
  assert mdns.address_for("not an address") is None


def test_a_one_shot_resolver_gets_an_answer(monkeypatch):
  """The real socket, on loopback: a resolver asking from its own port gets a direct answer."""
  monkeypatch.setattr(mdns, "local_address", lambda ip: True)  # loopback stands in for the Wi-Fi here
  r = mdns.Responder(port=0)
  r.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  r.sock.bind(("127.0.0.1", 0))
  r.joined = {1: "127.0.0.1"}
  port = r.sock.getsockname()[1]
  r.port = port
  stop = threading.Event()
  t = threading.Thread(target=r.run, kwargs={"stop": stop.is_set}, daemon=True)
  r.rescan = lambda: None  # no multicast on the test machine
  t.start()
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as c:
      c.settimeout(3)
      c.sendto(query("lizpilot.local", txid=4242), ("127.0.0.1", port))
      reply, _ = c.recvfrom(512)
      assert a_record(reply) == (4242, "lizpilot.local", mdns.CLASS_IN, mdns.LEGACY_TTL, "127.0.0.1")
      c.sendto(query("someone-else.local", txid=1), ("127.0.0.1", port))
      c.settimeout(0.5)
      with pytest.raises(TimeoutError):
        c.recvfrom(512)
  finally:
    stop.set()
    t.join(3)
    r.sock.close()


# *** local only, two commas, failures ***

class FakeSock:
  def __init__(self):
    self.sent: list[tuple[bytes, tuple]] = []

  def setsockopt(self, *args):
    pass

  def sendto(self, packet, addr):
    self.sent.append((packet, addr))


def responder(monkeypatch, ours="192.168.1.20", clock=None):
  t = clock if clock is not None else [100.]
  r = mdns.Responder(mono=lambda: t[0])
  r.sock = FakeSock()
  r.joined = {3: ours}
  monkeypatch.setattr(mdns, "address_for", lambda peer: ours if peer.startswith("192.168.1.") else "10.64.0.2")
  return r, t


def response(name: str, ip: str) -> bytes:
  return struct.pack("!6H", 0, 0x8400, 0, 1, 0, 0) + mdns.encode_name(name) + struct.pack("!HHIH", 1, 1, 120, 4) + socket.inet_aton(ip)


def test_only_wifi_hotspot_ethernet_and_usb():
  assert all(mdns.local_interface(n) for n in ("wlan0", "eth0", "enx001122", "usb0", "ap0"))
  assert not any(mdns.local_interface(n) for n in ("lo", "wwan0", "rmnet_data0", "ppp0", "tun0", "wg0", "docker0"))


def test_only_local_addresses():
  assert all(mdns.local_address(ip) for ip in ("192.168.43.1", "10.0.0.5", "172.16.3.4", "169.254.1.2"))
  assert not any(mdns.local_address(ip) for ip in ("8.8.8.8", "100.64.1.1", "127.0.0.1", "fe80::1", "garbage"))


def test_questions_from_outside_or_through_another_interface_get_nothing(monkeypatch):
  r, _ = responder(monkeypatch)
  r.handle(query("lizpilot.local"), ("8.8.8.8", 5353))  # not a local address
  r.handle(query("lizpilot.local"), ("10.64.0.9", 5353))  # local range, but routed through an interface we don't serve
  assert r.sock.sent == []
  r.handle(query("lizpilot.local"), ("192.168.1.7", 5353))
  assert [mdns.answers(p) for p, _ in r.sock.sent] == [[("lizpilot.local", "192.168.1.20")]]


def test_reads_the_a_records_of_a_response():
  assert mdns.answers(response("LizPilot.local", "192.168.1.30")) == [("lizpilot.local", "192.168.1.30")]
  assert mdns.answers(mdns.answer("10.0.0.5", txid=9, legacy=True)) == [("lizpilot.local", "10.0.0.5")]
  assert mdns.answers(query("lizpilot.local")) == []  # a question, not an answer
  with pytest.raises(ValueError):
    mdns.answers(response("lizpilot.local", "192.168.1.30")[:-2])


def test_asks_for_the_name_before_announcing_it(monkeypatch):
  r, t = responder(monkeypatch)
  r.ask("192.168.1.20")
  assert mdns.questions(r.sock.sent[0][0])[1] == [("lizpilot.local", mdns.TYPE_A, mdns.CLASS_IN | mdns.UNICAST_BIT)]
  r.announce_due()
  assert len(r.sock.sent) == 1  # not yet
  t[0] += mdns.PROBE_S
  r.announce_due()
  assert mdns.answers(r.sock.sent[1][0]) == [("lizpilot.local", "192.168.1.20")]


def test_two_commas_the_higher_address_keeps_the_name(monkeypatch):
  r, t = responder(monkeypatch)
  r.ask("192.168.1.20")
  r.handle(response("lizpilot.local", "192.168.1.30"), ("192.168.1.30", 5353))  # higher: it keeps the name
  assert r.yielded and mdns.status()["state"] == "conflict"
  sent = len(r.sock.sent)
  t[0] += mdns.PROBE_S
  r.announce_due()
  r.handle(query("lizpilot.local"), ("192.168.1.7", 5353))
  assert len(r.sock.sent) == sent  # quiet: no announcement, no answers
  # it goes away: after a while this comma asks again
  t[0] += mdns.YIELD_RETRY_S
  monkeypatch.setattr(mdns, "interfaces", lambda: {3: "192.168.1.20"})
  r.rescan()
  assert not r.yielded and mdns.status()["state"] == "ok"
  assert mdns.questions(r.sock.sent[-1][0]) is not None  # a probe first


def test_two_commas_the_lower_address_gives_up(monkeypatch):
  r, _ = responder(monkeypatch, ours="192.168.1.40")
  r.handle(response("lizpilot.local", "192.168.1.30"), ("192.168.1.30", 5353))
  assert not r.yielded
  assert mdns.answers(r.sock.sent[-1][0]) == [("lizpilot.local", "192.168.1.40")]  # says it again, so the other gives up
  r.handle(response("lizpilot.local", "192.168.1.40"), ("192.168.1.40", 5353))  # our own record: no conflict
  r.handle(response("other.local", "192.168.1.99"), ("192.168.1.99", 5353))
  assert not r.yielded


def test_broken_packets_never_raise(monkeypatch):
  r, _ = responder(monkeypatch)
  rng = random.Random(7)
  for _ in range(500):
    r.handle(bytes(rng.randrange(256) for _ in range(rng.randrange(40))), ("192.168.1.7", 5353))
  r.handle(b"\x00\x00\x84\x00\x00\x00\x00\x01" + b"\x00" * 4 + b"\xc0\x0c", ("192.168.1.7", 5353))  # answer pointing at itself


def test_without_the_port_the_panel_keeps_working(monkeypatch):
  def fail(self):
    raise OSError("in use")
  monkeypatch.setattr(mdns.Responder, "open", fail)
  mdns.serve()  # returns: logs and stops, never raises
  assert mdns.status()["state"] == "off"
