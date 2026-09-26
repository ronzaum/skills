#!/usr/bin/env python3
"""Copy a call folder's call.md into the user's Obsidian vault with its frames.

The vault folder and output root come from config.json (next to this script) (vault_call_tasks_dir, output_root).

  obsidian_push.py <call-folder> [--name "<YYYY-MM-DD> <Name>"] [--vault DIR]

<call-folder> is `<output_root>/<YYYY-MM-DD>-<Name>/`. The note becomes its
own folder in the vault:

    <vault_call_tasks_dir>/<YYYY-MM-DD> <Name>/<call.md H1 text>.md
    <vault_call_tasks_dir>/<YYYY-MM-DD> <Name>/frames/NN__tMM-SS__HH-MM-SS<tz>.webp

so `](frames/…)` links in call.md work unchanged and Obsidian's inline title
is the call title ("Northwind check-in call — Fri 25 Sep 2026"). On the way in:
  * the H1 is dropped (Obsidian shows the file name); line 1 is a "Full
    analysis" file:// link to the output folder, line 2 a link to dev-prompts.md;
  * every task in the ✅ checklist becomes an Obsidian link to its details
    (`[[#^tN|N. title]]`); each task's details are made one block (blank
    lines inside a task become `&nbsp;` lines) with the `^tN` id on its last
    line, so following the link highlights the whole task.
Re-running replaces the note and the frames folder. Never opens the file.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
VAULT = CFG.get("vault_call_tasks_dir")                 # asked on the first run, then saved in config.json
OUTPUT_ROOT = Path(CFG.get("output_root") or (Path.cwd() / "call-analysis"))
CHECK_RE = re.compile(r"^- \[ \] (\d+)\. (.+)$", re.M)
TASK_START_RE = re.compile(r"^\*\*(\d+)\. .+\*\*$")


def link_tasks(text: str) -> str:
    """Checklist rows → [[#^tN|…]]; each task's details become ONE block with a ^tN id.

    Obsidian highlights a single block when a block link is followed. A task's
    details span several paragraphs, so the blank lines inside a task are
    turned into `&nbsp;` spacer lines (they render as empty lines but keep the
    paragraph going) and the id sits on the task's last line. Clicking the
    checklist entry then highlights the whole task, title to Frame.
    """
    text = CHECK_RE.sub(lambda m: f"- [ ] [[#^t{m.group(1)}|{m.group(1)}. {m.group(2)}]]", text)
    head, sep, body = text.rpartition("\n---\n")          # the last rule starts the task details
    if not sep:
        return text
    out: list[str] = []
    task: list[str] = []
    task_n = ""

    def flush() -> None:
        if not task:
            return
        while task and not task[-1].strip():
            task.pop()
        lines = [l if l.strip() else "&nbsp;" for l in task]
        lines[-1] = lines[-1] + f" ^t{task_n}"
        out.append("\n".join(lines))

    for line in body.split("\n"):
        m = TASK_START_RE.match(line)
        if m:
            flush()
            task, task_n = [line], m.group(1)
        elif task:
            task.append(line)
        else:
            out.append(line)
    flush()
    return head + sep + "\n\n\n".join(o for o in out if o.strip()) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="<output_root>/<YYYY-MM-DD>-<Name>")
    ap.add_argument("--name", help='vault folder name, default "<YYYY-MM-DD> <Name>" from the call folder name')
    ap.add_argument("--vault", default=VAULT)
    a = ap.parse_args()

    if not a.vault:
        raise SystemExit("no Obsidian folder set: pass --vault or save vault_call_tasks_dir in config.json (the skill asks on the first run)")
    folder = Path(a.folder).resolve()
    call_md = folder / "call.md"
    if not call_md.exists():
        raise SystemExit(f"no call.md in {folder}")
    slug = folder.name                                   # 2026-09-25-Northwind
    m = re.match(r"^((?:_[a-z]+-)?)(\d{4}-\d{2}-\d{2})-(.+)$", slug)   # optional `_verify-` style prefix
    if not m:
        raise SystemExit(f"folder name must be <YYYY-MM-DD>-<Name>, got {slug}")
    prefix, date, name_part = m.groups()
    name = a.name or f"{prefix}{date} {name_part}"
    note_dir = Path(a.vault) / name
    note_dir.mkdir(parents=True, exist_ok=True)

    text = call_md.read_text()
    # The note is named after call.md's H1 so Obsidian's inline title reads
    # "Northwind check-in call — Fri 25 Sep 2026"; the H1 line itself is dropped.
    h1 = re.match(r"# (.+)\n", text)
    if not h1:
        raise SystemExit("call.md must start with an H1 title")
    note_name = h1.group(1).strip().replace("/", "-")
    text = text[h1.end():].lstrip("\n")
    try:
        rel = folder.relative_to(OUTPUT_ROOT.parent)
    except ValueError:
        rel = folder
    top = (f"Full analysis (timeline, transcript, dev prompts): [{rel}/](file://{folder}/)\n"
           f"Dev prompts: [dev-prompts.md](file://{folder / 'dev-prompts.md'})")
    text = re.sub(r"^(Full analysis \(timeline, transcript, dev prompts\)|Dev prompts): .*\n", "", text, flags=re.M)
    text = f"{top}\n{text}"
    text = link_tasks(text)

    dest_frames = note_dir / "frames"
    if dest_frames.exists():
        shutil.rmtree(dest_frames)
    cited = sorted(set(re.findall(r"\]\(frames/([^)]+)\)", text)))
    if cited:
        dest_frames.mkdir(parents=True)
        for f in cited:
            src = folder / "frames" / f
            if not src.exists():
                raise SystemExit(f"cited frame missing: {src}")
            shutil.copy2(src, dest_frames / f)

    note = note_dir / f"{note_name}.md"
    for stale in note_dir.glob("*.md"):            # a renamed title leaves no orphan note behind
        if stale != note:
            stale.unlink()
    note.write_text(text)
    print(f"[obsidian] wrote {note} ({len(cited)} frames → {dest_frames if cited else 'none'})")


if __name__ == "__main__":
    main()
