#!/usr/bin/env python3
"""Transcript and timeline tooling for the call-analysis skill.

  merge    <transcript file> --work DIR [--ours "Name,Name,…"]
           Take the provider's transcript (Granola JSON with id/title/created_at/
           transcript; or generic JSON {title, created_at, utterances:[{speaker,
           text}]}; or a `Speaker: text` text file), merge consecutive
           same-speaker utterances, map "Microphone" to the user (config: user_name), number the
           turns and strip everything before the first customer turn.
           Writes DIR/turns.json.

  anchor   <anchors.json> --work DIR --out FOLDER --t0 ISO [--title …]
           anchors.json is written by the Opus anchoring pass: either
           {"<turn>": "HH:MM:SS", …} or [{"turn": n, "bst": "HH:MM:SS",
           "evidence": "…"}, …]. Every other turn is interpolated by character
           count between its neighbouring anchors. Writes DIR/turns-timed.json
           and FOLDER/transcript.md (⚓ on anchors).

  timeline --work DIR --out FOLDER [--screen DIR/screen.json]
           One row per turn: local time · timer · who · said · the user's note typed at
           that moment · Screen (only when the shared screen changed) · Frame.
           Notes come from DIR/notes-events.json (frames.py index), Screen from
           the Opus timeline pass's screen.json, Frame numbers from
           DIR/frame-map.json when frames.py keep has run. Re-run after keep.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Settings: config.json next to this script if the skill has written one;
# anything missing is worked out from the machine (see SKILL.md, "Settings").
CFG_PATH = Path(__file__).resolve().parent / "config.json"
CFG = json.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {}


def _familiar_root() -> str:
    """Familiar's own settings say where its data lives."""
    try:
        ctx = json.loads((Path.home() / ".familiar" / "settings.json").read_text())["contextFolderPath"]
        return str(Path(ctx) / "familiar")
    except Exception:
        return str(Path.home() / "familiar")


def _local_offset_hours() -> int:
    import time
    return round(time.localtime().tm_gmtoff / 3600)


def _tz_label() -> str:
    import time
    return time.strftime("%Z")
USER = CFG.get("user_name", "User")
OURS = ",".join(CFG.get("our_side", [USER]))
TZ_LABEL = CFG.get("tz_label") or _tz_label()
LOCAL_OFFSET = CFG.get("local_utc_offset_hours", _local_offset_hours())

LABEL_RE = re.compile(r"^(?P<label>[^:\n]{1,80}?):\s?(?P<text>.*)$", re.S)
SYS_RE = re.compile(r"^System audio(?: \((?P<name>[^)]+)\))?$")

# ---------------------------------------------------------------------------


def secs(s: str) -> int:
    h, m, sec = (int(x) for x in s.split(":"))
    return h * 3600 + m * 60 + sec


def hms(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"


def fmt_timer(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def parse_t0_local(s: str, offset_h: int) -> tuple[str, float]:
    """Granola created_at (UTC) -> (date, seconds-of-day) in local time."""
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    loc = dt.astimezone(timezone(timedelta(hours=offset_h)))
    return loc.strftime("%Y-%m-%d"), loc.hour * 3600 + loc.minute * 60 + loc.second + loc.microsecond / 1e6


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def speaker_of(label: str, user: str) -> str:
    label = label.strip()
    if label == "Microphone":
        return user
    m = SYS_RE.match(label)
    if m:
        return m.group("name") or "System audio"
    return label


def cmd_merge(a: argparse.Namespace) -> None:
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    raw = Path(a.transcript).read_text()
    utterances: list[tuple[str, str]] = []
    start = raw.find("{")
    if start >= 0 and raw.lstrip().startswith(("{", "The content below")):
        # JSON: Granola's {id, title, created_at, transcript: "Label: text\n\n…"} or the
        # generic {title, created_at, utterances: [{speaker, text}, …]} any provider can emit.
        doc = json.loads(raw[start:])
        if "utterances" in doc:
            for u in doc["utterances"]:
                utterances.append((speaker_of(str(u["speaker"]), a.user), str(u["text"]).strip()))
        text = doc.get("transcript", "") if "utterances" not in doc else ""
    else:
        # Plain text: `Speaker: text` blocks separated by blank lines (a .txt export).
        doc = {"id": None, "title": Path(a.transcript).stem, "created_at": None}
        text = raw
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        m = LABEL_RE.match(block)
        if not m:
            if utterances:
                utterances[-1] = (utterances[-1][0], utterances[-1][1] + " " + block)
            continue
        utterances.append((speaker_of(m.group("label"), a.user), m.group("text").strip()))
    if not utterances:
        sys.exit("no utterances found: expected Granola JSON, generic {utterances:[…]} JSON, or `Speaker: text` blocks")

    merged: list[dict] = []
    for spk, t in utterances:
        if merged and merged[-1]["speaker"] == spk:
            merged[-1]["text"] += " " + t
        else:
            merged.append({"speaker": spk, "text": t})

    ours = {n.strip().lower() for n in a.ours.split(",") if n.strip()}
    ours.add(a.user.lower())
    first_customer = next((i for i, t in enumerate(merged) if t["speaker"].lower() not in ours and t["speaker"] != "System audio"), 0)
    stripped = merged[:first_customer]
    kept = merged[first_customer:]
    for n, t in enumerate(kept, first_customer + 1):
        t["turn"] = n
        t["chars"] = len(t["text"])
        t["customer"] = t["speaker"].lower() not in ours and t["speaker"] != "System audio"

    (work / "turns.json").write_text(json.dumps({
        "meeting_id": doc.get("id"),
        "title": doc.get("title"),
        "created_at": doc.get("created_at"),
        "raw_utterances": len(utterances),
        "merged_turns": len(merged),
        "stripped_before_first_customer": [{"turn": i + 1, "speaker": t["speaker"], "text": t["text"]} for i, t in enumerate(stripped)],
        "turns": kept,
    }, indent=1))
    speakers = sorted({t["speaker"] for t in kept})
    print(f"[merge] {len(utterances)} utterances → {len(merged)} turns; stripped {len(stripped)} pre-customer turns; first kept turn [{kept[0]['turn']}] {kept[0]['speaker']}")
    print(f"[merge] speakers: {', '.join(speakers)}")
    print(f"[merge] wrote {work/'turns.json'}")


# ---------------------------------------------------------------------------
# anchor
# ---------------------------------------------------------------------------


def load_anchors(path: Path) -> dict[int, dict]:
    """anchors.json → {turn: {bst, evidence}}. A top-level "_unclear": [turns] is kept aside."""
    data = json.loads(path.read_text())
    out: dict[int, dict] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "_unclear":
                out[-1] = {"unclear": [int(x) for x in v]}
                continue
            if isinstance(v, dict):
                out[int(k)] = {"bst": v["bst"], "evidence": v.get("evidence", "")}
            else:
                out[int(k)] = {"bst": v, "evidence": ""}
    else:
        for row in data:
            out[int(row["turn"])] = {"bst": row["bst"], "evidence": row.get("evidence", "")}
    return out


def cmd_anchor(a: argparse.Namespace) -> None:
    work = Path(a.work)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    doc = json.loads((work / "turns.json").read_text())
    turns = doc["turns"]
    anchors = load_anchors(Path(a.anchors))
    unclear = set(anchors.pop(-1, {}).get("unclear", []))
    date, t0 = parse_t0_local(a.t0 or doc.get("created_at"), a.offset)

    idx = {t["turn"]: i for i, t in enumerate(turns)}
    pinned = sorted((idx[n], secs(v["bst"])) for n, v in anchors.items() if n in idx)
    if len(pinned) < 2:
        sys.exit("need at least two anchors inside the kept turns")
    # Anchors must be monotonic; drop any that go backwards and say so.
    clean = [pinned[0]]
    for i, s in pinned[1:]:
        if s > clean[-1][1]:
            clean.append((i, s))
        else:
            print(f"[anchor] dropping anchor at turn [{turns[i]['turn']}] {hms(s)}: earlier than the previous anchor")
    pinned = clean

    # Character rate per segment; extrapolate the ends with the neighbouring rate.
    starts = [0.0] * len(turns)
    for (i0, s0), (i1, s1) in zip(pinned, pinned[1:]):
        chars = sum(t["chars"] for t in turns[i0:i1]) or 1
        rate = (s1 - s0) / chars
        acc = s0
        for j in range(i0, i1):
            starts[j] = acc
            acc += turns[j]["chars"] * rate
    # Before the first anchor.
    i0, s0 = pinned[0]
    i1, s1 = pinned[1]
    rate0 = (s1 - s0) / (sum(t["chars"] for t in turns[i0:i1]) or 1)
    acc = s0
    for j in range(i0 - 1, -1, -1):
        acc -= turns[j]["chars"] * rate0
        starts[j] = acc
    # After the last anchor.
    iN, sN = pinned[-1]
    iM, sM = pinned[-2]
    rateN = (sN - sM) / (sum(t["chars"] for t in turns[iM:iN]) or 1)
    acc = sN
    for j in range(iN, len(turns)):
        starts[j] = acc
        acc += turns[j]["chars"] * rateN

    anchored = {i for i, _ in pinned}
    for j, t in enumerate(turns):
        t["bst"] = hms(starts[j])
        t["bst_end"] = hms(starts[j + 1]) if j + 1 < len(turns) else hms(starts[j] + max(2, t["chars"] / 15))
        t["timer"] = fmt_timer(starts[j] - t0)
        t["anchor"] = j in anchored
        if t["turn"] in unclear:
            t["unclear"] = True           # frames.py select reads this and builds a moment for it
        if j in anchored:
            t["anchor_evidence"] = anchors[t["turn"]].get("evidence", "")
    (work / "turns-timed.json").write_text(json.dumps(turns, indent=1))

    title = a.title or doc.get("title") or "Call"
    first, last = turns[0], turns[-1]
    lines = [
        f"# Transcript — \"{title}\", {datetime.fromisoformat(date).strftime('%a %-d %b %Y')}, {first['bst'][:5]} {TZ_LABEL}",
        "",
        f"Source: {CFG.get('transcript_source', 'transcript')} meeting `{doc.get('meeting_id')}`, pulled through its connector. "
        f"\"Microphone\" = {a.user}; \"System audio (Name)\" = the named participant. "
        f"{doc.get('raw_utterances')} raw utterances merged into {doc.get('merged_turns')} turns (consecutive same-speaker utterances joined). "
        "Turn numbers `[n]` are used everywhere else in this folder.",
        "",
        f"**Timestamps are estimates.** The transcript carries no per-utterance times. Each `[HH:MM:SS]` is {TZ_LABEL}, interpolated by character count between "
        f"{len(pinned)} anchor turns pinned to the screen (call timer, the user's note keystrokes, who the call app highlighted as speaking, what the shared screen showed). "
        f"Anchors are marked `⚓`; expect ±15 s between anchors, ±5 s at an anchor. Call timer 00:00 = {hms(t0)} {TZ_LABEL}.",
        "",
    ]
    stripped = doc.get("stripped_before_first_customer") or []
    if stripped:
        lines.append(f"Turns 1–{len(stripped)} (before the first customer turn: "
                     + "; ".join(f"[{s['turn']}] {s['speaker']}: \"{s['text'][:60]}\"" for s in stripped[:3])
                     + ("…" if len(stripped) > 3 else "") + ") are stripped; the first kept turn is [" + str(first["turn"]) + "].")
        lines.append("")
    lines.append("Speaker labels as Granola gave them; \"System audio\" alone is an unidentified voice.")
    lines.append("")
    lines.append("---")
    lines.append("")
    for t in turns:
        mark = " ⚓" if t["anchor"] else ""
        lines.append(f"[{t['bst']}]{mark} [{t['turn']}] {t['speaker']}: {t['text']}")
        lines.append("")
    (out / "transcript.md").write_text("\n".join(lines))
    print(f"[anchor] {len(turns)} turns timed from {len(pinned)} anchors: {first['bst']} → {last['bst']} {TZ_LABEL}; wrote {work/'turns-timed.json'} and {out/'transcript.md'}")


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


def cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").strip()


def cmd_timeline(a: argparse.Namespace) -> None:
    work = Path(a.work)
    out = Path(a.out)
    turns = json.loads((work / "turns-timed.json").read_text())
    notes = json.loads((work / "notes-events.json").read_text()) if (work / "notes-events.json").exists() else []
    screen_path = Path(a.screen) if a.screen else work / "screen.json"
    screen = json.loads(screen_path.read_text()) if screen_path.exists() else {"intro": "", "events": []}
    fmap = json.loads((work / "frame-map.json").read_text()) if (work / "frame-map.json").exists() else {}
    t0 = None
    if a.t0:
        _, t0 = parse_t0_local(a.t0, a.offset)

    def turn_at(s: int) -> dict | None:
        """The turn whose [bst, bst_end) holds second s, else the first turn after it."""
        for t in turns:
            if secs(t["bst"]) <= s < secs(t["bst_end"]):
                return t
        return next((t for t in turns if secs(t["bst"]) >= s), None)

    note_by_turn: dict[int, list[str]] = {}
    for e in notes:
        t = turn_at(secs(e["bst"]))
        if t:
            note_by_turn.setdefault(t["turn"], []).append(e["text"])
    screen_by_turn: dict[int, list[str]] = {}
    for e in screen.get("events", []):
        t = turn_at(secs(e["bst"]))
        if t:
            screen_by_turn.setdefault(t["turn"], []).append(e["screen"])
    frame_by_turn: dict[int, list[str]] = {}
    for m in sorted(fmap.values(), key=lambda v: v["n"]):
        t = turn_at(secs(m["bst"]))
        if t:
            frame_by_turn.setdefault(t["turn"], []).append(f"[{m['n']}]({m['file']})")

    title = a.title or "Call"
    lines = [
        f"# Timeline — {title}",
        "",
        f"One row per transcript turn. Time = {TZ_LABEL} estimated from screen anchors (±15 s). Timer = call timer"
        + (f" (00:00 = {hms(t0)} {TZ_LABEL})" if t0 is not None else "") + ". "
        f"\"{CFG.get('user_short', 'User')}'s note\" = what {CFG.get('user_short', 'the user')} typed in their notes at that moment. \"Screen\" = what the other side could see on the shared screen, written only when it changed. "
        "Frame = the kept image in `frames/`, by number.",
        "",
    ]
    if screen.get("intro"):
        lines += [screen["intro"], ""]
    lines += [f"| {TZ_LABEL} | Timer | Who | Said | {CFG.get('user_short', 'User')}'s note | Screen | Frame |", "|---|---|---|---|---|---|---|"]
    for t in turns:
        lines.append("| " + " | ".join([
            t["bst"], t.get("timer", ""), cell(t["speaker"]), cell(t["text"]),
            cell(" / ".join(note_by_turn.get(t["turn"], []))),
            cell(" · ".join(screen_by_turn.get(t["turn"], []))),
            " ".join(frame_by_turn.get(t["turn"], [])),
        ]) + " |")
    (out / "timeline.md").write_text("\n".join(lines) + "\n")
    print(f"[timeline] {len(turns)} rows · notes on {len(note_by_turn)} turns · screen changes on {len(screen_by_turn)} · frames on {len(frame_by_turn)} → {out/'timeline.md'}")
    if not fmap:
        print("[timeline] no frame-map.json yet: Frame column empty; re-run after `frames.py keep`")


# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("merge")
    p.add_argument("transcript", help="JSON from get_meeting_transcript, saved to a file")
    p.add_argument("--work", required=True)
    p.add_argument("--user", default=USER, help="name for the Microphone source (config: user_name)")
    p.add_argument("--ours", default=OURS,
                   help="our side, comma separated (config: our_side); the first turn by anyone else starts the kept transcript")
    p.set_defaults(fn=cmd_merge)

    p = sub.add_parser("anchor")
    p.add_argument("anchors", help="anchors.json from the Opus anchoring pass")
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True, help="the call folder")
    p.add_argument("--t0", help="Granola created_at; defaults to turns.json created_at")
    p.add_argument("--offset", type=int, default=LOCAL_OFFSET, help="local UTC offset in hours (config: local_utc_offset_hours)")
    p.add_argument("--title")
    p.add_argument("--user", default=USER)
    p.set_defaults(fn=cmd_anchor)

    p = sub.add_parser("timeline")
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--screen", help="screen.json from the Opus timeline pass (default DIR/screen.json)")
    p.add_argument("--t0")
    p.add_argument("--offset", type=int, default=LOCAL_OFFSET)
    p.add_argument("--title")
    p.set_defaults(fn=cmd_timeline)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
