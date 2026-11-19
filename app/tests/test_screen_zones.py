"""Names closest to the cursor first, and mumbled names matched to them (Phase 2b, near-focus).

A fake UI Automation tree shaped like LinkedIn messaging in Chrome: the browser toolbar and tab
strip, LinkedIn's top nav (a banner), the conversation list, the open conversation with its header
and messages, the message box you type in, and a "People you may know" sidebar. No real UI
Automation, no network.
"""

import time

import pytest

from gpw.names import MAX_SCREEN_NAMES, NameFix, extract_names, loose_score
from gpw.screen import NEAR_MIN_CHARS, Node, ScreenReader, assemble_parts, node_zones


def el(*text, children=(), far=False, focus=False):
    return {"text": [t for t in text if t], "children": list(children), "far": far, "focus": focus}


def flatten(tree):
    """Document-order nodes, as UIAWindowReader.window_parts builds them, and the focused index."""
    nodes, focus = [], -1
    stack = [(tree, -1, False)]
    while stack:
        item, parent, far = stack.pop()
        far = far or item["far"]
        index = len(nodes)
        nodes.append(Node(parent, list(item["text"]), far))
        if item["focus"]:
            focus = index
        for child in reversed(item["children"]):
            stack.append((child, index, far))
    return nodes, focus


CONVERSATIONS = [
    "Jordan Whitfield", "Priya Raman", "Tobias Wren", "Alejandra Ruiz", "Desmond Okafor", "Harriet Vance",
    "Kwame Mensah", "Svetlana Orlova", "Rafael Quintero", "Imogen Blackwood", "Thaddeus Grant", "Noor Haddad",
    "Beatriz Salgado", "Callum Fraser", "Yuki Tanaka", "Oluwaseun Adeyemi", "Margaux Lefevre", "Ignatius Cole",
    "Fenella Brooks", "Lorenzo Bianchi", "Saoirse Kavanagh", "Dmitri Volkov", "Ines Carvalho", "Ezekiel Price",
]
PEOPLE_YOU_MAY_KNOW = [
    "Gideon Marsh", "Rosalind Tate", "Barnaby Holt", "Philippa Crane", "Augustin Moreau", "Wilhelmina Shaw",
    "Cornelius Pike", "Ottoline Hart", "Leopold Strand", "Marisol Vega", "Kelly Adams",
]
MESSAGE = "Hi Matt, thanks for connecting. I wanted to share the program roadmap before Thursday. Kelli on my team has the deck."


def linkedin_window():
    conversation_list = el(
        "Conversation List",
        children=[
            el(f"{name}", f"Open conversation with {name}", f"{name} Nov {i + 1} You: sounds good, talk soon")
            for i, name in enumerate(CONVERSATIONS)
        ],
    )
    thread = el(
        children=[
            el("Lemonte Pegues, PMP", "Senior Program Manager at Acme", "Active now"),  # the header
            el(
                "Messages",
                children=[
                    el("Lemonte Pegues sent the following messages at 10:42 AM"),
                    el("View Lemonte’s profile", "Lemonte Pegues, PMP"),
                    el(MESSAGE),
                    el("Best, Lemonte Pegues, PMP"),
                ],
            ),
            el(
                children=[
                    el("Write a message…", focus=True),
                    el("Attach an image to your conversation with Lemonte Pegues"),
                    el("Attach a file to your conversation with Lemonte Pegues"),
                    el("Open Emoji Keyboard"),
                    el("Send"),
                ],
            ),
        ],
    )
    page = el(
        "Messaging | LinkedIn",
        children=[
            el("Global Navigation", "Home", "My Network", "Jobs", "Messaging", "Notifications", "Me",
               "For Business", "Try Premium for $0", far=True),  # banner landmark
            el(children=[conversation_list, thread]),  # main
            el("People you may know", far=True, children=[  # complementary landmark
                el(name, f"Invite {name} to connect", f"{name} Connect") for name in PEOPLE_YOU_MAY_KNOW
            ]),
            el("About Accessibility Help Center Privacy & Terms Ad Choices Advertising", far=True),  # contentinfo
        ],
    )
    return el(
        "Messaging | LinkedIn - Google Chrome",
        children=[
            el("Back", "Forward", "Reload", "Address and search bar", "linkedin.com/messaging/thread/2-abc", far=True),
            el(children=[el(t) for t in ["Feed | LinkedIn", "Messaging | LinkedIn", "Gmail Inbox", "Calendar"]],
               far=True),  # tab strip
            page,
        ],
    )


@pytest.fixture
def linkedin():
    nodes, focus = flatten(linkedin_window())
    parts = assemble_parts(nodes, node_zones(nodes, focus))
    return parts, extract_names(parts)


# -- zones --

def test_the_near_zone_is_the_open_conversation(linkedin):
    parts, found = linkedin
    near = "\n".join(text for zone, text in parts if zone == "near")
    assert "Lemonte Pegues, PMP" in near and MESSAGE in near
    for elsewhere in ["Jordan Whitfield", "Gideon Marsh", "My Network", "Address and search bar"]:
        assert elsewhere not in near
    assert [zone for zone, _ in parts][:2] == ["near", "title"] and parts[-1][0] == "far"


def test_near_names_fill_the_keyword_slots_first(linkedin):
    _, found = linkedin
    assert found.names[0] == "Lemonte Pegues"
    near_names = found.names[: found.zones["near"]]
    assert set(near_names) == {"Lemonte Pegues", "Acme", "Matt", "Kelli"}  # job title and buttons are not names
    assert sum(found.zones.values()) == len(found.names) == MAX_SCREEN_NAMES
    assert {"Lemonte", "Pegues", "Lemonte Pegues", "Kelli"} <= found.near
    assert "Jordan Whitfield" not in found.near and "Gideon Marsh" not in found.near


def test_a_busy_page_no_longer_crowds_out_the_person_you_answer():
    # Before zones: one text for the whole window, most frequent first, 30 slots. Forty names in
    # a list, each shown twice, pushed out a name shown once in the conversation.
    others = [f"{first} {last}" for first in CONVERSATIONS[:8] for last in ["Abbott", "Barlow", "Cardenas", "Dunmore", "Ellery"]]
    listing = "\n".join(f"{name}\nOpen conversation with {name}" for name in others)
    flat = extract_names(f"{listing}\nLemonte Pegues, PMP")
    assert "Lemonte Pegues" not in flat.names and len(flat.names) == MAX_SCREEN_NAMES
    zoned = extract_names([("near", "Lemonte Pegues, PMP"), ("page", listing)])
    assert zoned.names[0] == "Lemonte Pegues" and zoned.zones == {"near": 1, "title": 0, "page": 29, "far": 0}


def test_near_grows_until_it_holds_enough_text():
    #       0 window
    #       1 pane ── 2 list (lots of text), 3 box ── 4 field (focus), 5 button
    nodes = [Node(-1, ["Window"]), Node(0, []), Node(1, ["x" * 500]), Node(1, []), Node(3, ["draft"]), Node(3, ["Send"])]
    zones = node_zones(nodes, focus=4, near_min_chars=NEAR_MIN_CHARS)
    assert [z for z, _ in zones] == ["title", "near", "near", "near", "near", "near"]
    small = node_zones(nodes, focus=4, near_min_chars=6)  # "draft" and "Send" are enough
    assert [z for z, _ in small] == ["title", "page", "page", "near", "near", "near"]


def test_toolbars_inside_the_near_zone_count_as_near_and_no_focus_means_no_near():
    #       0 window ── 1 message box (focus) ── 2 formatting bar;  3 menu;  4 pane
    nodes = [Node(-1, ["Window"]), Node(0, ["draft"]), Node(1, ["Bold Italic"], far=True),
             Node(0, ["File Edit View"], far=True), Node(0, ["Thread"])]
    assert [z for z, _ in node_zones(nodes, focus=1, near_min_chars=1)] == ["title", "near", "near", "far", "page"]
    assert [z for z, _ in node_zones(nodes, focus=-1)] == ["title", "page", "far", "far", "page"]


def test_the_near_zone_never_takes_the_whole_window():
    nodes = [Node(-1, ["Window"]), Node(0, ["Toolbar text"]), Node(0, ["tiny"])]
    assert [z for z, _ in node_zones(nodes, focus=2)] == ["title", "page", "near"]


def test_screen_reader_reports_zones():
    class Zoned:
        app, title = "chrome.exe", "Messaging | LinkedIn - Google Chrome"

        def parts(self):
            nodes, focus = flatten(linkedin_window())
            return assemble_parts(nodes, node_zones(nodes, focus))

    class Reader:
        def foreground(self):
            return Zoned()

    screen = ScreenReader(Reader)
    try:
        result = screen.collect(screen.begin(), time.perf_counter() + 2)
    finally:
        screen.close()
    assert result.status == "ok" and result.names.names[0] == "Lemonte Pegues"
    assert result.names.zones["near"] == 4 and "Lemonte" in result.names.near
    assert MESSAGE not in repr(result) and "roadmap" not in repr(result)


# -- respelling with the near zone --

@pytest.mark.parametrize(
    "heard, wanted",
    [
        ("Lamont, somehow this slipped past me.", "Lemonte, somehow this slipped past me."),  # the real failure
        ("Lemont, somehow this slipped past me.", "Lemonte, somehow this slipped past me."),
        ("Lemon Tay, somehow this slipped past me.", "Lemonte, somehow this slipped past me."),
        ("Thanks Lemon Tay, talk Thursday.", "Thanks Lemonte, talk Thursday."),
        ("I told Mr. Peggs about it.", "I told Mr. Pegues about it."),
        ("I told Mr. Pegus about it.", "I told Mr. Pegues about it."),
        ("Is this Peggy's draft?", "Is this Pegues draft?"),  # one name heard as a possessive
        ("I read Lemont's roadmap.", "I read Lemonte's roadmap."),
        ("Thanks Lemon Tay Pegus, see you then.", "Thanks Lemonte Pegues, see you then."),
        ("Lemontay Pegs, thanks.", "Lemonte Pegues, thanks."),
        ("Ask Kelly for the deck.", "Ask Kelli for the deck."),  # near Kelli beats the sidebar's Kelly Adams
    ],
)
def test_mumbled_names_come_out_as_the_conversation_spells_them(linkedin, heard, wanted):
    _, found = linkedin
    assert NameFix(found.words, near=found.near)(heard) == wanted


@pytest.mark.parametrize(
    "heard",
    [
        "Tell Gwendolyn I said hi.",  # not on screen and not close to anything near
        "Ask Jennifer at Microsoft on Tuesday.",
        "Lemon cake is good.",  # a sentence-start word, not said to anyone
        "Will you send it? Mark the date.",  # common words never change
        "Thanks Sean.",  # Sean is not Shane, near or not (see below)
        "Tell Gideon Marsh hi.",  # spelled as on screen, even if far
        "Tell PEGS hi.",  # all caps: an acronym
        "I like the lemon tay.",  # lowercase: not a name
        "Talk to him about the Delaware filing.",  # a place, not Delmar: a mumble drops sounds, it does not swap them
    ],
)
def test_names_not_near_the_cursor_are_left_alone(linkedin, heard):
    _, found = linkedin
    near = set(found.near) | {"Shane Doyle", "Shane", "Doyle", "Delmar Ruiz"}
    fix = NameFix(found.words, near=near)
    assert fix(heard) == heard and fix.from_near == 0


def test_only_near_names_qualify_for_the_loose_pass():
    # Lemonte only in a sidebar: too loose a fit for the strict pass, so "Lemon Tay" stays.
    heard = "Thanks Lemon Tay, talk soon."
    assert NameFix({"Lemonte", "Pegues"})(heard) == heard
    assert NameFix({"Lemonte", "Pegues"}, near={"Lemonte"})(heard) == "Thanks Lemonte, talk soon."


def test_two_near_names_that_fit_as_well_leave_the_word_alone():
    fix = NameFix({"Delmar", "Delmer"}, near={"Delmar Ruiz", "Delmer Hayes"})
    assert loose_score("Del More", "Delmar") == loose_score("Del More", "Delmer")
    assert fix("Ask Del More.") == "Ask Del More." and fix.ambiguous == 1 and fix.changed == 0
    clear = NameFix({"Delmar"}, near={"Delmar Ruiz"})
    assert clear("Ask Del More.") == "Ask Delmar."


def test_loose_changes_are_counted_and_listed(linkedin):
    _, found = linkedin
    fix = NameFix(found.words, near=found.near)
    fix("Thanks Lemon Tay, ask Pegus.")
    assert (fix.from_near, fix.from_screen, fix.changed) == (1, 1, 2)
    assert ("Lemon Tay", "Lemonte") in fix.changes


def test_loose_score():
    assert loose_score("Lemon Tay", "Lemonte") > 0.7 and loose_score("Lemon Tay", "Lemonte Pegues") is None
    assert loose_score("Jennifer", "Lemonte") is None  # different first sound
    assert loose_score("Pegs", "Pegues") and not loose_score("Pg", "Pegues")
    assert loose_score("Peggy", "Pegues", known=True) is None  # a known first name heard: consonants must match
