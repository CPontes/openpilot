"""Settings → LizPilot: the fork's own options, built from openpilot/selfdrive/hexa/options.py, plus phone pairing.

Texts follow i18n.comma_lang, from the same catalogs as the phone panel."""
import time

from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigParamControl, BigMultiParamToggle, GreyBigButton
from openpilot.selfdrive.ui.mici.widgets.qr import QR
from openpilot.selfdrive.ui.layouts.settings.common import restart_needed_callback
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.hexa import i18n, phone
from openpilot.selfdrive.hexa.options import GROUPS, locked_while_engaged, options_in_group, supported

PHONE_REFRESH = 1.0  # s
IP_REFRESH = 10.0  # s


class HexaPairScroller(NavScroller):
  """Pairing screen: a QR that opens the phone panel, and the code to type there. Closes by itself
  when the code is used or expires."""

  def __init__(self, url: str, pin: str):
    super().__init__()
    self._params = Params()
    lang = i18n.comma_lang(self._params)
    self._scroller.add_widgets([
      QR(url),
      GreyBigButton(pin, i18n.tr("digite este código\nno celular", lang)),
      GreyBigButton("", i18n.tr("aponte a câmera do celular para o QR. o celular precisa estar no mesmo Wi-Fi ou no hotspot do comma.", lang)),
    ])

  def _update_state(self):
    super()._update_state()
    if phone.parse_pin(self._params.get(phone.PIN_PARAM)) is None:  # used or expired
      self.dismiss()


class HexaLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._lang = lang = i18n.comma_lang(self._params)
    self._param_controls: list[BigParamControl] = []
    widgets = []

    for group, _ in GROUPS:
      for opt in options_in_group(group):
        if not supported(opt, self._params):
          continue
        title = i18n.tr(opt.title, lang)
        description = (i18n.tr("em teste", lang) + " - " if opt.testing else "") + i18n.tr(opt.description, lang)  # "·" isn't in the font
        if opt.kind == "bool":
          widget = BigParamControl(title, opt.key, toggle_callback=restart_needed_callback if opt.restart_needed else None,
                                   description=description)
          self._param_controls.append(widget)
        else:
          widget = BigMultiParamToggle(title, opt.key, [i18n.tr(c, lang) for c in opt.choices], description=description)
        if locked_while_engaged(opt):  # an alert's restart, too, would stop openpilot for a second mid-drive (3X review)
          widget.set_enabled(lambda: not ui_state.engaged)
        widgets.append(widget)

    self._pair_btn = BigButton(i18n.tr("parear celular", lang), i18n.tr("toque para gerar o código", lang),
                               description=i18n.tr("Mostra um código de 6 dígitos por 2 minutos.\n" +
                                                   "No celular, na mesma rede, abra o endereço que aparecer e digite o código.", lang))
    self._pair_btn.set_click_callback(self._on_pair)
    self._pair_btn.set_visible(self._phone_enabled)

    self._unpair_btn = BigButton(i18n.tr("desconectar celulares", lang), "",
                                 description=i18n.tr("Remove o acesso de todos os celulares pareados.", lang))
    self._unpair_btn.set_click_callback(self._on_unpair)
    self._unpair_btn.set_visible(self._phone_enabled)

    widgets += [self._pair_btn, self._unpair_btn]
    self._scroller.add_widgets(widgets)

    self._last_phone_refresh = 0.0
    self._ip = ""
    self._last_ip = -IP_REFRESH

  def _phone_enabled(self) -> bool:
    return self._params.get_bool("HexaWebEnabled")

  def _url(self) -> str:
    now = time.monotonic()
    if now - self._last_ip >= IP_REFRESH:
      self._ip = phone.local_ip()
      self._last_ip = now
    return phone.panel_url(self._ip).removeprefix("http://")

  def _on_pair(self):
    stored = phone.new_pin()
    self._params.put(phone.PIN_PARAM, stored)
    self._last_ip = -IP_REFRESH
    self._refresh_phone()
    pin = phone.parse_pin(stored)
    if pin is not None:
      gui_app.push_widget(HexaPairScroller(phone.pair_url(self._ip), pin))

  def _on_unpair(self):
    self._params.remove(phone.TOKENS_PARAM)
    self._params.remove(phone.PIN_PARAM)
    self._refresh_phone()

  def _refresh_phone(self):
    pin = phone.parse_pin(self._params.get(phone.PIN_PARAM))
    if pin is not None:
      self._pair_btn.set_text(pin)
      self._pair_btn.set_value(i18n.tr(f"abra {self._url()}", self._lang))
    else:
      self._pair_btn.set_text(i18n.tr("parear celular", self._lang))
      self._pair_btn.set_value(i18n.tr("toque para gerar o código", self._lang))
    count = len(self._params.get(phone.TOKENS_PARAM) or [])
    self._unpair_btn.set_value(i18n.tr(f"{count} pareado(s)", self._lang) if count else i18n.tr("nenhum pareado", self._lang))

  def _update_state(self):
    super()._update_state()
    now = time.monotonic()
    if now - self._last_phone_refresh >= PHONE_REFRESH:
      self._last_phone_refresh = now
      self._refresh_phone()

  def show_event(self):
    super().show_event()
    for control in self._param_controls:
      control.refresh()
    self._refresh_phone()
