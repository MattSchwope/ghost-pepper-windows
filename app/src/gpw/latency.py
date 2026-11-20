"""Release-to-paste report from the dictation timing log (logs/dictation-latency.jsonl).

Each line is one dictation with stage timings in seconds. No transcript text is stored there.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TARGET_SECONDS = 1.5
TARGET_COUNT = 50
TEN_SECOND_CLIPS = (7.0, 13.0)  # "a 10-second clip", with room for how long a key is really held
SNIPPET_TARGET_SECONDS = 0.3  # from the transcript to the paste; OpenAI still has to hear "insert <name>"
SCREEN_TARGET_SECONDS = 0.1  # Phase 2b: screen names on vs off
SCREEN_WAIT_CAP = 0.08  # service.SCREEN_DEADLINE_SECONDS, measured from the key release


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile, q in [0, 100]."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _median_of(records: list[dict[str, Any]], key: str) -> float | None:
    return percentile([float(r[key]) for r in records if isinstance(r.get(key), (int, float))], 50)


def is_dry_run(record: dict[str, Any]) -> bool:
    # Older records carry no model; a real OpenAI round trip never takes 0 ms, so api == 0 marks a dry run.
    return record.get("model") == "dry-run" or record.get("api") == 0


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    dry_runs = sum(1 for r in records if is_dry_run(r))
    records = [r for r in records if not is_dry_run(r)]
    pasted = [r for r in records if r.get("status") == "pasted" and isinstance(r.get("release_to_paste"), (int, float))]
    low, high = TEN_SECOND_CLIPS
    ten = [r for r in pasted if low <= float(r.get("audio", 0)) <= high]

    def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        r2p = [float(r["release_to_paste"]) for r in rows]
        return {
            "count": len(rows),
            "median": percentile(r2p, 50),
            "p90": percentile(r2p, 90),
            "api": _median_of(rows, "api"),
            "server": _median_of(rows, "server"),
            "prep": _median_of(rows, "prep"),
            "record_end": _median_of(rows, "record_end"),
            "paste": _median_of(rows, "paste"),
            "cleanup": _median_of(rows, "cleanup"),
        }

    spoken = [r for r in pasted if not r.get("snippet")]
    screen_on = [r for r in spoken if r.get("screen") not in (None, "off")]
    screen_off = [r for r in spoken if r.get("screen") in (None, "off")]
    checked = [r for r in pasted if "clipboard_restored" in r]
    local = [float(r["transcript_to_paste"]) for r in pasted if isinstance(r.get("transcript_to_paste"), (int, float))]
    return {
        "snippets": {
            "count": len(local),
            "median": percentile(local, 50),
            "slowest": max(local, default=None),
            "release_to_paste": _median_of([r for r in pasted if "transcript_to_paste" in r], "release_to_paste"),
        },
        "screen": {
            "on": len(screen_on),
            "off": len(screen_off),
            "on_median": _median_of(screen_on, "release_to_paste"),
            "off_median": _median_of(screen_off, "release_to_paste"),
            "wait": _median_of(screen_on, "screen_wait"),
            "slowest_wait": max((float(r["screen_wait"]) for r in screen_on if isinstance(r.get("screen_wait"), (int, float))), default=None),
            "late": sum(1 for r in screen_on if r.get("screen") == "late"),
            "fixed": sum(1 for r in screen_on if r.get("names_fixed")),
        },
        "pasted": len(pasted),
        "skipped": sum(1 for r in records if r.get("status") == "skipped"),
        "errors": sum(1 for r in records if r.get("status") == "error"),
        "dry_runs": dry_runs,
        "all": stats(pasted),
        "ten": stats(ten),
        "clipboard_checked": len(checked),
        "clipboard_restored": sum(1 for r in checked if r.get("clipboard_restored")),
    }


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}s"


def format_report(summary: dict[str, Any], source: Path) -> str:
    low, high = TEN_SECOND_CLIPS
    a, t = summary["all"], summary["ten"]
    rows = [
        ("Dictations", str(a["count"]), str(t["count"])),
        ("Median release-to-paste", _fmt(a["median"]), _fmt(t["median"])),
        ("90th percentile", _fmt(a["p90"]), _fmt(t["p90"])),
        ("Median stop recording", _fmt(a["record_end"]), _fmt(t["record_end"])),
        ("Median clip prep", _fmt(a["prep"]), _fmt(t["prep"])),
        ("Median OpenAI round trip", _fmt(a["api"]), _fmt(t["api"])),
        ("  of which OpenAI processing", _fmt(a["server"]), _fmt(t["server"])),
        ("Median paste", _fmt(a["paste"]), _fmt(t["paste"])),
    ]
    if a["cleanup"] is not None:
        rows.insert(-1, ("Median text-model cleanup", _fmt(a["cleanup"]), _fmt(t["cleanup"])))
    head = ("", "all clips", f"{low:g}-{high:g} s clips")
    width = max(len(r[0]) for r in rows)
    lines = [
        f"Dictation latency from {source}",
        f"Pasted: {summary['pasted']}   Skipped (silence): {summary['skipped']}   Errors: {summary['errors']}"
        + (f"   Dry runs (not counted): {summary['dry_runs']}" if summary["dry_runs"] else ""),
        "",
        f"{head[0]:<{width}}  {head[1]:>10}  {head[2]:>12}",
    ]
    lines += [f"{name:<{width}}  {x:>10}  {y:>12}" for name, x, y in rows]
    if summary["clipboard_checked"]:
        lines.append(f"\nClipboard restored after paste: {summary['clipboard_restored']} of {summary['clipboard_checked']}")

    lines.append(
        f"\nPhase 1 target: median release-to-paste <= {TARGET_SECONDS:g} s for ~10 s clips "
        f"({low:g}-{high:g} s), over {TARGET_COUNT} dictations."
    )
    if t["median"] is None:
        lines.append("Result: no ~10 s dictations logged yet.")
    else:
        met = "met" if t["median"] <= TARGET_SECONDS else "missed"
        enough = "" if t["count"] >= TARGET_COUNT else f" (only {t['count']} so far; need {TARGET_COUNT})"
        lines.append(f"Result: {t['median']:.2f} s -> {met}{enough}.")

    s = summary["snippets"]
    if s["count"]:
        met = "met" if s["median"] < SNIPPET_TARGET_SECONDS else "missed"
        lines += [
            f"\nPhase 2 target: snippets paste in under {SNIPPET_TARGET_SECONDS:g} s, from the transcript to the paste.",
            f"Result: median {s['median']:.3f} s, slowest {s['slowest']:.3f} s, over {s['count']} -> {met}.",
            f"Release to paste for those: median {_fmt(s['release_to_paste'])}, which includes OpenAI hearing the phrase.",
        ]

    sc = summary.get("screen") or {}
    if sc.get("on"):
        lines += [
            f"\nPhase 2b target: median release-to-paste with screen_names on within {SCREEN_TARGET_SECONDS:g} s "
            f"of off, over {TARGET_COUNT} dictations.",
            f"Screen names on: median {_fmt(sc['on_median'])} over {sc['on']}   "
            f"off: median {_fmt(sc['off_median'])} over {sc['off']}",
            f"Time spent waiting for the screen at release: median {sc['wait']:.3f} s, slowest {sc['slowest_wait']:.3f} s"
            f" (capped at {SCREEN_WAIT_CAP:g} s). Late reads: {sc['late']}. Dictations with a name respelled: {sc['fixed']}."
            if sc.get("wait") is not None else "No screen wait times logged yet.",
        ]
        if sc["on_median"] is not None and sc["off_median"] is not None:
            gap = sc["on_median"] - sc["off_median"]
            met = "met" if gap <= SCREEN_TARGET_SECONDS else "missed"
            short = [] if min(sc["on"], sc["off"]) >= TARGET_COUNT else [f"need {TARGET_COUNT} of each"]
            lines.append(f"Result: on minus off = {gap:+.2f} s -> {met}" + (f" ({short[0]})." if short else "."))
        else:
            lines.append("Result: needs dictations with screen_names both on and off to compare.")
    return "\n".join(lines)
