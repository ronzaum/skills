# Skills

Skills for AI agents. Each folder is one skill.

| Skill | Runs in | What it does |
|---|---|---|
| [call-analysis](call-analysis/) | Claude Code | Turns a call's Granola transcript, your notes and your [Familiar](https://github.com/familiar-software/familiar) screen captures into a task list (owner, due date, frame), a timeline, prompts for an engineering LLM, and an Obsidian note. |

## Install

    git clone https://github.com/ronzaum/skills

Then copy the skill's folder to where your agent loads skills. For Claude Code:

    cp -r skills/call-analysis ~/.claude/skills/

Each skill asks for anything it needs on its first run.
