# call-analysis

Turns a call into a task list.

1. **Transcript.** Pulls the call transcript and your notes from Granola.
2. **Familiar.** The call timer in Familiar's frames times each transcript line. Images are opened only around bugs, feature requests, action items, feedback and screen shares, starting with the frames where the screen changed.
3. **Output.** An Obsidian note with the task list (owner, due date, the frame each task came from); `dev-prompts.md`, one prompt per bug or request with the quotes, frames and likely code location, so an LLM in the codebase can act on it; a timeline of quotes, speaker, frame and shared screen.

## Requirements

| Tool | Needed for | Swappable |
|---|---|---|
| [Claude Code](https://claude.com/claude-code) | runs the skill | yes, any agent that loads skills and can run Python |
| [Familiar](https://github.com/familiar-software/familiar) | timing, what was shared, your notes | no; without it the skill runs text-only |
| [Granola](https://www.granola.ai) | transcript and your notes | yes, any provider with a start time and a transcript |
| [Obsidian](https://obsidian.md) | a copy of the task list | optional |

## Install

    git clone https://github.com/ronzaum/skills
    cp -r skills/call-analysis ~/.claude/skills/

Then say "analyse this call" or paste a Granola link. The skill asks for anything it needs on the first run. Full instructions: [SKILL.md](SKILL.md).
