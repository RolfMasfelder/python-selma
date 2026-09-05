# Workspace-Pfade-Analyse (workspace_dir-Change)

Stand: 2026-09-05 — Analyse FERTIG; Umsetzung schrittweise (eine Datei pro Step; 2026-09-04: setup.py, agent_runtime.py, heartbeat.py, runtime.py ✅; 2026-09-05: resource_loader.py ✅)

## Festlegung

- kein neuer Bezeichner `workspace_dir`. Es wird nur semantisch `workspace` in Beschreibungen/Kommentaren benutzt um `<root>/.selma/workspace` zu Beschreiben, aber **nicht** als Argument/Variable.

- keine Klassenvariable self._root, stattdessen: self._cwd = Projekt-Root (Root) und Helper `get_workspace(cwd)` liefert `<root>/.selma/workspace`.

- Sessions liegen derzeit in `<root>/.selma/agents/main/sessions/` Verwendung muss geklärt werden (z. B. `agent_runtime.py:SessionFactory`).

- State-Dateien (selma.json, memory.db, memory_index.db) liegen in `<root>/.selma/` (State-Dir). `resolve_state_dir(cwd)` liefert den Pfad. bisher sind
  - `<root>/.selma/memory.db`
  - `<root>/.selma/memory_index.db`
nicht zu sehen! Warum?

- Home-Fallback: `<home>/.selma/` wird NICHT mehr benutzt, da `SELMA_STATE_DIR` gesetzt ist. (siehe `resolve_state_dir`)

- Multi-Agent: `AgentInfo.workspace` ist derzeit ungenutzt. Semantik klären (z. B. `runtime.cwd = <root>/<agent.workspace>` bei Multi-Agent). FUnktion von Multi-Agent ist derzeit unklar, da `AgentInfo.workspace` ungenutzt ist. Rolf: entfernen oder Semantik definieren? Zustand beibehalten bis Klärung erfolgt.

## Ausgangslage

- Neu: `workspace` (=`cwd`, Default `"."`) ist **projektcwd** (Root),
  nicht mehr `.selma/workspace`. Intent-Beleg: `runtime.py:369`.
- Agent schreibt damit **relativ zum Root** (z. B. `.selma/workspace/memory/today.md`).
- Ziel: jede `.selma`- bzw. workspace-Auflösung über die Helper
  `resolve_state_dir(cwd)` / `get_workspace(cwd)` (`helper.py`) —
  **eine** Stelle mit SELMA_STATE_DIR-/Home-Fallback-Logik.

## 2 Pfadebenen (Konzept)
- **STATE_DIR** (`<root>/.selma`): selma.json, sessions/, memory.db, memory_index-DB
  → `resolve_state_dir(cwd)` (SELMA_STATE_DIR → `<cwd>/.selma` → `~/.selma`)
- **WORKSPACE** (`<root>/.selma/workspace`): MY/AGENTS/SOUL/IDENTITY/USER/TOOLS/MEMORY/HEARTBEAT/BOOTSTRAP.md, memory/, skills/, CODING_TOOLS.md
  → `get_workspace(cwd)`

## Bugklassen
- **A) HARTE VERKETTUNG** `Path(cwd)/".selma"/"workspace"` (ignoriert SELMA_STATE_DIR + Home-Fallback)
- **B) DOPPELTER PFAD** `workspace_dir"/.selma/workspace"` wenn workspace_dir schon = Workspace (oder umgekehrt, wenn es Root ist → hier B'=`Root/.selma/workspace` OK, aber nur per Zufall)
- **C) CWD-RELMTIVE STATE-PFADE** `".selma/selma.json"` etc. relativ zum **Prozess-CWD** statt Projekt-Root
- **D) STALE-KOMMENTARE/DOCSTRINGS/AGENT-TEXT** (funktioniert, aber irreführend oder für Agent falsch)

## FINDELISTE (alle, sortiert nach Datei)

### src/selma/resource_loader.py
| Zeile | Typ | Code | Fix |
|---|---|---|---|
| 31 | A ✅ | ~~`self._workspace = Path(cwd) / ".selma" / "workspace"`~~ → `Path(get_workspace(cwd))` (2026-09-05) |
| 85 | B ✅ | ~~`self._workspace / ".selma" / "workspace" / "BOOTSTRAP.md"`~~ — jetzt `self._workspace / "BOOTSTRAP.md"` (2026-09-05) |

### src/selma/my_resource_loader.py
| Zeile | Typ | Fix |
|---|---|---|
| 19 | A (`self._cwd / ".selma/workspace/CODING_TOOLS.md"`) | `get_workspace(cwd) / "CODING_TOOLS.md"`; Docstring L13-14 (sagt `<cwd>/CODING_TOOLS.md`) anpassen |

### src/selma/skills.py
| Zeile | Typ | Anmerkung |
|---|---|---|
| 30 | B/B' | `workspace_dir/".selma"/"workspace"/"skills"` — semantik-abhängig. **Entscheidung:** `find_skill_files`, `get_skills_snapshot_version`, `build_skill_snapshot` nehmen **cwd/Root** und nutzen intern `get_workspace(cwd)/"skills"`. Aufrufer (`command_manager.py:338,375`) geben dann `self._cwd` statt `get_workspace(self._cwd)` durch → Dopplung weg. |
| 29/72/85 | D | Docstrings `<workspace>/.selma/workspace/skills` → `<state-dir>/workspace/skills` |

### src/selma/command_manager.py
| Zeile | Typ | Fix |
|---|---|---|
| 338-339, 375-376 | (Konsum von skills.py) | nach skills.py-Entscheidung: `find_skill_files(self._cwd)` |
| 394 | C | `Path(".selma/selma.json")` (prozess-CWD!) → `resolve_state_dir(self._cwd)/"selma.json"` (konsistent zu config.py:197) |

### src/selma/memory_index.py
| Zeile | Typ | Fix |
|---|---|---|
| 122 | B'/A-mix | `self._workspace / ".selma" / "memory.db"` bei `self._workspace = Path(arg).resolve()`. Neu: `self._root = Path(cwd)`; `self._db_path = resolve_state_dir(self._root) / "memory.db"`; Arg rename `workspace_dir → cwd`. **Wichtig:** `state_dir != cwd/.selma` nur bei Fallback — `resolve_state_dir` deckt HOME-FALLBACK ab, der aktuell **kaputt** wäre (`~/.selma/workspace/.selma`? etc.). |
| 96, 102 | D | Docstring-Beispiele `workspace_dir=".selma/workspace"` → `cwd="."` |
| 12 `self._workspace`-Semantik | — | intern umbenennen in `self._root` (oder `self._cwd`), DB via Helper; evtl. `self._workspace = get_workspace(self._root)` falls sonst gebraucht (Prüfung: nur `_db_path`?) |
| 516 | Hybrid-Heuristik | `get_memory_index(cwd)`: Normalisierung `+ /.selma/workspace` entfernen → Cache-Key auf `Path(cwd).resolve()`; `MemoryIndex(cwd, ...)` |
| 17 | D | Docstring „→ .selma/memory.db (outside workspace…)" → STATE_DIR formulieren |

### src/selma/runtime.py
| Zeile | Typ | Fix/Notiz |
|---|---|---|
| 124 | Legacy ✅ | ~~`agent_dir: str = ".selma"` — **niendlesend**~~ — entfernt (grep: keine Referenzen). |
| 367-369 | OK | `workspace_dir = runtime.cwd` ✅ (neu) |
| 373, 376 | OK | `get_workspace(runtime.cwd)` ✅ (schon Helper) |
| 659 | A/B ✅ | ~~`cwd = str(Path(opts.workspace_dir).parent.parent)`~~ — jetzt: `cwd = opts.workspace_dir` (=Root, direkt durchreichen an `memory_flush`). |
| 831-833 | D/A-Residue ✅ | ~~Kommentar „workspace_dir is <root>/.selma/workspace"~~ — aktualisiert auf „workspace_dir ist der Projekt-Root"; auskommentierte Alt-Zeile `cwd = …parent.parent` entfernt. `ResourceLoader(cwd=workspace_dir)` bleibt korrekt. |
| 1073, 1091, 1108 | OK | `os.path.abspath(opts.workspace_dir)`, `create_selma_tools(opts.workspace_dir, ...)`, `cwd=opts.workspace_dir` — alle nehmen neu Root ✅ (Tools erhalten dann Root als cwd; tools.py-Texte unten beachten) |

### src/selma/agent_runtime.py
| Zeile | Typ | Fix |
|---|---|---|
| 117-118 | D (stale) ✅ | ~~Kommentar „workspace_dir ist IMMER .../.selma/workspace" — **falsch**. Neu: workspace_dir=Root; Sessions-Datei `resolve_state_dir(workspace_dir)/"sessions"/f"{key}.jsonl"`. |
| ~119 | B ✅ | ~~`session_file = Path(workspace_dir).parent/"sessions"/…`~~ — bei workspace_dir=Root: `Path("..")` → **kaputt**. → `resolve_state_dir(workspace_dir)/"sessions"/f"{key}.jsonl"` |
| (SessionFactory-Arg-Nutzung) | ok | `CreateSessionOptions(cwd=workspace_dir)` → Root-CWD ✅ (Agent bekommt Root) |
| 195 (`hb_path = Path(workspace_dir)/"HEARTBEAT.md"`) | B ✅ | ~~**HEARTBEAT.md liegt im WORKSPACE, nicht in Root!**~~ — jetzt: `get_workspace(workspace_dir)/"HEARTBEAT.md"` (light_context-Pfad). |
| 205-206 | OK ✅ | ~~`cwd = str(Path(workspace_dir).parent.parent)`~~ — jetzt: `cwd=workspace_dir` (Root), `ResourceLoader(cwd=cwd)` ✅ |

### src/selma/session_store.py
| Zeile | Typ | Fix |
|---|---|---|
| 71 | D | Kommentar „Here: .selma/sessions/…“ → STATE_DIR formulieren |
| 103 | C | `store_path: str = ".selma/sessions-store.json"` (Pydantic-Default = **Prozess-CWD** relativ). Neu: Default entfernen/`None`; bei Init mit `cwd` `resolve_state_dir(cwd)/"sessions-store.json"`. (Prüfung: wer setzt `store_path`? Gateway/CLI/Cmd-Manager?) |

### src/selma/tools.py
| Zeile | Typ | Fix |
|---|---|---|
| 265-267, 267-272 | D/B | memory_tool: Docstring „cwd is workspace directory itself (e.g. .selma/workspace)" — **stale**; Code `memory_dir = cwd + "/.selma/workspace"` — bei neuem cwd=Root **zufällig korrekt**, aber A (hart). Neu: `memory_dir = get_workspace(cwd)`; Docstring: cwd=Root. |
| 315 | D (Agent-Text) | Tool-Schema-Beispiel `".selma/workspace/MEMORY.md"` ✅ (relativ zu Root), Wortlaut „relative to workspace" → „relative to your working directory (project root)" klarstellen. |
| 343 | D | memory_search-Tool Docstring „cwd is the workspace directory (same convention as memory_get)" → „project root", Konsistenz mit get_memory_index-Call. |
| 360 | OK | `get_memory_index(cwd, ...)` — Arg-Semantik Root → passt (sobald memory_index normalisiert). |

### src/selma/setup.py
| Zeile | Typ | Fix |
|---|---|---|
| 56-59 | OK | `base_path/.selma[/workspace]` ✅ |
| 87 | **B (real bug)** ✅ | ~~`memory_dir = workspace_dir / ".selma/workspace/memory"` → doppelte Nestung~~ — jetzt: `workspace_dir / "memory"` (Test angepasst). |
| 92/94 ✅ | D (ok) | Log-Zeilen pfad-OK für User, semantik ok (relativ zu base) — unverändert. |

### src/selma/heartbeat.py
| Zeile | Typ | Fix |
|---|---|---|
| 207 | arg-Ok | `run_heartbeat_turn(config, workspace_dir)` — mit neuem Root ✅ (Caller gateway.py:42 = `"."`) |
| 223 | ok | `RuntimeEnv(cwd=workspace_dir)` (Root) ✅ |
| 261 | **B** ✅ | ~~`hb_path = Path(workspace_dir) / "HEARTBEAT.md"`~~ — jetzt: `Path(get_workspace(workspace_dir)) / "HEARTBEAT.md"`. |
| 83-88 | prüfen ✅ | `is_heartbeat_content_effectively_empty()` ist pfad-frei (nimmt nur Content) — keine Änderung nötig. |

### src/selma/dashboard.py
| Zeile | Typ | Fix |
|---|---|---|
| 59 | C | `CONFIG_FILE = ".selma/selma.json"` prozess-relativ. → bei Load `resolve_state_dir("./").selma.json` (oder `resolve_state_dir(os.getcwd())`); Streamlit-Start-CWD klären |

### src/selma/config.py
| Zeile | Typ | Notiz |
|---|---|---|
| 72 | — | `AgentInfo.workspace: str = "."` — wer liest das? (grep leer) → **Unused? Kandidat für Deletion oder Semantik-Verdrahtung** (z. B. `runtime.cwd = <base>/<agent.workspace>` bei Multi-Agent). **Offen: Rolf bestätigt?** |
| 197 | OK | `resolve_state_dir(cwd)/"selma.json"` ✅ Muster |

### src/selma/system_prompt.py
| Zeile | Typ | Fix |
|---|---|---|
| 110 | ok | `workspace_dir: str` Feld (neu = Root) ✅ |
| 684 | ok | „Your working directory is: {params.workspace_dir}" ✅ |
| 687-690 | ok-D | Beispiel `.selma/workspace/memory/today.md` ✅ (relativ zu Root); Text-Teile mit `Path(workspace_dir).name` = „selma", `.parent.name` = „workspace" — **`.parent.name`-Ban ist jetzt überflüssig (Workspace-Name nie Root-Name)** → Text vereinfachbar, aber harmlos |

### src/selma/gateway.py
| Zeile | Typ | Status |
|---|---|---|
| 42 | ok | `heartbeat_loop(config, ".", queue)` ✅ |
| (andere workspace_Nutzung?) | — | grep: nur Kommentar L16 ✅ |

### src/selma/my_tools.py, agent_session.py, agent.py
grep-Check: keine `.selma`-Pfade, keine `parent.parent` außer `agent_session.py:150/226` = `entry.parent_id` (Session-Tree, KEIN Pfad) ✅. `my_tools.py:255` = `resolved.parent.mkdir` (unabhängig) ✅.

## NUTZUNGSMUSTER (Vorschlag)
1. **Domänen-APIs** (`MemoryIndex`, `ResourceLoader`, `find_skill_files`, `build_skill_snapshot`, `get_skills_snapshot_version`, `SessionFactory`, `heartbeat.run_*`, `memory_search_tool`, `setup`) bekommen **immer `cwd` (= Projekt-Root)**.
2. **STATE-Dateien**: `resolve_state_dir(cwd) / "<file>"`.
3. **WORKSPACE-Dateien**: `get_workspace(cwd) / "<rel>"`.
4. **Kein** `.selma/workspace` mehr in src-*Code* (nur noch in Helper-Definition + Agent-facing Beispiel-Text in system_prompt.py + Log-Ausgaben).
5. **Kandidaten für Aufräumung** (Option): `runtime.agent_dir` (legacy, unbenutzt), `config.agent.workspace` (unbenutzt — Semantik klären), alt-kommentierte `cwd = str(Path(workspace_dir).parent.parent)` in runtime.py:832.

## RISIKEN / OFFEN
- `AgentInfo.workspace` unlesend → Rolf: entfernen oder Semantik definieren?
- `session_store.py` `store_path`-Default: wer überschreibt das? (gateway/cli) — bevor Default geändert wird kurz prüfen.
- Tests bauen auf alten Konventionen (v. a. `tests/unit/test_unit_memory.py`, `test_unit_skills.py`, `test_unit_dashboard.py`, `test_unit_setup.py`, `test_unit_helper.py`). Nach src-Länder: Tests anpassen + evtl. Coverage-Todo-Pipeline pausieren.
- `MEMORY.md`/daily-note verweisen auf `.selma/workspace/memory/…` — nur in MEMORY.md-Beispieldoku, nicht code.

## Umsetzungs-Plan (nächste Sprints, Reihenfolge)
1. **Hilfs-Hardener:** `helper.py` (unberührt) — keine Änderung nötig; evtl. Convenience-Wrapper `get_state_file(name, cwd)`, `get_workspace_file(rel, cwd)` optional.
2. **Kritische Bugs fixen** (B + C):
   a. `setup.py:87` (Setup bricht/doppelt erstellt)
   b. `agent_runtime.py` (SessionFactory sessions-dir, HEARTBEAT.md light-Pfad)
   c. `heartbeat.py:261` (HEARTBEAT.md-Pfad)
   d. `resource_loader.py:85` (BOOTSTRAP.md doppel)
   e. `skills.py`+`command_manager.py` (skills doppel)
   f. `command_manager.py:394`, `dashboard.py:59`, `session_store.py:103` (CWD-relativ → resolve_state_dir)
3. **A** (harte Verkettung): `resource_loader.py:31`, `my_resource_loader.py:19`, `tools.py:272`
4. **memory_index.py**: Arg rename + `resolve_state_dir` + `get_memory_index`-Heuristik auflösen + docstrings
5. **D** (Docstrings/Texte): alle in Findelist; `system_prompt.py`-Text optional vereinfachen
6. **Tests** nachziehen, ggf. `agent.session_store`-Init-Pfad im gateway/cli mit `resolve_state_dir`
7. **Aufräumtage**: `runtime.agent_dir`, `config.agent.workspace`, alte „parent.parent"-Kommentare
8. **MEMORY.md** Notiz: „Alle state/workspace-Pfade via helper.py; workspace_dir=Root".
