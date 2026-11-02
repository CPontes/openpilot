"""The comma's network, from the phone: Wi-Fi, its hotspot and data use, through the same WifiManager the screen uses.

Created on first use, so nothing talks to NetworkManager unless a phone opens Rede. Every change that can take the
phone off the comma goes through the backend's guard (hexaweb): if the same phone doesn't reach the comma again within
GUARD_S, the comma goes back to the network it was on.
"""
GUARD_S = 120.
METERED = ("padrão", "medido", "sem limite")  # MeteredType.UNKNOWN, YES, NO in wifi_manager


class CommaNetwork:
  def __init__(self, factory=None):
    self._factory = factory
    self._wifi = None

  def wifi(self):
    if self._wifi is None:
      if self._factory is None:
        from openpilot.system.ui.lib.wifi_manager import WifiManager
        self._factory = WifiManager
      self._wifi = self._factory()
    return self._wifi

  def state(self) -> dict:
    w = self.wifi()
    try:
      w.process_callbacks()  # nothing reads them here, but the queue must not grow
    except Exception:
      pass
    nets = []
    for n in list(w.networks)[:30]:
      nets.append({"ssid": n.ssid, "strength": int(getattr(n, "strength", 0)), "secured": int(getattr(n, "security_type", 0)) != 0,
                   "saved": bool(w.is_connection_saved(n.ssid)), "tethering": bool(getattr(n, "is_tethering", False))})
    metered = int(getattr(w.current_network_metered, "value", w.current_network_metered) or 0)
    return {"ssid": w.connected_ssid, "connecting": w.connecting_to_ssid, "ip": w.ipv4_address, "networks": nets,
            "tethering": bool(w.is_tethering_active()), "metered": metered if 0 <= metered < len(METERED) else 0}

  def tethering_password(self) -> str:
    return self.wifi().tethering_password

  def snapshot(self) -> dict:
    """What to go back to."""
    w = self.wifi()
    return {"ssid": w.connected_ssid, "tethering": bool(w.is_tethering_active())}

  def restore(self, snap: dict) -> None:
    w = self.wifi()
    if snap.get("tethering"):
      w.set_tethering_active(True)
    else:
      if w.is_tethering_active():
        w.set_tethering_active(False)
      if snap.get("ssid"):
        w.activate_connection(snap["ssid"])

  def connect(self, ssid: str, password: str) -> None:
    w = self.wifi()
    if w.is_connection_saved(ssid) and not password:
      w.activate_connection(ssid)
    else:
      w.connect_to_network(ssid, password)

  def forget(self, ssid: str) -> None:
    self.wifi().forget_connection(ssid)

  def set_tethering(self, on: bool) -> None:
    self.wifi().set_tethering_active(on)

  def set_tethering_password(self, password: str) -> None:
    self.wifi().set_tethering_password(password)

  def set_metered(self, value: int) -> None:
    from openpilot.system.ui.lib.wifi_manager import MeteredType
    self.wifi().set_current_network_metered(MeteredType(value))
