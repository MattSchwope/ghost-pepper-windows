"""On-screen name spelling (Phase 2b): name matching, picking names off a screen, respelling. Pure, no UI."""

import pytest

from gpw.name_data import COMMON_WORDS, NAME_VARIANTS, variant_groups
from gpw.names import (
    MAX_SCREEN_NAMES,
    NameFix,
    adds_words,
    extract_names,
    looks_like_name_variant,
    merge_keywords,
    name_key,
    sounds_alike,
)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Sean", "Shawn"), ("Shawn", "Shaun"), ("Kelly", "Kelli"), ("Kelli", "Kellie"),
        ("Catherine", "Katherine"), ("Catherine", "Kathryn"), ("Brian", "Bryan"), ("Jon", "John"),
        ("Steven", "Stephen"), ("Jeff", "Geoff"), ("Philip", "Phillip"), ("Sara", "Sarah"),
        ("Megan", "Meghan"), ("Caitlin", "Katelyn"), ("Zach", "Zack"), ("Marc", "Mark"),
        ("Muhammad", "Mohammed"), ("Isabel", "Isobel"),
        # not in the name list: by sound and spelling
        ("Smith", "Smyth"), ("Johnson", "Jonson"), ("Peterson", "Petersen"), ("Schwope", "Shwope"),
        ("Nguyen", "Nguyan"), ("Okafor", "Okafur"), ("Kavaliro", "Cavaliro"), ("Zoë", "Zoe"),
    ],
)
def test_spellings_of_one_name_sound_alike(a, b):
    assert sounds_alike(a, b) and sounds_alike(b, a)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Sean", "Shane"), ("Shawn", "Shane"), ("Dan", "Don"), ("Jon", "Joan"), ("Tom", "Tim"),
        ("Aaron", "Erin"), ("Ellen", "Allen"), ("Kyle", "Kelly"), ("Kelly", "Kayla"), ("Jan", "Jen"),
        ("Bill", "William"),  # nicknames are other names
        ("Smith", "Smart"), ("Sun", "Sean"), ("Schmidt", "Smith"), ("Patel", "Pratt"),
    ],
)
def test_different_names_do_not(a, b):
    assert not sounds_alike(a, b) and not sounds_alike(b, a)


def test_name_key_merges_what_spellings_swap():
    assert name_key("Catherine") == name_key("Kathryn") == "ktrn"
    assert name_key("Philip") == name_key("Filip") == "flp"
    assert name_key("Shawn") == name_key("Sean") == "sn"
    assert name_key("") == ""


def test_name_list_is_well_formed():
    lines = NAME_VARIANTS.strip().splitlines()
    for line in lines:
        assert line.split(), "blank line in NAME_VARIANTS"
        for name in line.split():
            assert name[0].isupper(), name
    groups = variant_groups()
    assert groups["kelli"] == groups["kelly"]
    assert not groups["sean"] & groups["shane"]
    assert "kelli" not in COMMON_WORDS and "mark" in COMMON_WORDS


# -- picking names off a screen --

OUTLOOK = """\
Inbox - kelli.smith@acme.com - Outlook
Reply  Reply All  Forward
From: Kelli Smith <kelli.smith@acme.com>
To: Shawn O'Brien; Mark Jones
Subject: Quarterly Review with Kavaliro
Hi Matt,
Thanks for the update. Shawn will send the deck. Unfortunately the room moved.
Kelli
"""


def test_extract_names_from_an_email():
    found = extract_names(OUTLOOK)
    assert found.names[:3] == ["Kelli Smith", "Shawn O'Brien", "Mark Jones"]
    assert {"Kavaliro", "Matt"} <= set(found.names)
    for not_a_name in ["Inbox", "Reply", "Reply All", "Forward", "From", "Subject", "Quarterly", "Review",
                       "Thanks", "Unfortunately", "Hi Matt", "Outlook"]:
        assert not_a_name not in found.names
    assert {"Kelli", "Smith", "Shawn", "O'Brien", "Jones", "Kavaliro"} <= found.words


def test_extract_names_ranks_by_how_often_a_name_shows():
    text = "Notes for Priya. Call Kelli today. Kelli said yes. Ask Kelli again."
    assert extract_names(text).names[0] == "Kelli"


def test_extract_names_skips_words_also_seen_in_lowercase_and_sentence_starts():
    text = "Thread\nUpdates are here. The Updates page has updates. Zebulon Pike went west.\nPlease ask Dana."
    found = extract_names(text)
    assert "Updates" not in found.names and "Thread" not in found.names and "Please" not in found.names
    assert "Zebulon Pike" in found.names and "Dana" in found.names


def test_extract_names_handles_possessives_and_caps_the_count():
    found = extract_names("Send it to Kelli's team and O'Brien's desk.")
    assert set(found.names) == {"Kelli", "O'Brien"}
    many = " ".join(f"met Zork{chr(97 + i)}{chr(97 + j)}." for i in range(8) for j in range(8))
    assert len(extract_names(many).names) == MAX_SCREEN_NAMES
    assert extract_names("").names == []


def test_merge_keywords_puts_screen_names_first_within_the_limit():
    assert merge_keywords(["Kelli Smith", "SOCOM"], ["Kavaliro", "socom", "FSP"], 3) == ["Kelli Smith", "SOCOM", "Kavaliro"]


# -- respelling --

def test_one_spelling_on_screen_wins():
    fix = NameFix({"Shawn", "Kelli", "Smith"})
    assert fix("Tell Sean and Kelly Smyth that Kelly's draft is in.") == "Tell Shawn and Kelli Smith that Kelli's draft is in."
    assert fix.from_screen == 4 and fix.ambiguous == 0


def test_two_spellings_on_screen_leave_the_word_alone():
    # Done-when: "With two spellings of the same name on screen, the transcribed word is left alone."
    fix = NameFix({"Kelly", "Kelli"}, prefer={"kellie": "Kelli"})
    assert fix("Ask Kellie and Kelly about it.") == "Ask Kellie and Kelly about it."
    assert fix.ambiguous == 1 and fix.changed == 0


def test_prefer_applies_only_when_the_screen_has_no_spelling():
    prefer = {"kelly": "Kelli", "catherine": "Katherine"}
    assert NameFix(set(), prefer)("Kelly met Catherine.") == "Kelli met Katherine."
    on_screen = NameFix({"Kelly"}, prefer)
    assert on_screen("Ask Kelly.") == "Ask Kelly." and on_screen.from_prefer == 0


def test_only_capitalized_words_change_and_sentence_start_words_stay():
    fix = NameFix({"Marc", "Kelli"}, prefer={"will": "Wil"})
    text = "Mark the date. Will you ask Mark? kelly green, mark it. Then Will can go."
    assert fix(text) == "Mark the date. Will you ask Marc? kelly green, mark it. Then Wil can go."


def test_acronyms_and_exact_matches_are_untouched():
    fix = NameFix({"Sean", "SOCOM"})
    assert fix("SEAN and SOCOM and Sean.") == "SEAN and SOCOM and Sean."
    assert fix.changed == 0


# -- dictionary checks --

@pytest.mark.parametrize(
    "wrong, right, variant",
    [
        ("kelly", "Kelli", True), ("catherine", "Katherine", True), ("sean", "Shawn", True),
        ("cavaliro", "Kavaliro", False),  # a mishearing of a company name stays in replace:
        ("ts sci", "TS/SCI", False), ("bill", "Bill", False), ("dan", "Don", False),
    ],
)
def test_looks_like_name_variant(wrong, right, variant):
    assert looks_like_name_variant(wrong, right) is variant


def test_adds_words():
    assert adds_words("clearance", "clearance TS/SCI")
    assert adds_words("poly", "CI Poly")
    assert not adds_words("fsp", "Full Scope Polygraph") and not adds_words("ts sci", "TS/SCI")


# -- offline stand-in for the 95% done-when target --
# Window text shaped like each app, and how GPT-Transcribe might spell the name it heard. The real
# check runs in the apps themselves (see BUILD_PLAN.md); this one guards the matching and picking.

SCREENS = {
    "outlook": "Inbox - Outlook\nFrom: Shawn Mulcahy <smulcahy@contoso.com>\nTo: Matt\nSubject: Offer letter\n"
               "Hi Matt,\nThe offer for Kelli Nguyen is attached. Catherine signed off.\nThanks,\nShawn",
    "gmail": "Gmail\nCompose\nInbox 3\nKathryn Oyelaran\nto me\nCan you loop in Jon Pettersen? Thanks!\n"
             "Reply Forward",
    "linkedin": "LinkedIn\nMessaging\nGeoff Mikkelsen\nSenior Recruiter at Acme\n"
                "Geoff Mikkelsen sent the following message\nHi Matt, Aiden Schwope recommended you.",
    "teams": "Microsoft Teams\nChat\nBryan Okafor\nBryan Okafor 10:42 AM\nAre we still on with Zack?\n"
             "Type a message",
    "slack": "Slack\n# recruiting\nMegan Holloway  9:12 AM\nAsk Isobel Pryce about the req.\n"
             "Message #recruiting",
}

DICTATIONS = [
    ("outlook", "Thanks Sean, I will send Kelly the letter.", "Thanks Shawn, I will send Kelli the letter."),
    ("outlook", "Katherine already approved it.", "Catherine already approved it."),
    ("outlook", "Copy Kelly Nguyen on the reply.", "Copy Kelli Nguyen on the reply."),
    ("outlook", "Tell Shaun Mulcahey yes.", "Tell Shawn Mulcahy yes."),
    ("gmail", "Hi Catherine, sure, I'll loop in John Petersen.", "Hi Kathryn, sure, I'll loop in Jon Pettersen."),
    ("gmail", "Kathryn, thanks for this.", "Kathryn, thanks for this."),
    ("linkedin", "Hi Jeff, thanks for reaching out.", "Hi Geoff, thanks for reaching out."),
    ("linkedin", "Aidan Schwoppe is a friend.", "Aiden Schwope is a friend."),
    ("linkedin", "Jeff Mickelson, happy to talk.", "Geoff Mikkelsen, happy to talk."),
    ("teams", "Brian, yes, we are on with Zach.", "Bryan, yes, we are on with Zack."),
    ("teams", "Thanks Brian Okafur.", "Thanks Bryan Okafor."),
    ("slack", "Meghan, I asked Isabel Price.", "Megan, I asked Isobel Pryce."),
    ("slack", "Sounds good Megan.", "Sounds good Megan."),
]


def test_eval_one_spelling_on_screen_is_pasted():
    right = 0
    for app, heard, wanted in DICTATIONS:
        fix = NameFix(extract_names(SCREENS[app]).words)
        got = fix(heard)
        right += got == wanted
        assert got == wanted, (app, heard, got)
    assert right / len(DICTATIONS) >= 0.95


def test_eval_two_spellings_on_screen_leave_the_word_alone():
    screen = "To: Kelly Adams; Kelli Brown\nSubject: Shawn and Sean"
    words = extract_names(screen).words
    for heard in ["Ask Kellie about it.", "Ask Kelly about it.", "Ask Shaun about it."]:
        assert NameFix(words)(heard) == heard
