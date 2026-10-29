import os

import pytest
import yaml

from gpw.dictionary import (
    TEMPLATE,
    Dictionary,
    DictionaryStore,
    apply_replacements,
    insert_replacement,
    insert_term,
    keywords_for,
    parse_dictionary,
    validate_term,
)


def test_parse_dictionary():
    d = parse_dictionary({"terms": ["Kavaliro", " ", None, "TS/SCI"], "replace": {"cavaliro": "Kavaliro"}})
    assert d.terms == ["Kavaliro", "TS/SCI"] and d.replace == {"cavaliro": "Kavaliro"}
    assert parse_dictionary(None) == Dictionary()
    assert parse_dictionary({"terms": "SOCOM"}).terms == ["SOCOM"]


@pytest.mark.parametrize("raw", [["a"], {"terms": {"a": 1}}, {"replace": ["a"]}, {"replace": {"a": None}}])
def test_parse_dictionary_rejects_bad_shapes(raw):
    with pytest.raises(ValueError):
        parse_dictionary(raw)


def test_template_parses_to_the_examples():
    d = parse_dictionary(yaml.safe_load(TEMPLATE))
    assert d.terms[:2] == ["Kavaliro", "TS/SCI"] and d.replace["ts sci"] == "TS/SCI"


def test_keywords_put_dictionary_first_drop_duplicates_and_cap():
    d = Dictionary(terms=["Kavaliro", "SOCOM", "<bad>", "kavaliro"])
    assert keywords_for(d, ["SOCOM", "FSP"], 10) == ["Kavaliro", "SOCOM", "bad", "FSP"]
    assert keywords_for(d, ["FSP"], 2) == ["Kavaliro", "SOCOM"]


def test_replacements_ignore_case_and_only_hit_whole_words():
    fixes = {"cavaliro": "Kavaliro", "ts sci": "TS/SCI", "ts": "T.S."}
    assert apply_replacements("Cavaliro needs a TS  SCI, not ts.", fixes) == "Kavaliro needs a TS/SCI, not T.S.."
    assert apply_replacements("contests science; cavalirO's team", fixes) == "contests science; Kavaliro's team"
    assert apply_replacements("nothing to fix", {}) == "nothing to fix"


def test_replacement_keys_with_punctuation_are_literal():
    assert apply_replacements("ask c.i. poly about it", {"c.i. poly": "CI Poly"}) == "ask CI Poly about it"
    assert apply_replacements("cxi poly", {"c.i. poly": "CI Poly"}) == "cxi poly"


def test_validate_term():
    assert validate_term("  CI \n Poly ") == "CI Poly"
    for bad in ["", "   ", "x" * 61, "<b>"]:
        with pytest.raises(ValueError):
            validate_term(bad)


BLOCK = """\
# my notes
terms:   # most used first
  - Kavaliro   # the company
  # - Old
replace:
  "cavaliro": "Kavaliro"

# trailing comment
"""


def test_insert_into_block_lists_keeps_comments():
    text = insert_replacement(insert_term(BLOCK, "SOCOM"), "so com", "SOCOM")
    assert "# the company" in text and "# - Old" in text and "# trailing comment" in text
    d = parse_dictionary(yaml.safe_load(text))
    assert d.terms == ["Kavaliro", "SOCOM"]
    assert d.replace == {"cavaliro": "Kavaliro", "so com": "SOCOM"}
    assert text.index("SOCOM") < text.index("replace:")


def test_insert_converts_flow_style_and_adds_missing_keys():
    text = insert_term("terms: [Kavaliro, 'TS/SCI']  # hints\nreplace: {}\n", 'Zoë "Z"')
    text = insert_replacement(text, "zoe", "Zoë")
    d = parse_dictionary(yaml.safe_load(text))
    assert d.terms == ["Kavaliro", "TS/SCI", 'Zoë "Z"'] and d.replace == {"zoe": "Zoë"}
    assert "# hints" in text
    d = parse_dictionary(yaml.safe_load(insert_replacement(insert_term("# empty\n", "FSP"), "f s p", "FSP")))
    assert d.terms == ["FSP"] and d.replace == {"f s p": "FSP"}


def touch_later(path):
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))


def test_store_creates_reloads_and_keeps_the_last_good_copy(tmp_path):
    path = tmp_path / "data" / "dictionary.yaml"
    store = DictionaryStore(path, ["FSP"], max_keywords=100)
    assert store.current() == Dictionary() and store.keywords() == ["FSP"]
    assert store.ensure_file() is True and store.ensure_file() is False
    assert store.current().terms[0] == "Kavaliro" and "FSP" in store.keywords()

    path.write_text("terms: [Acme]\n", encoding="utf-8")
    touch_later(path)
    assert store.current().terms == ["Acme"]

    path.write_text("terms: [Acme\n", encoding="utf-8")  # typo mid-edit
    touch_later(path)
    assert store.current().terms == ["Acme"] and store.error
    with pytest.raises(ValueError, match="has an error"):
        store.add_term("Other")


def test_store_add_term_and_fix(tmp_path):
    path = tmp_path / "dictionary.yaml"
    path.write_text(BLOCK, encoding="utf-8")
    store = DictionaryStore(path)
    assert store.add_term("SOCOM") is True
    assert store.add_term("socom") is False  # already there, any case
    assert store.add_replacement("So Com", "SOCOM") is True
    assert store.add_replacement("so com", "SOCOM") is False
    assert store.add_replacement("so com", "SoCom") is True  # an update wins
    d = store.current()
    assert d.terms == ["Kavaliro", "SOCOM"] and d.replace["so com"] == "SoCom"
    text = path.read_text(encoding="utf-8")
    assert "# the company" in text and text.count('"so com"') == 1
