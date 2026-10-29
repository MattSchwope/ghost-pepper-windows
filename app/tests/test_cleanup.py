import pytest

from gpw.cleanup import apply_spoken_commands, clean_dictation, remove_fillers, words_kept


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("So, um, I think we should go.", "So, I think we should go."),
        ("Um, so we go now.", "So we go now."),
        ("Okay. Uh, we go now.", "Okay. We go now."),
        ("Let's review the uh meeting notes.", "Let's review the meeting notes."),
        ("We need more, uh.", "We need more."),
        ("We need more uh.", "We need more."),
        ("Umm... I'm not sure, erm, yet.", "I'm not sure, yet."),
        ("Um, uh, so yes.", "So yes."),
        ("Uh.", ""),
    ],
)
def test_remove_fillers(raw, clean):
    assert remove_fillers(raw) == clean


@pytest.mark.parametrize(
    "text",
    [
        "Take him to the ER at UM Health.",  # er and UM are words here
        "Bring an umbrella and a drum.",
        "Uh-huh, that works.",
        "Ahem, hmm, okay.",
    ],
)
def test_fillers_leave_real_words_alone(text):
    assert remove_fillers(text) == text


@pytest.mark.parametrize(
    "raw, out",
    [
        ("Dear John, new paragraph, thanks for the note.", "Dear John,\n\nThanks for the note."),
        ("Thanks for the help. New paragraph. Next week works.", "Thanks for the help.\n\nNext week works."),
        ("Groceries: new line eggs new line milk", "Groceries:\neggs\nmilk"),
        ("First item. New line. Second item.", "First item.\nSecond item."),
        ("End with a break new line", "End with a break\n"),
        ("Add a newline here", "Add a newline here"),
    ],
)
def test_spoken_commands(raw, out):
    assert apply_spoken_commands(raw) == out


@pytest.mark.parametrize(
    "text",
    [
        "We need a new line of credit.",
        "Draw a new line under the total.",
        "The new line manager starts Monday.",
        "Our new paragraph style is fine.",
        "Renew line items and new lines.",
    ],
)
def test_spoken_commands_leave_the_words_alone_in_normal_speech(text):
    assert apply_spoken_commands(text) == text


@pytest.mark.parametrize(
    "source, cleaned, kept",
    [
        ("so i think we should go", "So I think we should go.", True),
        ("um so the the plan is ready", "So the plan is ready.", True),
        ("i want i want to send it today", "I want to send it today.", True),
        ("send it today", "Send it tomorrow.", False),  # changed a word
        ("send it today", "Please send it today.", False),  # added a word
        ("today send it", "Send it today.", False),  # reordered
        ("do not send it", "Do send it.", False),  # dropped a negation
        ("we don't send it", "We send it.", False),
        ("one two three four five six seven eight", "One.", False),  # too much dropped
        ("we should we need to go", "We need to go.", True),  # restarted with the same opening word
        ("please send 500 dollars today", "Please send dollars today.", False),  # never drop a number
        ("please send the dollars today", "Please send dollars today.", False),  # not a false start
        ("i think we should let's go", "Let's go.", False),  # no repeated opening: can't tell
    ],
)
def test_words_kept(source, cleaned, kept):
    assert words_kept(source, cleaned) is kept


def test_clean_dictation_order_and_raw_ai_result():
    fixes = {"cavaliro": "Kavaliro"}
    r = clean_dictation("um, cavaliro called. new paragraph thanks", fixes)
    assert r.text == "Kavaliro called.\n\nThanks" and r.ai_status == "off" and r.ai_seconds is None

    r = clean_dictation("so cavaliro called socom", fixes, terms=["SOCOM"], ai=lambda t: "So kavaliro called Socom.")
    assert r.text == "So Kavaliro called SOCOM." and r.ai_status == "used" and r.ai_seconds >= 0

    r = clean_dictation("so cavaliro called", fixes, ai=lambda t: "Cavaliro phoned.")
    assert r.text == "so Kavaliro called" and r.ai_status == "rejected"

    def broken(text):
        raise RuntimeError("down")

    assert clean_dictation("so it goes", {}, ai=broken).ai_status == "failed"
    assert clean_dictation("so it goes", {}, ai=lambda t: t).ai_status == "unchanged"


def test_rules_can_be_turned_off():
    r = clean_dictation("um new line yes", {}, fillers=False, commands=False)
    assert r.text == "um new line yes"


DICTATIONS = [
    "Um, hi Dana, new paragraph, I wanted to follow up on the SOCOM role. Uh, can we talk Thursday?",
    "So the candidate has a TS/SCI, um, and a CI Poly. New line. She can start in two weeks.",
    "Uh, I think, uh, we should, um, move the meeting to 3 p.m.",
    "Groceries: new line eggs new line milk newline bread",
    "Erm, the, uh, the report is attached. New paragraph. Thanks, Matt",
    "Don't, um, send it until I say so.",
    "Hmm, okay, the UM Health account is, uh, ready.",
]


def _content_words(text):
    """Words of a dictation minus fillers and the "new line" / "new paragraph" commands."""
    import re

    from gpw.cleanup import FILLER

    words = [w.casefold() for w in re.findall(r"[\w'’]+", text) if not re.fullmatch(FILLER, w)]  # "UM" is no filler
    out, i = [], 0
    while i < len(words):
        if words[i] == "new" and i + 1 < len(words) and words[i + 1] in ("line", "paragraph"):
            i += 2
        elif words[i] == "newline":
            i += 1
        else:
            out.append(words[i])
            i += 1
    return out


@pytest.mark.parametrize("raw", DICTATIONS)
def test_light_cleanup_changes_no_content_words(raw):
    """The Phase 2 word-diff check: the rules only drop fillers and turn spoken commands into breaks."""
    from gpw.cleanup import _words

    out = clean_dictation(raw, {}).text
    assert _words(out) == _content_words(raw)
