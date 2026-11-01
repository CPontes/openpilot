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


def test_a_one_shot_resolver_gets_an_answer():
  """The real socket, on loopback: a resolver asking from its own port gets a direct answer."""
  r = mdns.Responder(port=0)
  r.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  r.sock.bind(("127.0.0.1", 0))
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
