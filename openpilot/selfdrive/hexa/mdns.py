"""lizpilot.local: one fixed name for the phone panel on the local network.

The comma's address changes with the network it is on (home Wi-Fi, its own hotspot, a phone's hotspot), and a phone
keeps the panel by address. This is a minimal mDNS responder for that one name: it answers A questions for
lizpilot.local with the comma's address on the network the question came from. IPv4 only, no other records, nothing
leaves the local network (multicast TTL 255 is the mDNS rule; routers don't forward 224.0.0.251).

Runs as a thread inside hexaweb, so it only exists while the phone panel is on (the manager runs hexaweb only with
HexaWebEnabled). If the port can't be shared (another responder holding it without SO_REUSEPORT), it logs and stops:
the panel keeps working by address.

Local only: it joins the group on Wi-Fi, hotspot, Ethernet and USB interfaces, never the cellular modem, and drops
anything from an address outside the local ranges or routed through another interface.

Two commas on one network: each asks for the name before taking it. If another answers with its own address, the one
with the higher address keeps the name and the other stays quiet and says so on the panel ("conflict"); it tries again
every minute and a half. Anyone on the network can answer for a name, so the panel never trusts the name alone: before
moving a phone there it checks that the name reaches the comma that served the page (hexaweb, /api/fixed/*).
"""
import fcntl
import ipaddress
import select
import socket
import struct
import threading
import time

NAME = "lizpilot.local"
GROUP = "224.0.0.251"
PORT = 5353
TTL = 120
LEGACY_TTL = 10  # RFC 6762 6.7: a one-shot (non-5353) querier gets a short TTL
TYPE_A, TYPE_ANY, CLASS_IN, CLASS_ANY = 1, 255, 1, 255
UNICAST_BIT = 0x8000  # in a question: answer me directly · in an answer: cache flush (the record is ours alone)
RESCAN_S = 30.  # interfaces come and go (hotspot on, phone hotspot joined)
SIOCGIFADDR = 0x8915
MAX_QUESTIONS = 32
MAX_RECORDS = 32
PROBE_S = 1.  # wait this long for someone else holding the name before announcing it
YIELD_RETRY_S = 90.  # after giving the name up, ask again once the other comma stays quiet this long
# Wi-Fi (wlan0, also the comma's hotspot), Ethernet and USB networking. The cellular modem (wwan, rmnet) never.
LOCAL_IFACES = ("wlan", "wl", "eth", "en", "usb", "ap")

_status = {"state": "starting", "name": NAME}
_status_lock = threading.Lock()


def status() -> dict:
  """For the panel: starting, ok, conflict (another comma has the name on this network) or off (port unavailable)."""
  with _status_lock:
    return dict(_status)


def _set_status(state: str, name: str = NAME) -> None:
  with _status_lock:
    _status.update(state=state, name=name)


def local_interface(name: str) -> bool:
  return name.startswith(LOCAL_IFACES)


def local_address(ip: str) -> bool:
  try:
    a = ipaddress.ip_address(ip)
  except ValueError:
    return False
  return a.version == 4 and (a.is_private or a.is_link_local) and not a.is_loopback


def encode_name(name: str) -> bytes:
  out = b""
  for label in name.rstrip(".").split("."):
    raw = label.encode()
    if not 0 < len(raw) < 64:
      raise ValueError("bad label")
    out += bytes([len(raw)]) + raw
  return out + b"\x00"


def read_name(data: bytes, pos: int) -> tuple[str, int]:
  """The name at pos, following compression pointers. Returns it lowercased and the position after it."""
  labels: list[str] = []
  end = None
  for _ in range(128):
    if pos >= len(data):
      raise ValueError("truncated")
    n = data[pos]
    if n & 0xC0 == 0xC0:  # pointer
      if pos + 1 >= len(data):
        raise ValueError("truncated")
      if end is None:
        end = pos + 2
      pos = ((n & 0x3F) << 8) | data[pos + 1]
      continue
    if n == 0:
      return ".".join(labels).lower(), end if end is not None else pos + 1
    if n > 63:
      raise ValueError("bad label")
    labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
    pos += 1 + n
  raise ValueError("loop")


def questions(packet: bytes) -> tuple[int, list[tuple[str, int, int]]] | None:
  """(id, [(name, type, class)]) of a standard query; None for anything else (responses, other opcodes)."""
  if len(packet) < 12:
    return None
  txid, flags, qdcount = struct.unpack("!3H", packet[:6])
  if flags & 0x8000 or (flags >> 11) & 0xF:
    return None
  out, pos = [], 12
  for _ in range(min(qdcount, MAX_QUESTIONS)):
    name, pos = read_name(packet, pos)
    if pos + 4 > len(packet):
      raise ValueError("truncated")
    qtype, qclass = struct.unpack("!HH", packet[pos:pos + 4])
    pos += 4
    out.append((name, qtype, qclass))
  return txid, out


def answers(packet: bytes) -> list[tuple[str, str]]:
  """[(name, address)] of the A records in a response (answers and additionals); [] for anything else."""
  if len(packet) < 12:
    return []
  flags, qdcount, ancount, nscount, arcount = struct.unpack("!5H", packet[2:12])
  if not flags & 0x8000:
    return []
  pos = 12
  for _ in range(min(qdcount, MAX_QUESTIONS)):
    _, pos = read_name(packet, pos)
    pos += 4
  out = []
  for _ in range(min(ancount + nscount + arcount, MAX_RECORDS)):
    name, pos = read_name(packet, pos)
    if pos + 10 > len(packet):
      raise ValueError("truncated")
    rtype, _, _, rdlen = struct.unpack("!HHIH", packet[pos:pos + 10])
    pos += 10
    if pos + rdlen > len(packet):
      raise ValueError("truncated")
    if rtype == TYPE_A and rdlen == 4:
      out.append((name, socket.inet_ntoa(packet[pos:pos + 4])))
    pos += rdlen
  return out


def probe(name: str = NAME) -> bytes:
  """Who has this name? Asked before taking it (one question, answer me directly)."""
  return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + encode_name(name) + struct.pack("!HH", TYPE_A, CLASS_IN | UNICAST_BIT)


def asked(qs: list[tuple[str, int, int]], name: str = NAME) -> tuple[bool, bool]:
  """Whether the questions ask for our A record, and whether one of them wants the answer by unicast."""
  hit = unicast = False
  for qname, qtype, qclass in qs:
    if qname == name and qtype in (TYPE_A, TYPE_ANY) and (qclass & 0x7FFF) in (CLASS_IN, CLASS_ANY):
      hit = True
      unicast = unicast or bool(qclass & UNICAST_BIT)
  return hit, unicast


def answer(ip: str, name: str = NAME, txid: int = 0, legacy: bool = False) -> bytes:
  """Our A record. A legacy (one-shot) querier gets its id and question back, a short TTL and no cache flush."""
  header = struct.pack("!6H", txid if legacy else 0, 0x8400, 1 if legacy else 0, 1, 0, 0)  # response, authoritative
  question = encode_name(name) + struct.pack("!HH", TYPE_A, CLASS_IN) if legacy else b""
  record = encode_name(name) + struct.pack("!HHIH", TYPE_A, CLASS_IN if legacy else CLASS_IN | UNICAST_BIT,
                                           LEGACY_TTL if legacy else TTL, 4) + socket.inet_aton(ip)
  return header + question + record


def address_for(peer: str) -> str | None:
  """The comma's address on the network a peer is on. Nothing is sent: connecting UDP only picks the route."""
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
      s.connect((peer, 9))
      ip = s.getsockname()[0]
  except OSError:
    return None
  return ip if ip and ip != "0.0.0.0" else None


def interfaces() -> dict[int, str]:
  """{index: IPv4 address} of the local interfaces (Wi-Fi, hotspot, Ethernet, USB) that have a local address."""
  out = {}
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    for index, name in socket.if_nameindex():
      if not local_interface(name):
        continue
      try:
        raw = fcntl.ioctl(s.fileno(), SIOCGIFADDR, struct.pack("256s", name[:15].encode()))
      except OSError:
        continue  # no IPv4 address
      ip = socket.inet_ntoa(raw[20:24])
      if local_address(ip):
        out[index] = ip
  return out


class Responder:
  def __init__(self, name: str = NAME, port: int = PORT, group: str = GROUP, log=None, mono=time.monotonic):
    self.name, self.port, self.group = name.lower(), port, group
    self.sock: socket.socket | None = None
    self.joined: dict[int, str] = {}
    self.member: set[int] = set()  # interfaces whose group membership the kernel holds for this socket
    self.log = log or (lambda msg: None)
    self.mono = mono
    self.announce_at: dict[str, float] = {}  # address -> when to announce it, once nobody answered the probe
    self.yielded = False
    self.conflict_at = -1e9

  def open(self) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
      s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("", self.port))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
    self.sock = s

  def rescan(self) -> None:
    """Join the mDNS group on new interfaces and ask for the name there (an interface that left drops by itself)."""
    assert self.sock is not None
    if self.yielded and self.mono() - self.conflict_at >= YIELD_RETRY_S:  # the other comma went quiet: ask again
      self.yielded = False
      for ip in self.joined.values():
        self.ask(ip)
    now = interfaces()
    for index, ip in now.items():
      if self.joined.get(index) == ip:
        continue
      if index not in self.member:
        try:
          mreqn = struct.pack("4s4si", socket.inet_aton(self.group), socket.inet_aton("0.0.0.0"), index)
          self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreqn)
        except OSError:
          continue
        self.member.add(index)
      self.joined[index] = ip
      self.ask(ip)
    for index in set(self.joined) - set(now):
      del self.joined[index]
    self.member &= set(now)  # a removed interface takes its membership with it
    if not self.yielded:
      _set_status("ok" if self.joined else "starting", self.name)

  def ask(self, ip: str) -> None:
    """Probe for the name on this network; announce it a moment later unless someone else holds it."""
    self.send_multicast(ip, probe(self.name))
    self.announce_at[ip] = self.mono() + PROBE_S

  def announce_due(self) -> None:
    for ip, at in list(self.announce_at.items()):
      if self.mono() >= at:
        del self.announce_at[ip]
        if not self.yielded and ip in self.joined.values():
          self.send_multicast(ip)

  def conflict(self, theirs: str, ours: str) -> None:
    """Someone else answers for our name. The higher address keeps it (RFC 6762 9: the later record wins)."""
    self.conflict_at = self.mono()
    if socket.inet_aton(theirs) > socket.inet_aton(ours):
      if not self.yielded:
        self.log(f"hexaweb: {theirs} also answers for {self.name}; this comma stays on its address")
      self.yielded = True
      self.announce_at.clear()
      _set_status("conflict", self.name)
    elif not self.yielded:
      self.send_multicast(ours)  # ours: say it again so the other one sees it and gives up

  def send_multicast(self, ip: str, packet: bytes | None = None) -> None:
    assert self.sock is not None
    try:
      self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
      self.sock.sendto(packet if packet is not None else answer(ip, self.name), (self.group, self.port))
    except OSError:
      pass

  def handle(self, packet: bytes, peer: tuple[str, int]) -> None:
    if not local_address(peer[0]):
      return  # never anyone outside the local network
    ip = address_for(peer[0])
    if ip is None or ip not in self.joined.values():
      return  # it came in on (or would go out through) an interface we don't serve, like the cellular modem
    try:
      records = answers(packet)
      q = questions(packet)
    except ValueError:
      return
    for name, addr in records:
      if name == self.name and addr not in self.joined.values():
        self.conflict(addr, ip)
    if q is None or self.yielded:
      return
    txid, qs = q
    hit, unicast = asked(qs, self.name)
    if not hit:
      return
    assert self.sock is not None
    if peer[1] != self.port:  # a one-shot resolver (dig, some phones): reply to it only
      self.sock.sendto(answer(ip, self.name, txid, legacy=True), peer)
      return
    if unicast:
      self.sock.sendto(answer(ip, self.name), peer)
    self.send_multicast(ip)

  def run(self, stop=lambda: False) -> None:
    if self.sock is None:
      self.open()
    assert self.sock is not None
    last = 0.
    while not stop():
      if time.monotonic() - last >= RESCAN_S:
        self.rescan()
        last = time.monotonic()
      self.announce_due()
      ready, _, _ = select.select([self.sock], [], [], .25)
      if ready:
        try:
          packet, peer = self.sock.recvfrom(9000)
        except OSError:
          continue
        self.handle(packet, peer)


def serve(name: str = NAME) -> None:
  """hexaweb's thread. Never raises: without the name, the panel still works by address."""
  from openpilot.common.swaglog import cloudlog
  responder = Responder(name, log=cloudlog.warning)
  try:
    responder.open()
  except OSError:
    _set_status("off", name)
    cloudlog.exception(f"hexaweb: {name} unavailable, the panel works by address only")
    return
  cloudlog.info(f"hexaweb: answering for {name}")
  while True:
    try:
      responder.run()
    except Exception:
      cloudlog.exception("hexaweb: mDNS responder failed, trying again")
      time.sleep(5.)
