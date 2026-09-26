---
name: call-analysis
version: 2.0.0
description: Analyse a call from its transcript, the user's private notes and their screen captures into one folder (scores, ✅ Tasks with owner and due date, timeline, transcript, engineering dev-prompts, cited frames) plus an Obsidian note. Use when the user says "analyse this call", "analyse the <name> call", pastes a meeting link, or asks what came out of a call.
---

# Call analysis

**TL;DR.**

1. **Call and transcript.** Resolve the meeting through the provider's
   connector (Granola built in), pull the transcript and the user's private
   notes. The transcript has speakers and words but no times and no screen.
2. **Familiar enrichment.** Familiar captured the user's screen every ~5 s
   with OCR. The call timer in the OCR gives the clock, so turns get pinned
   to real times; the shared screen shows what the other side actually saw
   (pages, errors, files, numbers); the notes editor shows what the user typed
   and when, which is where tasks surface. OCR text is read for every frame;
   images only in the moments that need a picture, key frames first,
   neighbours only while a question is open.
3. **Outputs.** `call.md` (a score per side with evidence, every task with
   asker, doer, due date and the frame it surfaced on), `timeline.md` (one
   row per turn with the user's note and the screen when it changed),
   `transcript.md`, `dev-prompts.md` (self-contained prompts for an engineering
   LLM, with the frames and quotes as evidence), only the cited frames, and
   the note copied into Obsidian with clickable tasks.

Skill files: `~/.claude/skills/call-analysis/` → `frame-reader.md` (the brief the image
agents follow), `frames.py`, `timeline.py`,
`obsidian_push.py`. Run scripts with `python3`. Keep every
intermediate file in a work dir under this session's scratchpad:
`WORK=<scratchpad>/call-<slug>`.

## Settings

No setup file is needed to start. The skill works out:

- **the user and their side** from the meeting's participants: the provider
  marks the note creator (that is the user); everyone with the user's email
  domain is on their side. Pass them to `timeline.py merge` as `--user` and
  `--ours`.
- **Familiar's folder** from Familiar's own `~/.familiar/settings.json`
  (`contextFolderPath`).
- **time zones** from the machine's clock, checked against the on-screen
  clock in the frames.
- **the output folder** as `call-analysis/` under the current working folder.

Two things cannot be guessed. On the first run, ask the user once for their
Obsidian folder for call notes and, optionally, the code repo that
dev-prompts should grep. Save both in `config.json` next to `SKILL.md`
(`vault_call_tasks_dir`, `code_repo`) and never ask again. The same file can
override anything above (`familiar_root`, `output_root`, `tz_label`,
`local_utc_offset_hours`, `stamp_utc_offset_hours`, `user_name`,
`user_short`, `our_side`). It is personal: never share it with the skill.

## Inputs and how the call is resolved

The user gives any of: a name and "just happened" / "the call today"; a
meeting link from the provider; a time. The skill resolves the meeting with
the provider's connector and derives the call window itself: start = the
meeting's `created_at`; end = the frame where the call ended (the leave
marker), which `frames.py index` reports. It never asks for a window. The
only question it asks is which meeting, when two match.

For Granola: a link `notes.granola.ai/t/<uuid>-<suffix>` or `/d/<uuid>`
carries the meeting id (the 36-character UUID). Otherwise `list_meetings`
(`time_range: custom`, the day or the last 7 days,
`involvement.captured_by_me: true`) and match on title, attendee domain or
start time. Then `get_meetings [id]` → attendees, `private_notes` (the
user's own typed notes, kept verbatim), the AI summary (read once for
cross-check, never saved). Then `get_meeting_transcript id` → JSON with
`id`, `title`, `created_at`, `transcript`; save it to
`$WORK/granola-transcript.json` (when the result is spilled to a
tool-results file, copy it with `tail -n +2`; line 1 is a preamble).

Name slug for the folder: the short name the user uses for the other side
(a company, a team, a person). Date = `created_at` in the user's zone.

## Sources and hard rules

- **Read only what is given.** The transcript provider, the user's notes
  from the same provider, the Familiar frames in the window, the code repo
  from settings when writing dev-prompts, and, only if the user offers
  it, their WhatsApp connector for messages they sent during the call.
  Nothing else: no mail, no other notes, no other systems, no other people's files.
- **Provider connector only.** Never read a provider's local token or
  cache files, never scrape its web pages. If the connector is unauthorised,
  stop and tell the user to authorise it in their connector settings.
- **Swapping the provider.** `transcript_source` names it. A provider must
  supply: a meeting id or link, `created_at` (call start, UTC), the
  participants, the user's private notes (optional), and the transcript.
  `timeline.py merge` reads Granola's JSON, a generic JSON
  `{"title", "created_at", "utterances": [{"speaker", "text"}, …]}`, or a
  plain text file of `Speaker: text` blocks. To add a provider, write the
  resolve-and-pull step for it in this section and keep the rest unchanged.
- **Frames come from Familiar:** `<familiar_root>/stills/<session>/*.webp`
  and the OCR twins in `stills-markdown/<session>/*.md`. Filename stamps are
  in a fixed zone (`stamp_utc_offset_hours`) whatever the Mac says; the
  on-screen clock in the OCR confirms the offset. The call timer in the OCR
  is the clock; timer 00:00 = `created_at`. `frames.py index` prints the
  anchor check.
- **Never run `open` on a file.** `open <folder>` only, and only if asked.
- **Nothing personal.** Frames or text from the user's private apps (chat,
  mail, IDE, personal notes) are never kept or quoted unless the other side
  could see them on a shared screen. The kept frames are only those cited
  by `call.md` or `dev-prompts.md`.
- **Accuracy over cost.** Models are split by what each step needs (table
  below), never by price.
- Formatting in every output file: no emojis except the `✅` in the Tasks
  heading; no tables except `timeline.md`; spacing only (two blank lines
  between tasks; a `---` rule between sections with two blank lines before
  it and none after it; no blank lines inside the two score blocks). Bold
  labels (`**Do:**`, `**Due by:**`, `**Owner/Giver:**`, `**Frame:**`); the
  Asked text sits directly under the task title with no label.

## Pipeline

`OUT=<output_root>/<YYYY-MM-DD>-<Name>`, `WORK` in the scratchpad, `T0` the
meeting's `created_at`.

**1. Resolve and pull (main session, provider connector).** Resolve the
meeting as above. Write the user's private notes one line per note to
`$WORK/notes.txt` (drop `---` separators). Save the transcript. Note title,
attendees, the other side's people with roles, our people.

**2. Frame index (Sonnet 5 agent runs the script and reads the text).**
`frames.py index <start> <end> --t0 $T0 --work $WORK --notes $WORK/notes.txt`
with `<start>` = t0 in local time and `<end>` = t0 + 2 h on the first run.
The script walks every frame in the window, reads clock, call timer, share
state, front app, note lines, error banners and OCR change, and writes
`$WORK/frame-index.json` + `$WORK/notes-events.json`. It prints the anchor
check (median OCR-timer drift vs t0; > 2 s means t0 is wrong) and
`call ended at HH:MM:SS` when it sees a leave marker; re-run with that as
`<end>`. The agent then reads `frame-index.json` (text, not images) and
writes `$WORK/ocr-pass.md`: share runs with a presenter guess and what the
OCR says was shared, every error banner with its time, joins and leaves seen
in the roster, whether the anchor check passed. No images in this step.

**3. Merge the transcript (script).**
`timeline.py merge $WORK/<transcript file> --work $WORK --user "<user>" --ours "<our side>"` → `$WORK/turns.json`.
Consecutive same-speaker utterances are joined, `Microphone` becomes the
user, other labels are kept as given, turns are numbered, turns before the
first other-side turn are stripped.

**4. Anchoring pass (Fable 5.1 agent, `model: fable`).** Give it `turns.json`,
`frame-index.json`, `notes-events.json`, `ocr-pass.md`, the OCR `.md` paths.
It writes `$WORK/anchors.json` as `{"<turn>": {"bst": "HH:MM:SS",
"evidence": "<frame id>: why"}, "_unclear": [<turns it could not place>]}`.
A turn is pinned when the screen proves when it was said: the user types
what a speaker just said (notes-events), a share starts or stops while
someone says "let me share" / "can you see", an error banner appears while
someone reads it aloud, the call app highlights a speaker (read the image
for that, sparingly), a join or leave matches a greeting, the last turn sits
on the last frame before the leave marker. Anchors must be monotonic; aim
for one every one to two minutes and one at each end. Then
`timeline.py anchor $WORK/anchors.json --work $WORK --out $OUT --t0 $T0 --title "<title>"`
→ `$WORK/turns-timed.json` (with `unclear: true` on the listed turns) and
`$OUT/transcript.md` (⚓ on anchors, header explaining ±15 s).

**5. Select what to look at (script).**
`frames.py select $WORK/turns-timed.json --work $WORK` → `$WORK/read-list.json`
and `read-list-batch-NN.md`. See "Selecting frames" below.

**6. Frame-reader agents (Fable 5.1, parallel, ~80 frames each).** One agent
per `read-list-batch-NN.md`, all launched in one message, `model: fable`,
brief = `frame-reader.md` with the `<…>` fields filled. Each writes
`$WORK/frames-batch-NN.md` (one pipe-row per frame read, a closing line per
moment saying which frames answered it and how far it widened).

**7. Timeline pass (Sonnet 5 agent).** Give it `turns-timed.json`,
`notes-events.json`, all `frames-batch-NN.md`, `ocr-pass.md`. It writes
`$WORK/screen.json`: `{"intro": "<one line: share runs with presenter and
times, who joined or left when, when the call ended>", "events": [{"bst":
"HH:MM:SS", "screen": "<Presenter>: <what the other side saw, short,
verbatim where it matters>"}]}`, one event per change of the shared screen
and nothing when it did not change. Then
`timeline.py timeline --work $WORK --out $OUT --t0 $T0 --title "<title>"`
(the Frame column fills after step 9; re-run it then).

**8. call.md and dev-prompts.md (main session).** Read `turns-timed.json`,
`notes-events.json`, the `frames-batch-NN.md` rows and `screen.json`;
cross-check the provider's summary once. Write `$OUT/call.md` to the
contract below, citing frames as `{frame:<id>}` placeholders. Write
`$OUT/dev-prompts.md` for an LLM working in the product's code repo
(`code_repo`): grep that checkout for every "where it lives" path and state
its commit and date from `git -C <repo> log -1 --format='%h %cs'` in the
header. Without `code_repo`, write the prompts with the evidence and the
hypothesis and say paths were not checked.

**9. Keep the cited frames (script).**
`frames.py keep $OUT --work $WORK --from $OUT/call.md $OUT/dev-prompts.md`
copies only the cited frames into `$OUT/frames/` as
`NN__tMM-SS__HH-MM-SS<tz_label>.webp`, numbered from 01 in time order,
rewrites every `{frame:<id>}` to `[N](frames/NN__…)`, writes
`$WORK/frame-map.json`. Re-run `timeline.py timeline` so the Frame column
fills.

**10. README.md and Obsidian (script).** Write `$OUT/README.md` to the
template. Then `obsidian_push.py $OUT`. Report: the folder path, the two
scores, the number of tasks, frames read vs kept, anything unclear.

## Selecting frames

OCR text is read for every frame in the window; images are read only inside
**visual-need moments**:

- a share is on (every contiguous run of share frames);
- an error or warning banner is in the OCR;
- the user's note changed (a task surfacing on screen);
- a transcript turn with visual language: "you see", "this one", "here",
  "let me share", "can you see", "this tab/button/dropdown", "scroll",
  "upload", "refresh", "error", "went wrong", "not working", "stuck",
  "loading", "demo", or a count read aloud ("221 issues");
- an ask or a promise ("can you", "please", "we need", "I'll send",
  "we'll fix", "by Tuesday") within 20 s of a share or a note change;
- a turn the anchoring pass flagged `unclear`.

Overlapping moments merge. Inside a moment the **key frames** are those
whose OCR content really changed (line-set similarity below 0.6 vs the
previous frame), plus one frame at least every 60 s so a slow scroll or a
long static share is still seen. **±1 and ±2 neighbours** are listed but
read only while the moment's question is unanswered. Excluded, with a reason
in `read-list.json`: post-call frames, OCR-identical frames, frames showing
only the user's private apps with no share and no note change. No cap on
the count; batches of ~80 key frames, a moment longer than a batch is split
into parts. Typical result: a 50-minute call with ~500 frames in the window
yields ~200 key frames and ~220 images read.

## Output contract

### call.md

```
# <Name> <call kind> — <Ddd D Mon YYYY>

<Provider>: <link the user gave, or the meeting's URL>
Attended: <our people, first names only>, <other side, first and last name>

### How the customer took it · N/10

<~70 words. Named people, what each got or did not get, their closing words vs their real verdict, quoted from the transcript.>

### How <user_short> handled it · N/10

**Good:** <what worked, comma-separated, evidence-backed.>
**Not good:** <what did not, same form; no softening.>
**Next time:** <one or two rules.>


---
## ✅ Tasks

- [ ] 1. <title>
- [ ] 2. <title>
…


---
**1. <title>**
<what the named person wants and why, plain words>

**Do:** <what to open, send, build>

**Due by:** <only what was said on the call, else "no date">
**Owner/Giver:** <asker> to <doer>
**Frame:** {frame:<id>} {frame:<id>}


**2. <title>**
…
```

Title is the call kind and the date only, no times; the provider line and
Attended sit directly under it with no blank line between them. "Customer"
is whoever the other side is (a client, a partner, a candidate); the
heading keeps the word. The other side's score sits on their heading, the
user's on theirs. No blank line between the three bold lines of the user's
block. Two blank lines between tasks. Each `---` has two blank lines before
it and the next heading or task directly after it. `**Frame:**` only when
the task surfaced on screen; omit the line otherwise. No Watch, no Open
threads, no "What happened", no tables. `{frame:<id>}` placeholders become
`[N](frames/NN__…)` after `keep`.

**Tasks rules (verbatim, apply every time):**

- Title = the outcome, not the activity ("Release the two stuck checklists", not "Check queue").
- The line under the title = what the named person wants and why, in plain words (no "Asked:" label); quote the transcript where the wording matters.
- Do = what to open, send, build; concrete enough that someone else could do it.
- Due by = only what was said on the call, else "no date". Never invent a date.
- Owner/Giver = "<asker> to <doer>". The giver is the named person on the other side. "<user> to <user>" only when the user initiated it (their own note, their own promise nobody asked for). A promise the user made to the other side reads "<user> to <team>" with the other person named in the Asked line.
- Frame = the frame(s) where the task surfaced on screen, by number after `keep`.
- Every task said or typed on the call is listed, including the user's own note lines that were never said aloud ("<user_short>'s own note during the call, never said aloud.").

**Scoring rule (verbatim):**

- Customer N/10 = how the other side took the call: did they get answers or dates, were they reassured or sceptical, what did their closing words say versus their actual verdict.
- <user_short> N/10 = how the user handled it: opener, listening, closing every "let me check", one screen or many, promises with or without the people who must deliver them.
- Both blocks direct, evidence from the transcript only, ~70 words for the other side's block and ~80 for the user's, no softening, no praise without a quote behind it.

### timeline.md

Generated by `timeline.py timeline`. Header, the `Shares:` intro line from
`screen.json`, then one row per turn:
`| <tz> | Timer | Who | Said | <user_short>'s note | Screen | Frame |`.
Screen is written only on the row where the shared screen changed and blank
otherwise; Frame is `[N](frames/NN__…)` for kept frames whose time falls in
that turn.

### transcript.md

Generated by `timeline.py anchor`: title line, source paragraph (meeting
id, provider, label mapping, raw→merged counts), the "Timestamps are
estimates" paragraph (anchor count, ±15 s / ±5 s, timer 00:00 = t0), the
stripped-turns note, then `[HH:MM:SS] ⚓ [n] Speaker: text` per turn, blank
line between.

### dev-prompts.md

```
# <Name> call <YYYY-MM-DD> — engineering prompts

Written for an LLM working inside the product's code repo, not for a person. Each section is one self-contained prompt: paste it into a coding session opened at the repo root. Evidence is quoted from the call transcript (`transcript.md`, turn numbers `[n]`) and the screen captures (`frames/`, numbered 1–<N> in time order; each name also carries the call timer `tMM-SS` and the local clock, 00:00 = <t0>). File paths were checked against the checkout at commit `<hash>` (<date>); re-grep before editing, the tree may have moved.

Context: <the other side, their tenant or account id if one exists, domain>. People quoted: <names and roles>.

Ordering is by how self-contained the fix is. P1 to Pk are bugs seen live on the other side's screen. Then product gaps they asked for. Then content, not code.

---

## P1. <one-line problem>

### Prompt

You are <fixing / diagnosing / scoping> … Read the evidence, <reproduce it, fix it, add a regression test,> and write a two-line note for the customer.

**What the customer saw.** <frames by number, verbatim on-screen text>

**What was said.** <turn quotes with [n]>

**Where it lives.** <paths grepped in the checkout, with line hints>

**Hypothesis to test first.** <one paragraph>

**Do.**
1. …
2. …

**Do not** … (when a guardrail is needed)

---
```

One prompt per fixable item; scoping-only prompts say "You are scoping,
not building." Frames cited as `{frame:<id>}` until `keep` rewrites them.
No branch names, no internal repo names: the header says "the product's
code repo" and the commit hash; the prompt is for an LLM that already sits
in it.

### README.md

```
# <Name> — call analysis, <Ddd D Mon YYYY HH:MM> <tz>

- `call.md` — scores, how it went, Tasks (title, what was asked, what to do, due, owner/giver, frame). Read this.
- `timeline.md` — one row per transcript turn: time, who, said, <user_short>'s note typed at that moment, what <Name> could see on the shared screen, kept frame.
- `dev-prompts.md` — for an LLM in the product's code repo, not for people: <N> self-contained prompts (<short list>), each with transcript quotes, frame refs and repo entry points.
- `transcript.md` — raw transcript (provider connector), turns numbered, local time estimated from screen anchors.
- `frames/` — <N> stills, only those cited by a task in `call.md` or a prompt in `dev-prompts.md`. Numbered in time order: `NN__t<call timer>__<local time>.webp`. `Frame: 7` in call.md means `frames/07__…`. Timer 00:00 = <t0>.

Sources: <provider> meeting `<id>` (transcript + <user_short>'s private notes via the connector; AI summary cross-checked, not kept), Familiar stills <start>–<end> (<frames in window> in window, <read> read as images, <kept> kept). <Who shared; whether the user shared.> Nothing personal kept.
```

### frames/

Only cited frames. `NN__tMM-SS__HH-MM-SS<tz_label>.webp`, `NN` from 01 in
time order. Referred to by number everywhere. Nothing personal unless the
other side saw it.

## Obsidian push

`obsidian_push.py $OUT` makes the note its own folder in the vault:

```
<vault_call_tasks_dir>/<YYYY-MM-DD> <Name>/
  <call.md H1 text>.md                 e.g. "Acme check-in call — Fri 25 Sep 2026.md"
  frames/NN__tMM-SS__HH-MM-SS<tz>.webp   (only the frames call.md cites)
```

so the `](frames/…)` links in call.md work unchanged and Obsidian's inline
title is the call title (the H1 line is dropped from the note body; nothing
else sits above it). Line 1 of the note is `Full analysis (timeline,
transcript, dev prompts): [<output_root>/<folder>/](file:///…/<folder>/)`,
line 2 is `Dev prompts: [dev-prompts.md](file:///…/dev-prompts.md)`, then
the provider and Attended lines, no blank lines between them. The script
also turns every `- [ ] N. title` row into `- [ ] [[#^tN|N. title]]` and
makes each task's details one Obsidian block: the blank lines inside a task
become `&nbsp;` lines (they render as empty lines) and the `^tN` block id
goes on the task's last line, so clicking a task in the checklist jumps to
it and highlights the whole task, title to Frame. The repo call.md keeps
real blank lines; only the vault copy has the `&nbsp;` lines.
The task links render dark blue via the vault CSS snippet
`.obsidian/snippets/call-tasks.css` (enabled in `appearance.json`); if a
task link shows in the accent colour, that snippet is off.
Re-running replaces the note and the frames folder. `--name` overrides the
folder name (used for `_verify` runs). Do not `open` the note.

## Model split

| Step | Model | Why |
|---|---|---|
| Frame index + OCR text pass | Sonnet 5 (`model: sonnet`) | Text only, one pass over every frame's OCR; the accuracy is in the script |
| Frame-reader agents | Fable 5.1 (`model: fable`), parallel, ~80 frames each | Reading what is on an image is the evidence every task, score and prompt rests on; the strongest model goes here |
| Anchoring pass | Fable 5.1 (`model: fable`) | Holds the whole transcript, index, notes and share runs at once and finds the subtle matches; one bad anchor shifts every time after it, and nothing downstream corrects it |
| Timeline pass (Screen on change) | Sonnet 5 (`model: sonnet`) | Turns the reader rows into "screen changed at" events; simple, and call.md does not depend on it |
| Scores, the two blocks, Tasks, dev-prompts, README | Main session (Fable 5.1) | Judgement and the wording the other side would recognise |

## Fallbacks

- Provider connector unauthorised or the meeting not returned → stop, tell
  the user to authorise it (or share the meeting with their account); do not
  scrape, do not touch token files.
- No Familiar frames in the window (capture off, another machine) →
  produce `call.md`, `transcript.md` (anchors from t0 and character count
  only, header says so), `dev-prompts.md` without frame refs, no `frames/`,
  no `timeline.md` Screen/Frame columns; say plainly in the README and in the
  reply that the run was text-only.
- Anchor check fails (median drift > 2 s) → shift `--t0` by the printed
  offset and re-run `index`; if the OCR shows no timer at all, anchors come
  from note keystrokes and share changes only and the transcript header says
  ±30 s.
- A frame-reader agent fails → re-launch that batch only; the batch file is
  the unit of work.

## What a good output looks like

Anonymised from a real run: people are John Doe, Jane Roe, Richard Miles; the client is Acme, the project Space House. The shape is what matters.

### call.md header

```
# Acme check-in call — Fri 25 Sep 2026

Granola: https://notes.granola.ai/d/<id>
Attended: Alex, Sam, John Doe, Jane Roe, Richard Miles

### How the customer took it · 6/10

John got a date, not answers: his question about which codes for the Space House project are loaded went unanswered and he learned two uploads sat a week in our queue. Jane's own checklist plan was accepted only after eight minutes of pushback. Richard got a clear yes on organising the library. Closing words "really encouraging"; real verdict: "button down some of these glitches before we can expand to more users."

### How Alex handled it · 6/10

**Good:** honest opener, asked for cameras, found the "Only you can see it" cause himself, live notes, every promise dated.
**Not good:** argued with Jane instead of repeating her plan back; "let me check" never closed; multitasked through Sam's demo and missed him overselling the plugin; promised a report "next week" with no engineer on the call; invite drafted on the wrong date.
**Next time:** one screen during a client call; close every "let me check" before goodbye.
```

### One task

```
**3. Confirm to John that every code the Space House project needs is in the platform**
John asked whether every code the Space House project needs (the city code, two county specs, the water authority standards) is loaded and visible to his team. Alex said "let me check" and never came back with an answer.

**Do:** Log in as an Acme user, list the loaded books under All codes and Org codes, compare with his list, publish anything missing, send John the list of what is there.

**Due by:** before Mon 28 Sep
**Owner/Giver:** John Doe to Alex
**Frame:** [3](frames/03__t19-49__18-19-15BST.webp)
```

### One dev-prompt section

```
## P7. Integration sync expired on a customer project with no nudge

### Prompt

You are checking why a customer's third-party sync connection expired and stayed expired for at least the length of a call, and making the reconnect prompt reach them.

**What the customer saw.** On Jane Roe's Space House project, left rail: "Sync is paused — Your connection expired, so new files and markups have stopped importing. Reconnect to resume." (frame 1, 18:18:05, and again at frame 6). Jane did not mention it. Richard [349], asked whether they have exported anything through it: "I have not."

**Where it lives.** `backend/app/integrations/<provider>/oauth.py` around the line that creates the re-auth notification ("Reconnect … to resume importing files and markups"), the matching route in `backend/app/routes/`, the frontend test that covers the 401 re-auth path, and the guide entry for connecting the integration.

**Do.**
1. Find when Jane's token expired and whether the re-auth notification was created and delivered (email? in-app only?). If in-app only, add an email with the reconnect link.
2. Check token refresh: tokens should be refreshed before expiry while a project has an active sync. If refresh exists and failed, log why.
3. Make the reconnect one click from the banner and confirm sync resumes and back-fills what was made while paused.
4. Write one line for Jane: "your link expired on <date>; click Reconnect in the project and the items since then will import."
```
