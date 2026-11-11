"""LizPilot route: the phone picks where to go, and openpilot's own navigation (selfdrive/navigation, alpha) takes it there.

The phone sends an address, a saved place, coordinates or a link shared from Google Maps, Waze, Apple Maps or OpenStreetMap.
The comma turns an address into coordinates with OpenStreetMap's Nominatim (no key, at most one search a second, as its
usage policy asks), and writes NavDestination, which navd reads: navd asks for the route (Mapbox, through comma's API or
the owner's own key) and publishes navInstruction and navRoute, which the comma's screen and the phone show.

Nothing here touches how the car drives: openpilot keeps the lane and the speed; turning at a junction is the driver's.
The places and the Mapbox key stay on the comma (DONT_LOG params).
"""
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

DEST_PARAM = "NavDestination"  # openpilot's own: {"latitude", "longitude", "place_name"}; cleared at boot and when the car turns off
ENABLED_PARAM = "NavigationEnabled"  # openpilot's own toggle, on the comma's screen only (comma_ctl level "so")
PLACES_PARAM = "HexaNavPlaces"  # [{"name", "latitude", "longitude"}], saved from the phone. DONT_LOG
TOKEN_PARAM = "HexaMapboxToken"  # the owner's own Mapbox key, if comma's API doesn't route for this comma. DONT_LOG

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "LizPilot/1 (+https://www.lizpilot.app)"  # Nominatim's policy: say who asks
SEARCH_EVERY_S = 1.1
SEARCH_TIMEOUT = 8.
MAX_RESULTS = 5
MAX_PLACES = 10
MAX_NAME = 40  # a saved place
MAX_LABEL = 120  # an address found, and the destination's name
MAX_QUERY = 200
NEAR_DEG = 1.5  # results are searched around the car first: about 150 km


class NavError(Exception):
  """What the phone shows (Portuguese, translated there)."""


def valid_point(lat, lon) -> bool:
  return all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)) \
    and -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


_NUM = r"(-?\d{1,3}(?:\.\d+)?)"
_LINK_PATTERNS = (
  re.compile(r"@" + _NUM + "," + _NUM),  # google.com/maps/@lat,lon,zoom and /place/.../@lat,lon
  re.compile(r"!3d" + _NUM + "!4d" + _NUM),  # google.com/maps/place/...!3dlat!4dlon (the pin itself)
  re.compile(r"[?&](?:q|query|ll|daddr|destination|sll)=" + _NUM + r"(?:,|%2C)\s*" + _NUM, re.I),  # ?q=lat,lon (Google, Apple)
  re.compile(r"[?&]ll=" + _NUM + r"(?:%2C|,)" + _NUM, re.I),  # waze.com/ul?ll=lat,lon
  re.compile(r"[?&]mlat=" + _NUM + r"&mlon=" + _NUM, re.I),  # openstreetmap.org/?mlat=..&mlon=..
  re.compile(r"#map=\d+/" + _NUM + "/" + _NUM),  # openstreetmap.org/#map=z/lat/lon
  re.compile(r"^geo:" + _NUM + "," + _NUM, re.I),  # geo:lat,lon
  re.compile(r"^\s*" + _NUM + r"\s*[,;\s]\s*" + _NUM + r"\s*$"),  # "-22.9, -43.2"
)


def parse_point(text: str) -> tuple[float, float] | None:
  """Coordinates written down or inside a shared map link, or None. Short links (maps.app.goo.gl) aren't followed: the
  phone opens them and copies the long one."""
  text = urllib.parse.unquote(str(text or "").strip())[:2000]
  for pat in _LINK_PATTERNS:
    m = pat.search(text)
    if m:
      lat, lon = float(m.group(1)), float(m.group(2))
      if valid_point(lat, lon):
        return round(lat, 6), round(lon, 6)
  return None


def clean_name(name, limit: int = MAX_NAME) -> str:
  return " ".join("".join(c for c in str(name or "") if c.isprintable()).split())[:limit]


def http_get_json(url: str, timeout: float = SEARCH_TIMEOUT):
  req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt,en"})
  with urllib.request.urlopen(req, timeout=timeout) as r:
    return json.loads(r.read(256 * 1024))


class Navigator:
  """What the phone asks about the route. Never raises past its callers' answers; every write is a param navd already reads."""

  def __init__(self, store, get_json: Callable[[str], object] = http_get_json, mono: Callable[[], float] = time.monotonic,
               position: Callable[[], tuple[float, float] | None] = lambda: None):
    self.store = store
    self.get_json = get_json
    self.mono = mono
    self.position = position  # the car's last GPS fix (lat, lon), to search near it first
    self._last_search = -SEARCH_EVERY_S
    self._lock = threading.Lock()
    self._cache: dict[str, list[dict]] = {}

  # search

  def search(self, query: str) -> list[dict]:
    """Up to MAX_RESULTS places for an address, near the car first. Coordinates and links need no search."""
    query = " ".join(str(query or "").split())[:MAX_QUERY]
    if len(query) < 3:
      raise NavError("Escreva o endereço.")
    point = parse_point(query)
    if point is not None:
      return [{"name": f"{point[0]:.5f}, {point[1]:.5f}", "latitude": point[0], "longitude": point[1]}]
    if query.lower().startswith(("https://maps.app.goo.gl", "https://goo.gl", "https://waze.com/ul/h")):
      raise NavError("Link curto: abra no Maps e copie o link completo, ou o endereço.")
    near = self.position()
    key = f"{query.lower()}|{near and (round(near[0], 1), round(near[1], 1))}"
    with self._lock:
      if key in self._cache:
        return self._cache[key]
      wait = SEARCH_EVERY_S - (self.mono() - self._last_search)
      if wait > 0:
        raise NavError("Espere um instante entre buscas.")
      self._last_search = self.mono()
    q = {"q": query, "format": "jsonv2", "limit": str(MAX_RESULTS), "countrycodes": "br", "addressdetails": "0"}
    if near is not None:
      lat, lon = near
      q["viewbox"] = f"{lon - NEAR_DEG},{lat + NEAR_DEG},{lon + NEAR_DEG},{lat - NEAR_DEG}"  # preferred, not a limit
    try:
      raw = self.get_json(NOMINATIM + "?" + urllib.parse.urlencode(q))
    except Exception as e:
      raise NavError("Sem internet no comma para buscar o endereço.") from e
    out = []
    for r in raw if isinstance(raw, list) else []:
      try:
        lat, lon = float(r["lat"]), float(r["lon"])
      except (KeyError, TypeError, ValueError):
        continue
      if valid_point(lat, lon):
        out.append({"name": clean_name(r.get("display_name", ""), MAX_LABEL) or f"{lat:.5f}, {lon:.5f}",
                    "latitude": round(lat, 6), "longitude": round(lon, 6)})
    with self._lock:
      self._cache = dict(list(self._cache.items())[-50:]) | {key: out}
    return out

  # the destination

  def enabled(self) -> bool:
    return bool(self.store.get_bool(ENABLED_PARAM))

  def go(self, lat, lon, name: str = "") -> dict:
    if not self.enabled():
      raise NavError("Ligue \"navegação (alfa)\" na tela do comma: Configurações, Desenvolvedor.")
    if not valid_point(lat, lon):
      raise NavError("Destino inválido.")
    dest = {"latitude": float(lat), "longitude": float(lon), "place_name": clean_name(name, MAX_LABEL) or f"{lat:.5f}, {lon:.5f}"}
    self.store.put(DEST_PARAM, dest)
    return dest

  def stop(self) -> None:
    self.store.remove(DEST_PARAM)

  def destination(self) -> dict | None:
    d = self.store.get(DEST_PARAM)
    return d if isinstance(d, dict) and valid_point(d.get("latitude"), d.get("longitude")) else None

  # saved places

  def places(self) -> list[dict]:
    p = self.store.get(PLACES_PARAM)
    return [x for x in p if isinstance(x, dict) and valid_point(x.get("latitude"), x.get("longitude")) and x.get("name")] \
      if isinstance(p, list) else []

  def save_place(self, name, lat, lon) -> list[dict]:
    name = clean_name(name)
    if not name:
      raise NavError("Dê um nome ao lugar.")
    if not valid_point(lat, lon):
      raise NavError("Destino inválido.")
    places = [p for p in self.places() if p["name"].lower() != name.lower()]
    if len(places) >= MAX_PLACES:
      raise NavError(f"No máximo {MAX_PLACES} lugares salvos.")
    places.append({"name": name, "latitude": round(float(lat), 6), "longitude": round(float(lon), 6)})
    self.store.put(PLACES_PARAM, places)
    return places

  def remove_place(self, name) -> list[dict]:
    places = [p for p in self.places() if p["name"].lower() != clean_name(name).lower()]
    self.store.put(PLACES_PARAM, places)
    return places

  # the owner's Mapbox key

  def set_token(self, token) -> bool:
    token = str(token or "").strip()
    if not token:
      self.store.remove(TOKEN_PARAM)
      return False
    if not re.fullmatch(r"pk\.[A-Za-z0-9._-]{20,400}", token):  # a public Mapbox token: navd sends it in the request's URL
      raise NavError("Chave Mapbox inválida: ela começa com pk.")
    self.store.put(TOKEN_PARAM, token)
    return True


def route_view(instruction, route_points: list[tuple[float, float]] | None, alive: bool) -> dict:
  """What the phone shows of the route now: the next turn, how far, and the path (thinned), from navd's messages."""
  out: dict = {"active": False}
  if instruction is not None and alive:
    out = {
      "active": True,
      "next": instruction.maneuverPrimaryText,
      "then": instruction.maneuverSecondaryText,
      "type": instruction.maneuverType,
      "modifier": instruction.maneuverModifier,
      "in_m": round(float(instruction.maneuverDistance)),
      "left_m": round(float(instruction.distanceRemaining)),
      "left_s": round(float(instruction.timeRemaining)),
    }
  if route_points:
    step = max(1, math.ceil(len(route_points) / 300))  # a drive of an hour is a few thousand points: 300 draw it
    pts = route_points[::step]
    if pts[-1] != route_points[-1]:
      pts.append(route_points[-1])
    out["path"] = [[round(lat, 5), round(lon, 5)] for lat, lon in pts]
  return out
