import json
import re

import pytest

from openpilot.selfdrive.hexa import i18n, i18n_keys
from openpilot.selfdrive.hexa.options import OPTIONS

FIELDS = re.compile(r"\{\w+\}")


@pytest.mark.parametrize("lang", ["en", "es"])
def test_catalog_is_valid(lang):
  raw = json.loads((i18n.CATALOG_DIR / f"{lang}.json").read_text(encoding="utf-8"))
  assert raw and all(isinstance(k, str) and k and isinstance(v, str) and v.strip() for k, v in raw.items())
  for src, dst in raw.items():
    assert sorted(FIELDS.findall(src)) == sorted(FIELDS.findall(dst)), f"{lang}: fields differ in {src!r}"
    assert src.count("\n") == dst.count("\n"), f"{lang}: line breaks differ in {src!r}"


@pytest.mark.parametrize("lang", ["en", "es"])
def test_every_text_is_translated(lang):
  # options, reasons, why-not texts, server messages, the comma page and the phone pages
  assert i18n_keys.missing(lang) == []


@pytest.mark.parametrize("lang", ["en", "es"])
def test_no_stale_translations(lang):
  used = {i18n_keys.norm(t) for t in i18n_keys.all_texts()}
  stale = [k for k in i18n.catalog(lang) if i18n_keys.norm(k) not in used]
  assert stale == [], "remove texts that no screen shows anymore"


def test_option_texts_are_covered():
  texts = i18n_keys.all_texts()
  for o in OPTIONS:
    assert {o.title, o.description} <= texts


def test_translate():
  assert i18n.tr("Código errado.", "pt") == "Código errado."
  assert i18n.tr("Código errado.", "en") == "Wrong code."
  assert i18n.tr("Código errado.", "es") == "Código incorrecto."
  assert i18n.tr("Pontos restaurados: 7. Valem na próxima partida do carro.", "en").startswith("Points restored: 7.")
  assert i18n.tr("um texto que não existe", "en") == "um texto que não existe"  # never hidden
  assert i18n.tr("", "en") == ""


def test_language_from_the_comma():
  assert i18n.lang_of("pt-BR") == "pt"
  assert i18n.lang_of("es") == "es"
  assert i18n.lang_of("en") == "en"
  assert i18n.lang_of("de") == "en"
  assert i18n.lang_of(None) == "en"

  class Params:
    def __init__(self, value):
      self.value = value

    def get(self, key):
      assert key == "LanguageSetting"
      return self.value

  assert i18n.comma_lang(Params("en")) == "pt"  # the default on a comma 4, which has no language menu
  assert i18n.comma_lang(Params(None)) == "pt"
  assert i18n.comma_lang(Params("de")) == "en"
  assert i18n.comma_lang(Params("pt-BR")) == "pt"
  assert i18n.comma_lang(Params("es")) == "es"
  assert i18n.comma_lang(Params("main_en")) == "pt" and i18n.comma_lang(Params("main_pt-BR")) == "pt"  # older versions' prefix


def test_the_language_menu_of_the_comma_3x_counts():
  # the comma 3X has a language menu: "pt-BR" today, "main_pt-BR" in older versions (system/ui/lib/multilang.py)
  for code, lang in (("pt-BR", "pt"), ("main_pt-BR", "pt"), ("es", "es"), ("main_es", "es"), ("de", "en"), ("main_de", "en")):
    assert i18n.lang_of(code) == lang, code
