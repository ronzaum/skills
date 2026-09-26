#!/usr/bin/env python3
"""Frame tooling for the call-analysis skill.

Machine-specific paths and zones come from config.json (next to this script) (familiar_root,
stamp_utc_offset_hours, local_utc_offset_hours, tz_label). Everything below
that says "local" means the user's wall clock; tz_label names it in file names.

Three sub-commands, run in this order during a call analysis:

  index  <start> <end> --t0 <granola created_at> --work DIR [--notes FILE]
         Walk the Familiar stills in the call window, parse every frame's OCR
         markdown, and write DIR/frame-index.json plus DIR/notes-events.json.
         Prints the timer anchor check (OCR Teams timer vs. stamp-derived timer).

  select <turns-timed.json> --work DIR
         Build "visual-need" moments from the index and the timed transcript,
         mark key frames (OCR changed vs. the previous frame), and write
         DIR/read-list.json plus DIR/read-list-batch-NN.md for the frame-reader
         agents. Nothing is capped; excluded frames are listed with a reason.

  keep   <out-folder> --work DIR --from FILE [FILE ...] [--ids ID,ID]
         Collect every `{frame:<id>}` citation in the given files, copy those
         frames into <out-folder>/frames/ as NN__tMM-SS__HH-MM-SS<tz_label>.webp
         (numbered from 01 in time order), rewrite the citations in place to
         `[N](frames/NN__…)`, and write DIR/frame-map.json (id -> number).

Conventions shared with the rest of the skill:
  * Familiar filename stamps are in a fixed zone (config: stamp_utc_offset_hours).
    The on-screen clock in the OCR confirms the stamp→local offset per run.
  * The Microsoft Teams call timer in the OCR is the true clock. Timer 00:00 is
    the Granola meeting's `created_at`, which `--t0` carries in.
  * A frame's id is its stamp basename, e.g. `2026-09-25T13-23-21-110`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Locations and constants
# ---------------------------------------------------------------------------

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
FAMILIAR_ROOT = Path(CFG.get("familiar_root") or _familiar_root())
STILLS = FAMILIAR_ROOT / "stills"
STILLS_MD = FAMILIAR_ROOT / "stills-markdown"
STAMP_TZ = timezone(timedelta(hours=CFG.get("stamp_utc_offset_hours", -4)))   # Familiar stamps' zone, whatever the Mac says
TZ_LABEL = CFG.get("tz_label") or _tz_label()                                  # suffix in frame names, e.g. BST
DEFAULT_OFFSET = CFG.get("local_utc_offset_hours", _local_offset_hours()) - CFG.get("stamp_utc_offset_hours", -4)   # stamp → local clock, hours

STAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})")
CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
TIMER_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")
DATE_LINE_RE = re.compile(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{1,2}\s+\w{3}$")

# Teams UI words that only appear while someone is sharing a screen.
SHARE_MARKERS = ("Take control", "Pop out", "Annotate", "Stop presenting", "is presenting", "is sharing")
LOADING_MARKERS = ("Loading content",)
CALL_ENDED_MARKERS = ("Leaving...", "Leaving…", "Call ended", "You left the meeting", "Rejoin")

# Banners worth flagging regardless of who is on screen. Matched case-insensitively.
ERROR_MARKERS = (
    "something went wrong",
    "connection expired",
    "sync is paused",
    "finishing",          # "Our team is finishing N books by hand"
    "only you can see",
    "try again",
    "still streaming",
    "session ended",
    "failed",
)

# Apps that are the user's private workspace. A frame showing only these, with no
# share on and no note change, is never read as an image.
PRIVATE_APPS = {
    "whatsapp", "outlook", "mail", "messages", "slack", "cursor", "claude",
    "terminal", "iterm", "finder", "notes", "safari", "google chrome", "chrome",
    "arc", "notion", "linear", "spotify", "calendar",
}
NOTE_APPS = {"granola"}
CALL_APPS = {"microsoft teams", "zoom", "google meet", "meet"}

# Transcript cues that mean the speaker is pointing at something on screen.
VISUAL_CUES = re.compile(
    r"\b(you see|you can see|can you see|do you see|see that|this one|this here|right here|over here|"
    r"let me share|share my screen|sharing my screen|on my screen|look at|looking at|"
    r"showing|show you|screen|click|scroll|this page|this button|this tab|dropdown|banner|popup|"
    r"demo|upload|refresh|error|went wrong|not working|broke|frozen|stuck|loading|"
    r"\d{2,} (issues|items|checks|results|comments))\b",
    re.I,
)
# A customer asking for something, or our side promising something, near a share or a note.
ASK_CUES = re.compile(
    r"\b(can you|could you|would you|will you|please|i need|we need|make sure|send me|send us|"
    r"let's|lets|we should|you should|i'?ll (send|make|check|get|have|do|set)|we'?ll (send|make|have|get|do|fix|add)|"
    r"by (monday|tuesday|wednesday|thursday|friday|next week|end of day|eod|tomorrow|today))\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def parse_stamp(name: str) -> datetime | None:
    """`2026-09-25T13-23-21-110` -> naive datetime in the stamp's own zone."""
    m = STAMP_RE.search(name)
    if not m:
        return None
    d, hh, mm, ss, ms = m.groups()
    return datetime.fromisoformat(f"{d}T{hh}:{mm}:{ss}.{ms}")


def parse_local(s: str, date_hint: str | None) -> datetime:
    """Accept `YYYY-MM-DDTHH:MM:SS`, `YYYY-MM-DD HH:MM:SS` or `HH:MM:SS` (+date_hint)."""
    s = s.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", s):
        if not date_hint:
            sys.exit("time-only window needs --t0 (for the date) or a full timestamp")
        if s.count(":") == 1:
            s += ":00"
        return datetime.fromisoformat(f"{date_hint}T{s}")
    return datetime.fromisoformat(s.replace(" ", "T"))


def parse_t0(s: str) -> datetime:
    """Granola `created_at` (UTC ISO) -> aware UTC datetime."""
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_timer(seconds: float) -> str:
    """Seconds since t0 -> `MM:SS`; minutes run past 59 so names keep sorting."""
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def fmt_bst(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def read_md(path: Path) -> tuple[dict, list[str]]:
    """Split a Familiar stills-markdown file into frontmatter dict + OCR lines."""
    text = path.read_text(errors="replace")
    fm: dict = {}
    ocr: list[str] = []
    if text.startswith("---"):
        head, _, rest = text[3:].partition("\n---")
        for line in head.splitlines():
            if ":" in line and not line.startswith(" "):
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
        body = rest
    else:
        body = text
    for line in body.splitlines():
        if line.startswith('- "') and line.endswith('"'):
            ocr.append(line[3:-1])
    return fm, ocr


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def ocr_digest(lines: list[str]) -> str:
    """Hash of the OCR body with clocks/timers removed, so a tick does not count as a change."""
    keep = [l for l in lines if not TIMER_RE.match(l.strip()) and not CLOCK_RE.match(l.strip())]
    return hashlib.sha1("\n".join(keep).encode()).hexdigest()[:12]


def ocr_lineset(lines: list[str]) -> set[str]:
    """Lines that carry content (no clocks, timers, 1-3 char noise), normalised."""
    out = set()
    for l in lines:
        l = l.strip()
        if len(l) < 4 or TIMER_RE.match(l) or CLOCK_RE.match(l):
            continue
        out.add(norm(l))
    return out


def ocr_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two frames' content lines; 1.0 = same screen."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


# Below this similarity two consecutive frames count as a real screen change.
CHANGE_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


def find_clock(ocr: list[str]) -> tuple[int, int] | None:
    """Mac menu-bar clock: the HH:MM line right after a `Fri 25 Sep` line."""
    for i, line in enumerate(ocr[:-1]):
        l = line.strip()
        if DATE_LINE_RE.match(l) or l.endswith(tuple(f" {d}" for d in ("Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"))):
            m = CLOCK_RE.match(ocr[i + 1].strip())
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


def find_timer(ocr: list[str], clock: tuple[int, int] | None, expected: float | None) -> int | None:
    """Teams call timer in seconds. Picks the MM:SS / H:MM:SS line closest to `expected`."""
    cands: list[int] = []
    for line in ocr:
        m = TIMER_RE.match(line.strip())
        if not m:
            continue
        h, mm, ss = m.groups()
        if h is None:
            # Two-part value: skip the Mac clock itself.
            if clock and (int(mm), int(ss)) == clock:
                continue
            secs = int(mm) * 60 + int(ss)
        else:
            secs = int(h) * 3600 + int(mm) * 60 + int(ss)
        if secs <= 4 * 3600:
            cands.append(secs)
    if not cands:
        return None
    if expected is None:
        return cands[0]
    best = min(cands, key=lambda c: abs(c - expected))
    # A "timer" more than 90 s from the stamp is some other number on screen.
    return best if abs(best - expected) <= 90 else None


def match_note_lines(ocr: list[str], note_lines: list[str]) -> list[str]:
    """Return the note lines visible in this frame (OCR line is a prefix of a note line or vice versa)."""
    seen: list[str] = []
    for line in ocr:
        l = norm(line)
        if len(l) < 4:
            continue
        for n in note_lines:
            nn = norm(n)
            if len(nn) < 4:
                continue
            if nn.startswith(l) or l.startswith(nn) or (len(l) >= 12 and l in nn):
                if n not in seen:
                    seen.append(n)
                break
    return seen


def cmd_index(a: argparse.Namespace) -> None:
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    t0 = parse_t0(a.t0) if a.t0 else None
    date_hint = t0.astimezone(timezone(timedelta(hours=a.offset))).strftime("%Y-%m-%d") if t0 else None
    start = parse_local(a.start, date_hint)
    end = parse_local(a.end, date_hint)

    note_lines: list[str] = []
    if a.notes:
        note_lines = [l for l in Path(a.notes).read_text().splitlines() if l.strip() and l.strip() != "---"]

    # Window in stamp time = local - offset. We do not yet know the offset for
    # sure, so scan a generous range and filter after the clock is read.
    offset = a.offset
    stamp_lo = start - timedelta(hours=offset + 1)
    stamp_hi = end - timedelta(hours=offset - 1)

    sessions = sorted(p for p in STILLS_MD.glob("session-*") if p.is_dir())
    cand: list[tuple[datetime, Path]] = []
    for s in sessions:
        s_dt = parse_stamp(s.name)
        if s_dt is None or s_dt > stamp_hi:
            continue
        for md in s.glob("*.md"):
            dt = parse_stamp(md.name)
            if dt and stamp_lo <= dt <= stamp_hi:
                cand.append((dt, md))
    cand.sort()
    if not cand:
        sys.exit(f"no stills-markdown frames between {stamp_lo} and {stamp_hi} (stamp time)")

    # Pass 1: read the clock on every frame and pin the offset by majority vote.
    parsed = []
    votes: list[int] = []
    for dt, md in cand:
        fm, ocr = read_md(md)
        clock = find_clock(ocr)
        if clock:
            diff = (clock[0] - dt.hour) % 24
            votes.append(diff if diff <= 12 else diff - 24)
        parsed.append((dt, md, fm, ocr, clock))
    if votes:
        offset = statistics.mode(votes)
        if offset != a.offset:
            print(f"[index] on-screen clock says stamp offset is +{offset} h (default was +{a.offset})")
    else:
        print(f"[index] no on-screen clock found; assuming stamp offset +{offset} h")

    frames = []
    prev_digest = None
    prev_lineset: set[str] | None = None
    prev_notes: list[str] = []
    drift: list[float] = []
    bad_drift = []
    for dt, md, fm, ocr, clock in parsed:
        local = dt + timedelta(hours=offset)
        if not (start <= local <= end):
            continue
        fid = md.stem
        app = fm.get("app", "unknown")
        title = fm.get("window_title_norm", fm.get("window_title_raw", "unknown"))
        joined = "\n".join(ocr)
        low = joined.lower()
        share = any(m.lower() in low for m in SHARE_MARKERS)
        loading = any(m.lower() in low for m in LOADING_MARKERS)
        ended = any(m.lower() in low for m in CALL_ENDED_MARKERS)
        errors = sorted({m for m in ERROR_MARKERS if m in low})
        # Stamps are always in STAMP_TZ; the timer is seconds since t0.
        expected = (dt.replace(tzinfo=STAMP_TZ) - t0).total_seconds() if t0 else None
        timer_ocr = find_timer(ocr, clock, expected)
        if t0 and timer_ocr is not None and expected is not None:
            d = timer_ocr - expected
            drift.append(d)
            if abs(d) > 2:
                bad_drift.append((fid, fmt_timer(timer_ocr), fmt_timer(expected), round(d, 1)))
        timer = fmt_timer(expected) if expected is not None else (fmt_timer(timer_ocr) if timer_ocr is not None else "")
        digest = ocr_digest(ocr)
        lineset = ocr_lineset(ocr)
        sim = ocr_similarity(prev_lineset, lineset) if prev_lineset is not None else 0.0
        notes_now = match_note_lines(ocr, note_lines) if note_lines else []
        note_changed = bool([n for n in notes_now if n not in prev_notes])
        front = app.lower()
        private_only = (not share) and (front in PRIVATE_APPS) and (not note_changed)
        frames.append({
            "id": fid,
            "session": md.parent.name,
            "stamp": dt.isoformat(timespec="milliseconds"),
            "bst": fmt_bst(local),
            "timer": timer,
            "timer_ocr": fmt_timer(timer_ocr) if timer_ocr is not None else None,
            "clock": f"{clock[0]:02d}:{clock[1]:02d}" if clock else None,
            "app": app,
            "window_title": title,
            "share": share,
            "loading": loading,
            "call_ended": ended,
            "errors": errors,
            "note_lines": notes_now,
            "note_changed": note_changed,
            "ocr_digest": digest,
            "ocr_similarity": round(sim, 2),
            "ocr_identical": digest == prev_digest,
            "ocr_changed": sim < CHANGE_THRESHOLD,
            "private_only": private_only,
            "md": str(md),
            "webp": str(STILLS / md.parent.name / fm.get("source_image", fid + ".webp")),
            "ocr_head": [l for l in ocr if len(l) > 12][:6],
        })
        prev_digest = digest
        prev_lineset = lineset
        if notes_now:
            prev_notes = notes_now

    # Post-call frames: everything after the first "Leaving…" style marker.
    ended_at = next((f["bst"] for f in frames if f["call_ended"]), None)
    for f in frames:
        f["post_call"] = bool(ended_at and f["bst"] > ended_at)

    (work / "frame-index.json").write_text(json.dumps({
        "window": {"start": fmt_bst(start), "end": fmt_bst(end), "date": start.strftime("%Y-%m-%d")},
        "t0": t0.isoformat() if t0 else None,
        "stamp_offset_hours": offset,
        "frames": frames,
    }, indent=1))

    # The user's note events: the first frame where each note line (or its growing
    # prefix) appears. Later frames that extend the same line update the text
    # but keep the first bst, so the timeline shows when he started typing it.
    events: list[dict] = []
    for f in frames:
        for line in f["note_lines"]:
            hit = next((e for e in events if norm(line).startswith(norm(e["text"])) or norm(e["text"]).startswith(norm(line))), None)
            if hit:
                if len(line) > len(hit["text"]):
                    hit["text"] = line
            else:
                events.append({"bst": f["bst"], "frame": f["id"], "text": line})
    (work / "notes-events.json").write_text(json.dumps(events, indent=1))

    if ended_at:
        print(f"[index] call ended at {ended_at} {TZ_LABEL} (first frame with a leave/ended marker); tighten the window to that if it is earlier than --end")
    n_share = sum(f["share"] for f in frames)
    n_priv = sum(f["private_only"] for f in frames)
    n_post = sum(f["post_call"] for f in frames)
    print(f"[index] {len(frames)} frames {frames[0]['bst']}–{frames[-1]['bst']} {TZ_LABEL} across {len({f['session'] for f in frames})} sessions")
    print(f"[index] share on: {n_share} · private-only: {n_priv} · post-call: {n_post} · note events: {len(events)} · error banners: {sum(bool(f['errors']) for f in frames)}")
    if t0:
        if drift:
            med = statistics.median(drift)
            print(f"[index] anchor check: {len(drift)} OCR timer reads, median drift {med:+.1f} s vs t0={t0.isoformat()}; {len(bad_drift)} frames drift > 2 s")
            for row in bad_drift[:10]:
                print(f"         drift  {row[0]}  ocr {row[1]}  expected {row[2]}  ({row[3]:+} s)")
            if abs(med) > 2:
                print(f"[index] WARNING median drift {med:+.1f} s: t0 is off; consider t0 shifted by {med:+.0f} s")
            else:
                print("[index] anchor check PASS")
        else:
            print("[index] anchor check: no OCR timer reads in window (cannot verify t0)")
    print(f"[index] wrote {work/'frame-index.json'} and {work/'notes-events.json'}")


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


def bst_secs(s: str) -> int:
    h, m, sec = (int(x) for x in s.split(":"))
    return h * 3600 + m * 60 + sec


def cmd_select(a: argparse.Namespace) -> None:
    work = Path(a.work)
    idx = json.loads((work / "frame-index.json").read_text())
    frames = idx["frames"]
    turns = json.loads(Path(a.turns).read_text())
    if isinstance(turns, dict):
        turns = turns["turns"]

    def frames_between(lo: int, hi: int) -> list[int]:
        return [i for i, f in enumerate(frames) if lo <= bst_secs(f["bst"]) <= hi]

    moments: list[dict] = []

    # 1. Share runs: every contiguous stretch of share=true frames.
    run: list[int] = []
    for i, f in enumerate(frames):
        if f["share"] and not f["post_call"]:
            run.append(i)
        elif run:
            moments.append({"kind": "share", "frames": run, "question": "Who is sharing and what exactly is on the shared screen? Quote the text the other side could see."})
            run = []
    if run:
        moments.append({"kind": "share", "frames": run, "question": "Who is sharing and what exactly is on the shared screen? Quote the text the other side could see."})

    # 2. Error banners in the OCR (any app).
    for i, f in enumerate(frames):
        if f["errors"] and not f["post_call"]:
            moments.append({"kind": "error", "frames": frames_between(bst_secs(f["bst"]) - 10, bst_secs(f["bst"]) + 10),
                            "question": f"What error or warning is shown ({', '.join(f['errors'])}), who saw it, and what happened next?"})

    # 3. A task surfacing on screen: the user's note changed.
    for i, f in enumerate(frames):
        if f["note_changed"] and not f["post_call"]:
            moments.append({"kind": "note", "frames": frames_between(bst_secs(f["bst"]) - 5, bst_secs(f["bst"]) + 5),
                            "question": "What did the user type in their notes here, and what on screen or in the call prompted it?"})

    # 4. Transcript cues: visual language, ask verbs while a share is on or a
    #    note changed within 20 s, and turns the anchoring pass flagged unclear.
    share_secs = {bst_secs(f["bst"]) for f in frames if f["share"]}
    note_secs = {bst_secs(f["bst"]) for f in frames if f["note_changed"]}
    for t in turns:
        if not t.get("bst"):
            continue
        s = bst_secs(t["bst"])
        e = bst_secs(t.get("bst_end") or t["bst"]) + 10
        text = t.get("text", "")
        why = None
        if t.get("unclear"):
            why = f"Turn [{t['turn']}] is unclear in the transcript: what on screen explains \"{text[:80]}\"?"
        elif VISUAL_CUES.search(text):
            why = f"Turn [{t['turn']}] {t.get('speaker','?')} points at the screen: \"{text[:80]}\". What are they looking at?"
        elif ASK_CUES.search(text) and (any(abs(s - x) <= 20 for x in share_secs) or any(abs(s - x) <= 20 for x in note_secs)):
            why = f"Turn [{t['turn']}] {t.get('speaker','?')} asks for or promises something: \"{text[:80]}\". What on screen does it refer to?"
        if why:
            moments.append({"kind": "turn", "turn": t["turn"], "frames": frames_between(s - 5, e), "question": why})

    # Merge overlapping moments into one so a frame is read once, then pick key
    # frames and neighbours inside each.
    moments = [m for m in moments if m["frames"]]
    moments.sort(key=lambda m: m["frames"][0])
    merged: list[dict] = []
    for m in moments:
        if merged and m["frames"][0] <= merged[-1]["frames"][-1] + 1:
            last = merged[-1]
            last["frames"] = sorted(set(last["frames"]) | set(m["frames"]))
            last["kinds"].append(m["kind"])
            if m["question"] not in last["questions"]:
                last["questions"].append(m["question"])
            if "turn" in m:
                last["turns"].append(m["turn"])
        else:
            merged.append({"frames": m["frames"], "kinds": [m["kind"]], "questions": [m["question"]], "turns": [m["turn"]] if "turn" in m else []})

    excluded: dict[str, str] = {}

    def usable(i: int) -> bool:
        f = frames[i]
        if f["post_call"]:
            excluded.setdefault(f["id"], "post-call")
            return False
        if f["private_only"]:
            excluded.setdefault(f["id"], "private app only, no share, no note change")
            return False
        return True

    out_moments = []
    key_total = n1_total = n2_total = 0
    for n, m in enumerate(merged, 1):
        fr = [i for i in m["frames"] if usable(i)]
        if not fr:
            continue
        key = [i for i in fr if frames[i]["ocr_changed"]] or [fr[0]]
        if fr[0] not in key:
            key.insert(0, fr[0])
        # A slow scroll or a long static share keeps OCR similarity high; still
        # look at least every --every seconds so nothing on screen goes unseen.
        last = bst_secs(frames[key[0]]["bst"])
        for i in fr:
            if i in key:
                last = bst_secs(frames[i]["bst"])
            elif bst_secs(frames[i]["bst"]) - last >= a.every:
                key.append(i)
                last = bst_secs(frames[i]["bst"])
        key.sort()
        keyset = set(key)
        n1: list[int] = []
        n2: list[int] = []
        for k in key:
            for d, bucket in ((1, n1), (2, n2)):
                for j in (k - d, k + d):
                    if 0 <= j < len(frames) and j not in keyset and j not in n1 and j not in n2 and usable(j) and not frames[j]["ocr_identical"]:
                        bucket.append(j)
        for i in fr:
            if i not in keyset and frames[i]["ocr_identical"]:
                excluded.setdefault(frames[i]["id"], "OCR identical to previous frame")
        ids = lambda xs: [frames[i]["id"] for i in sorted(set(xs))]
        out_moments.append({
            "moment": n,
            "kinds": sorted(set(m["kinds"])),
            "turns": sorted(set(m["turns"])),
            "start": frames[fr[0]]["bst"],
            "end": frames[fr[-1]]["bst"],
            "questions": m["questions"],
            "key": ids(key),
            "neighbours": {"1": ids(n1), "2": ids(n2)},
        })
        key_total += len(key)
        n1_total += len(n1)
        n2_total += len(n2)

    frame_meta = {f["id"]: {"bst": f["bst"], "timer": f["timer"], "app": f["app"], "webp": f["webp"], "md": f["md"]} for f in frames}
    (work / "read-list.json").write_text(json.dumps({
        "window": idx["window"],
        "counts": {"frames_in_window": len(frames), "moments": len(out_moments), "key": key_total, "neighbours_1": n1_total, "neighbours_2": n2_total, "excluded": len(excluded)},
        "moments": out_moments,
        "frames": frame_meta,
        "excluded": excluded,
    }, indent=1))

    # Batches for the frame-reader agents: ~80 key+±1 frames each. A moment
    # longer than a batch (a 20-minute share) is split into parts by key frame,
    # each part carrying the neighbours that sit next to its own key frames.
    def parts_of(m: dict) -> list[dict]:
        keys = m["key"]
        if len(keys) + len(m["neighbours"]["1"]) <= a.batch:
            return [m]
        step = max(1, a.batch - a.batch // 5)
        chunks = [keys[i:i + step] for i in range(0, len(keys), step)]
        out = []
        for n, chunk in enumerate(chunks, 1):
            lo, hi = frame_meta[chunk[0]]["bst"], frame_meta[chunk[-1]]["bst"]
            near = lambda ids: [i for i in ids if lo <= frame_meta[i]["bst"] <= hi]
            out.append({**m, "part": f"{n}/{len(chunks)}", "start": lo, "end": hi, "key": chunk,
                        "neighbours": {"1": near(m["neighbours"]["1"]), "2": near(m["neighbours"]["2"])}})
        return out

    batches: list[list[dict]] = [[]]
    load = 0
    for m0 in out_moments:
        for m in parts_of(m0):
            cost = len(m["key"]) + len(m["neighbours"]["1"])
            if load + cost > a.batch and batches[-1]:
                batches.append([])
                load = 0
            batches[-1].append(m)
            load += cost
    for b, ms in enumerate(batches):
        n_img = sum(len(m["key"]) + len(m["neighbours"]["1"]) for m in ms)
        lines = [f"# Read-list batch {b:02d} — {len(ms)} moment(s), {n_img} key+±1 frames\n",
                 "Frames live at the `webp` path; OCR at the `md` path. Read key frames first, then widen only while the question is unanswered.\n"]
        for m in ms:
            part = f" (part {m['part']})" if m.get("part") else ""
            lines.append(f"\n## Moment {m['moment']}{part} · {m['start']}–{m['end']} {TZ_LABEL} · {', '.join(m['kinds'])}" + (f" · turns {m['turns']}" if m['turns'] else ""))
            for q in m["questions"]:
                lines.append(f"- Q: {q}")
            lines.append("- key:")
            for fid in m["key"]:
                fm = frame_meta[fid]
                lines.append(f"  - {fid}  t{fm['timer'].replace(':','-')}  {fm['bst']}  {fm['app']}  {fm['webp']}")
            for d in ("1", "2"):
                if m["neighbours"][d]:
                    lines.append(f"- ±{d}:")
                    for fid in m["neighbours"][d]:
                        fm = frame_meta[fid]
                        lines.append(f"  - {fid}  t{fm['timer'].replace(':','-')}  {fm['bst']}  {fm['app']}  {fm['webp']}")
        (work / f"read-list-batch-{b:02d}.md").write_text("\n".join(lines) + "\n")

    print(f"[select] {len(frames)} frames in window → {len(out_moments)} moments · key {key_total} · ±1 {n1_total} · ±2 {n2_total} · excluded {len(excluded)}")
    print(f"[select] worst case images read: {key_total + n1_total + n2_total}; typical: key + some ±1")
    print(f"[select] wrote {work/'read-list.json'} and {len(batches)} batch file(s) read-list-batch-NN.md")


# ---------------------------------------------------------------------------
# keep
# ---------------------------------------------------------------------------

CITE_RE = re.compile(r"\{frame:([0-9T\-]+)\}")


def cmd_keep(a: argparse.Namespace) -> None:
    work = Path(a.work)
    idx = json.loads((work / "frame-index.json").read_text())
    frames = {f["id"]: f for f in idx["frames"]}
    out = Path(a.out)
    files = [Path(p) for p in (a.from_files or [])]

    ids: set[str] = set(x for x in (a.ids or "").split(",") if x)
    for p in files:
        ids.update(CITE_RE.findall(p.read_text()))
    missing = sorted(i for i in ids if i not in frames)
    if missing:
        sys.exit(f"cited frames not in the index: {missing}")
    ordered = sorted(ids, key=lambda i: frames[i]["stamp"])
    if not ordered:
        sys.exit("no `{frame:<id>}` citations found and no --ids given")

    fdir = out / "frames"
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir(parents=True)
    fmap: dict[str, dict] = {}
    for n, fid in enumerate(ordered, 1):
        f = frames[fid]
        name = f"{n:02d}__t{f['timer'].replace(':', '-')}__{f['bst'].replace(':', '-')}{TZ_LABEL}.webp"
        src = Path(f["webp"])
        if not src.exists():
            sys.exit(f"missing still: {src}")
        shutil.copy2(src, fdir / name)
        fmap[fid] = {"n": n, "file": f"frames/{name}", "bst": f["bst"], "timer": f["timer"]}
    (work / "frame-map.json").write_text(json.dumps(fmap, indent=1))

    for p in files:
        text = p.read_text()
        new = CITE_RE.sub(lambda m: f"[{fmap[m.group(1)]['n']}]({fmap[m.group(1)]['file']})", text)
        if new != text:
            p.write_text(new)
    print(f"[keep] {len(ordered)} frames → {fdir} (01..{len(ordered):02d}); links rewritten in {', '.join(p.name for p in files) or 'no files'}")
    for fid, m in fmap.items():
        print(f"  {m['n']:2d}  {fid}  {m['file']}")


# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="index the frames in a call window")
    p.add_argument("start", help="window start, local time (YYYY-MM-DDTHH:MM:SS or HH:MM:SS with --t0)")
    p.add_argument("end", help="window end, local time")
    p.add_argument("--t0", help="Granola created_at (UTC ISO); Teams timer 00:00")
    p.add_argument("--work", required=True, help="scratch dir for frame-index.json etc.")
    p.add_argument("--notes", help="file with the user's private notes from the transcript provider, one line each")
    p.add_argument("--offset", type=int, default=DEFAULT_OFFSET, help="default stamp→local offset in hours (from config.json)")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("select", help="build the read-list of visual-need moments")
    p.add_argument("turns", help="turns-timed.json from timeline.py anchor")
    p.add_argument("--work", required=True)
    p.add_argument("--batch", type=int, default=80, help="key+±1 frames per agent batch")
    p.add_argument("--every", type=int, default=60, help="inside a moment, force a key frame at least every N seconds")
    p.set_defaults(fn=cmd_select)

    p = sub.add_parser("keep", help="copy cited frames into <out>/frames and rewrite {frame:id} links")
    p.add_argument("out", help="the call folder (<output_root>/<date>-<Name>)")
    p.add_argument("--work", required=True)
    p.add_argument("--from", dest="from_files", nargs="*", help="files to scan for {frame:<id>} and rewrite")
    p.add_argument("--ids", help="extra frame ids, comma separated")
    p.set_defaults(fn=cmd_keep)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
