# Skills

I'm Ron Zaum, Founding Solutions Architect at a YC startup, leading GTM, product, agent logic, systems thinking, customer success and deployments. I build agent skills for the work around all of that, and this repo holds the ones worth sharing. Each folder is one skill, with what it needs and how to install it.

| Skill | Runs in | What it does |
|---|---|---|
| [call-analysis](call-analysis/) | Claude Code | Turns a call's Granola transcript, your notes and your [Familiar](https://github.com/familiar-software/familiar) screen captures into a task list (owner, due date, frame), a timeline, prompts for an engineering LLM, and an Obsidian note. |

## call-analysis requirements

| Tool | Needed for | Swappable |
|---|---|---|
| [Claude Code](https://claude.com/claude-code) | runs the skill | yes, any agent that loads skills and can run Python |
| [Familiar](https://github.com/familiar-software/familiar) | screen captures: timing, what was shared, your notes | no; without it the skill runs text-only |
| [Granola](https://www.granola.ai) | transcript and your notes | yes, any provider with a start time and a transcript |
| [Obsidian](https://obsidian.md) | a copy of the task list | optional |

## Install

    git clone https://github.com/ronzaum/skills

Then copy the skill's folder to where your agent loads skills. For Claude Code:

    cp -r skills/call-analysis ~/.claude/skills/

Each skill asks for anything it needs on its first run.
