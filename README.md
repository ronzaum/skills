# Claude Code skills

Skills for [Claude Code](https://claude.com/claude-code). Each folder is one skill.

| Skill | What it does |
|---|---|
| [call-analysis](call-analysis/) | Turns a call's Granola transcript, your notes and your [Familiar](https://github.com/familiar-software/familiar) screen captures into a task list (owner, due date, frame), a timeline, prompts for an engineering LLM, and an Obsidian note. |

## Install a skill

Copy its folder into `~/.claude/skills/`:

    git clone https://github.com/ronzaum/claude-skills
    cp -r claude-skills/call-analysis ~/.claude/skills/

The skill asks for anything it needs on its first run.
