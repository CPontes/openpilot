import pytest

from openpilot.selfdrive.hexa import marks


def test_default_reasons_have_unique_stable_keys():
  keys = [k for k, _ in marks.DEFAULT_REASONS]
  assert len(keys) == len(set(keys))
  assert all(r["custom"] is False for r in marks.reasons(None))
  assert marks.reason_label(None, "balancou") == "balançou sem motivo"
  assert marks.reason_label(None, "estrada") == "balançou: estrada ruim"
  assert marks.reason_label(None, "nope") is None


def test_old_mark_keeps_its_label():
  old = {"id": "a", "reason": "balancou", "label": "balançou"}
  assert marks.public(old)["label"] == "balançou"


def test_add_custom_reason():
  custom, r = marks.add_reason([], "  Buraco  na pista ")
  assert r == {"key": "c-buraco-na-pista", "label": "Buraco na pista"}
  assert marks.reasons(custom)[-1] == {"key": "c-buraco-na-pista", "label": "Buraco na pista", "custom": True}
  # same slug, different label: a new unique key
  custom, r2 = marks.add_reason(custom, "Buraco na pista!")
  assert r2["key"] == "c-buraco-na-pista-2"


@pytest.mark.parametrize("label", ["", "   ", "BALANÇOU SEM MOTIVO", "curva difícil"])
def test_add_reason_rejects_empty_and_duplicates(label):
  with pytest.raises(ValueError):
    marks.add_reason([], label)


def test_custom_reason_limit_and_removal():
  custom = []
  for i in range(marks.MAX_CUSTOM_REASONS):
    custom, _ = marks.add_reason(custom, f"motivo {i}")
  with pytest.raises(ValueError):
    marks.add_reason(custom, "mais um")
  custom = marks.remove_reason(custom, "c-motivo-0")
  assert len(custom) == marks.MAX_CUSTOM_REASONS - 1
  assert marks.remove_reason(custom, "balancou") == custom  # defaults can't be removed


def test_label_and_note_are_bounded():
  _, r = marks.add_reason([], "x" * 100)
  assert len(r["label"]) == marks.MAX_LABEL
  assert len(marks.clean_note("y" * 500)) == marks.MAX_NOTE
  assert marks.clean_note("  a \n b  ") == "a b"


def test_phone_mark_adopts_the_bookmark_it_asked_for():
  m = marks.new_mark("celular", reason="curva", label="curva difícil", now=100.)
  m["_req_mono_ns"] = 1_000_000_000
  lst = marks.append([], m)
  lst, got, created = marks.attach_bookmark(lst, 1_300_000_000)
  assert not created and got["id"] == m["id"] and got["mono_ns"] == 1_300_000_000
  assert "_req_mono_ns" not in got and len(lst) == 1


def test_bookmark_without_phone_mark_is_pending():
  m = marks.new_mark("celular", reason="curva", label="curva difícil")
  m["_req_mono_ns"] = 1_000_000_000
  lst = marks.append([], m)
  # too late for the phone mark: a separate mark without reason
  lst, got, created = marks.attach_bookmark(lst, 1_000_000_000 + marks.ATTACH_WINDOW_NS + 1)
  assert created and got["source"] == "comma" and got["reason"] is None and len(lst) == 2
  # a bookmark can't adopt a phone mark asked for after it
  lst2, got2, created2 = marks.attach_bookmark([dict(m)], 900_000_000)
  assert created2


def test_classify_and_cap():
  lst = []
  for i in range(marks.MAX_MARKS + 5):
    lst = marks.append(lst, marks.new_mark("comma", mono_ns=i))
  assert len(lst) == marks.MAX_MARKS
  lst, m = marks.set_reason(lst, lst[-1]["id"], "freada", "freada estranha", "na lombada")
  assert (m["reason"], m["label"], m["note"]) == ("freada", "freada estranha", "na lombada")
  with pytest.raises(KeyError):
    marks.set_reason(lst, "nope", "freada", "freada estranha")


def test_public_and_log_fields_drop_private_keys():
  m = marks.new_mark("celular", reason="outro", label="outro")
  m["_req_mono_ns"] = 5
  assert "_req_mono_ns" not in marks.public(m)
  assert set(marks.log_fields(m)) == {"id", "t", "mono_ns", "source", "who", "reason", "label", "note", "delay_s"}
