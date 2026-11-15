"""LizPilot texts in Portuguese, English and Spanish, for the comma screen and the phone panel.

The texts are written in Portuguese in the code. Each other language has one catalog, i18n/<lang>.json, that maps
the Portuguese text to the translated one. The comma screen translates here, by the comma's own language; the phone
panel gets the same files (/i18n/<lang>.json) and translates in the browser, by the phone's language.

A text missing from a catalog shows in Portuguese: a missing translation never hides a text. A text with {name}
parts is matched as a template, so a message with a number in it still finds its translation.
"""
import json
import re
from functools import cache
from pathlib import Path

CATALOG_DIR = Path(__file__).parent / "i18n"
SOURCE = "pt"
LANGS = ("pt", "en", "es")
_FIELD = re.compile(r"\{(\w+)\}")


def lang_of(code: str | None) -> str:
  """openpilot's LanguageSetting (en, pt-BR, es, de, ...) to one of ours. Other languages get English."""
  code = (code or "").lower().removeprefix("main_")  # older comma versions stored "main_pt-BR" (multilang.py)
  if code.startswith("pt"):
    return "pt"
  if code.startswith("es"):
    return "es"
  return "en"


def comma_lang(params) -> str:
  """The comma 4 has no language menu, so its default "en" says nothing about the person: only another value counts."""
  try:
    code = params.get("LanguageSetting")
    code = code.removeprefix("main_") if isinstance(code, str) else code  # older versions stored "main_en" (multilang.py)
    return SOURCE if code in (None, "", "en") else lang_of(code)
  except Exception:
    return SOURCE


@cache
def catalog(lang: str) -> dict[str, str]:
  if lang not in LANGS or lang == SOURCE:
    return {}
  try:
    return json.loads((CATALOG_DIR / f"{lang}.json").read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}


@cache
def _templates(lang: str) -> tuple[tuple[re.Pattern, str], ...]:
  out = []
  for src, dst in catalog(lang).items():
    if _FIELD.search(src):
      parts = _FIELD.split(src)  # text, name, text, name, ...
      pattern = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
      out.append((re.compile(f"^{pattern}$", re.S), dst))
  return tuple(out)


def tr(text: str, lang: str) -> str:
  if not text or lang == SOURCE:
    return text
  table = catalog(lang)
  if text in table:
    return table[text]
  for pattern, dst in _templates(lang):
    m = pattern.match(text)
    if m:
      values = m.groupdict()
      return _FIELD.sub(lambda f, v=values: v.get(f.group(1), f.group(0)), dst)
  return text
