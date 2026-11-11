"""Settings → LizPilot on the comma 3X (and comma three): the same page the comma 4 has
(selfdrive/ui/mici/layouts/settings/hexa.py), built with the big screen's list widgets.

The options come from openpilot/selfdrive/hexa/options.py and the texts from the same catalogs as the
phone panel, so both comma screens and the phone always show the same thing."""
import time

import pyray as rl

from openpilot.common.params import Params
from openpilot.common.qrcode import make_texture
from openpilot.common.swaglog import cloudlog
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import IconButton
from openpilot.system.ui.widgets.list_view import ListItem, button_item, multiple_button_item, toggle_item
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.hexa import i18n, phone
from openpilot.selfdrive.hexa.options import GROUPS, HexaOption, locked_while_engaged, options_in_group, supported

PHONE_REFRESH = 1.0  # s
IP_REFRESH = 10.0  # s
CHOICE_WIDTH = 200  # px, an option with choices (e.g. 1 s / 2 s / 3 s)
QR_RETRY = 5.0  # s


class HexaPairDialog(Widget):
  """Pairing, full screen like the comma's own pairing dialog: the QR opens the phone panel, and the code is
  typed there. Closes by itself when the code is used or expires, or with the X."""

  def __init__(self, url: str, pin: str):
    super().__init__()
    self._params = Params()
    self._lang = i18n.comma_lang(self._params)
    self._url = url
    self._pin = pin
    self._qr: rl.Texture | None = None
    self._qr_tried = -QR_RETRY
    self._close_btn = IconButton(gui_app.texture("icons/close.png", 80, 80))
    self._close_btn.set_click_callback(gui_app.pop_widget)

  def _update_state(self):
    # used or expired; or the car started: the big screen renders only the top widget, and the road view must not wait
    if phone.parse_pin(self._params.get(phone.PIN_PARAM)) is None or ui_state.started:
      gui_app.pop_widget()

  def _qr_texture(self):  # no annotation: in pyray, rl.Texture is a function and "| None" fails on import
    if self._qr is None and time.monotonic() - self._qr_tried >= QR_RETRY:  # a failure is tried again, not every frame
      self._qr_tried = time.monotonic()
      try:
        self._qr = make_texture(self._url)
      except Exception:
        cloudlog.exception("hexa pairing QR failed")
    return self._qr

  def _render(self, rect: rl.Rectangle):
    rl.clear_background(rl.Color(224, 224, 224, 255))
    margin = 70
    content = rl.Rectangle(rect.x + margin, rect.y + margin, rect.width - 2 * margin, rect.height - 2 * margin)
    y = content.y

    close_size, pad = 80, 20
    self._close_btn.render(rl.Rectangle(content.x - pad, y - pad, close_size + pad * 2, close_size + pad * 2))
    y += close_size + 40

    left_w = int(content.width * 0.5 - 15)
    font = gui_app.font(FontWeight.NORMAL)
    bold = gui_app.font(FontWeight.BOLD)
    title = wrap_text(font, i18n.tr("parear celular", self._lang), 75, left_w)
    rl.draw_text_ex(font, "\n".join(title), rl.Vector2(content.x, y), 75, 0, rl.BLACK)
    y += len(title) * 75 + 40

    # the code, big: it is what the phone asks for
    rl.draw_text_ex(bold, self._pin, rl.Vector2(content.x, y), 150, 12, rl.BLACK)
    y += 160
    sub = wrap_text(bold, i18n.tr("digite este código\nno celular", self._lang).replace("\n", " "), 47, left_w)
    rl.draw_text_ex(bold, "\n".join(sub), rl.Vector2(content.x, y), 47, 0, rl.Color(70, 70, 70, 255))
    y += len(sub) * 47 + 40
    hint = wrap_text(font, i18n.tr("aponte a câmera do celular para o QR. o celular precisa estar no mesmo Wi-Fi ou no hotspot do comma.",
                                   self._lang), 40, left_w)
    rl.draw_text_ex(font, "\n".join(hint), rl.Vector2(content.x, y), 40, 0, rl.Color(70, 70, 70, 255))

    right_w = content.width // 2 - 20
    qr_size = min(right_w, content.height) - 40
    qr_rect = rl.Rectangle(content.x + left_w + 40 + (right_w - qr_size) // 2, content.y, qr_size, qr_size)
    tex = self._qr_texture()
    if tex is not None:
      rl.draw_texture_pro(tex, rl.Rectangle(0, 0, tex.width, tex.height), qr_rect, rl.Vector2(0, 0), 0, rl.WHITE)
    else:
      rl.draw_rectangle_rounded(qr_rect, 0.1, 20, rl.Color(240, 240, 240, 255))
      url = self._url.removeprefix("http://")
      size = measure_text_cached(bold, url, 40)
      rl.draw_text_ex(bold, url, rl.Vector2(qr_rect.x + (qr_rect.width - size.x) / 2, qr_rect.y + qr_rect.height / 2 - 20), 40, 0, rl.BLACK)
    return -1

  def __del__(self):
    if self._qr is not None and self._qr.id != 0:
      rl.unload_texture(self._qr)


class HexaLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._lang = lang = i18n.comma_lang(self._params)
    self._bools: dict[str, ListItem] = {}
    self._enums: dict[str, tuple[ListItem, HexaOption]] = {}
    items: list[Widget] = []

    for group, _ in GROUPS:
      for opt in options_in_group(group):
        if not supported(opt, self._params):
          continue
        title = i18n.tr(opt.title, lang)
        description = (i18n.tr("em teste", lang) + " - " if opt.testing else "") + i18n.tr(opt.description, lang)  # "·" isn't in the font
        if opt.kind == "bool":
          item = toggle_item(title, description, self._params.get_bool(opt.key), callback=lambda state, o=opt: self._set_bool(o, state))
          self._bools[opt.key] = item
        else:
          item = multiple_button_item(title, description, [i18n.tr(c, lang) for c in opt.choices], self._enum_value(opt),
                                      button_width=CHOICE_WIDTH, callback=lambda i, o=opt: self._set_enum(o, i))
          self._enums[opt.key] = (item, opt)
        if locked_while_engaged(opt):  # same rule as the comma 4: driving options, and those whose restart would stop openpilot
          item.action_item.set_enabled(lambda: not ui_state.engaged)
        items.append(item)

    self._pair_item = button_item(i18n.tr("parear celular", lang), i18n.tr("parear", lang),
                                  description=i18n.tr("Mostra um código de 6 dígitos por 2 minutos.\n" +
                                                      "No celular, na mesma rede, abra o endereço que aparecer e digite o código.", lang),
                                  callback=self._on_pair)
    self._pair_item.set_visible(self._phone_enabled)
    self._unpair_item = button_item(i18n.tr("desconectar celulares", lang), i18n.tr("remover", lang),
                                    description=i18n.tr("Remove o acesso de todos os celulares pareados.", lang),
                                    callback=self._on_unpair)
    self._unpair_item.set_visible(self._phone_enabled)
    items += [self._pair_item, self._unpair_item]

    self._scroller = Scroller(items, line_separator=True, spacing=0)
    self._last_phone_refresh = 0.0
    self._ip = ""
    self._last_ip = -IP_REFRESH

  # *** options ***

  def _enum_value(self, opt: HexaOption) -> int:
    try:
      v = self._params.get(opt.key)
      return int(opt.default if v is None else v)
    except Exception:
      return int(opt.default)

  def _set_bool(self, opt: HexaOption, state: bool):
    self._params.put_bool(opt.key, state, block=True)
    if opt.restart_needed:
      self._params.put_bool("OnroadCycleRequested", True, block=True)

  def _set_enum(self, opt: HexaOption, index: int):
    self._params.put(opt.key, index, block=True)
    if opt.restart_needed:
      self._params.put_bool("OnroadCycleRequested", True, block=True)

  def _refresh_options(self):
    """Mirror changes made elsewhere (the phone panel or the other screen)."""
    for key, item in self._bools.items():
      item.action_item.set_state(self._params.get_bool(key))
    for item, opt in self._enums.values():
      item.action_item.set_selected_button(self._enum_value(opt))

  # *** phone ***

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
      gui_app.push_widget(HexaPairDialog(phone.pair_url(self._ip), pin))

  def _on_unpair(self):
    self._params.remove(phone.TOKENS_PARAM)
    self._params.remove(phone.PIN_PARAM)
    self._refresh_phone()

  def _refresh_phone(self):
    pin = phone.parse_pin(self._params.get(phone.PIN_PARAM))
    action = self._pair_item.action_item
    if pin is not None:
      action.set_value(f"{pin} - {self._url()}")
    else:
      action.set_value("")
    count = len(self._params.get(phone.TOKENS_PARAM) or [])
    self._unpair_item.action_item.set_value(i18n.tr(f"{count} pareado(s)", self._lang) if count else i18n.tr("nenhum pareado", self._lang))

  # *** widget ***

  def _update_state(self):
    now = time.monotonic()
    if now - self._last_phone_refresh >= PHONE_REFRESH:
      self._last_phone_refresh = now
      self._refresh_phone()

  def show_event(self):
    super().show_event()
    self._scroller.show_event()
    self._refresh_options()
    self._refresh_phone()

  def _render(self, rect: rl.Rectangle):
    self._scroller.render(rect)
