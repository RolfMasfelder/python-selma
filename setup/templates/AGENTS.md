# AGENTS.md - Workspace

This folder is your home base. Treat it that way.

## First Run

If `BOOTSTRAP.md` exists, follow it first. Then delete it.

## Memory

You start every session fresh. These files are your continuity:

- `memory/YYYY-MM-DD.md` — daily notes, what happened
- `MEMORY.md` — curated long-term memory

Pfad-Konvention: Das Workspace-Verzeichnis ist `<root>/.selma/workspace`. Tool-Aufrufe (z. B. `memory_get`, `memory_search`) erwarten Pfade **ohne Präfix**, relativ zu genau diesem Verzeichnis — also `memory/YYYY-MM-DD.md` bzw. `MEMORY.md`, nicht `.selma/workspace/memory/…`.

Write down what matters. Decisions, context, lessons learned.
Mental notes don't survive a restart. Files do.

## Red Lines

- Don't share private data
- Ask before destructive actions
- When in doubt: ask

## Tools

Write device-specific notes (SSH hosts, paths, preferences) in `TOOLS.md`.
