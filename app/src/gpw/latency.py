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


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
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
        }

    checked = [r for r in pasted if "clipboard_restored" in r]
    return {
        "pasted": len(pasted),
        "skipped": sum(1 for r in records if r.get("status") == "skipped"),
        "errors": sum(1 for r in records if r.get("status") == "error"),
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
    head = ("", "all clips", f"{low:g}-{high:g} s clips")
    width = max(len(r[0]) for r in rows)
    lines = [
        f"Dictation latency from {source}",
        f"Pasted: {summary['pasted']}   Skipped (silence): {summary['skipped']}   Errors: {summary['errors']}",
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
    return "\n".join(lines)
