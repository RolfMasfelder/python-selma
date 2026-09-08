# CODING_TOOLS.md — Python-Entwicklungsumgebung

Stand: 2026-09-08. Versionsnummern sind Momentaufnahmen; bei Verdacht auf Drift:
`pip list` (venv) / `command -v <tool>` nachschauen.

## Regel

**Bevor ein Tool zum ersten Mal in einer Session genutzt wird:** einmal prüfen,
ob es in der Tabelle unten „installiert" ist. Ist es nicht mehr da → nachinstallieren
und diesen Eintrag aktualisieren. Damit muss Verfügbarkeit nicht *mehrfach* pro
Session entdeckt werden (Vorlage: rg-Entdeckung 2026-09-08 — hatte 8 Tests kaputtgemacht).

## Installiert

venv: `/home/rolf/workspace/selma/venv` → `source venv/bin/activate` bevor Python-Befehle laufen.

| Tool (Version) | Zweck | Aufruf |
|---|---|---|
| python 3.13.14, pip 26.2.1 | Basis | — |
| pytest 9.1.1 + pytest-cov 7.1.0 (coverage 7.15.4) | Tests, Coverage | `python -m pytest tests/... --cov` |
| ruff (0.16.3) | Lint + Format — **einziges** Lint/Format-Tool | `ruff check .` / `ruff format .` |
| pre-commit 4.6.2 (+Hooks: ruff, whitespace, check-yaml/toml, …) | Git-Gate | automatisch bei `git commit` |
| mypy **2.3.1** (neu 2026-09-08) | Statische Typenprüfung — ruff prüft nur Syntax-Stil! | `mypy src/` — *bekannt: 5 arg-types Fehler in runtime.py, nicht neu produzieren* |
| vulture **2.16** (neu 2026-09-08) | Dead-Code-Finder | `vulture src/ --min-confidence 80` |
| bandit **1.9.4** (neu 2026-09-08) | Security-Lint (eval, subprocess, Hardcoded-Secrets) | `bandit -q src/` |
| rg (ripgrep) 14.1.1, `/usr/bin/rg` | Suche — **`my_tools.py`-grep nutzt rg als Primärpfad!** | `rg 'pattern'` |
| tree, jq, ctags | Utility | `tree`, `jq . file.json` |
| psql, docker, git, maturin | System | — |

## Explizit NICHT installiert (bewusste Entscheidung)

- **black** — `ruff format` ersetzt es (`[tool.ruff] line-length = 120`).
- **isort (im venv)** — System-`isort` existiert, ist aber im Projekt nicht im Einsatz
  (ruff regelt Import-Sortierung).
- **pyflakes / pylint / flake8** — ruff deckt das ab; System-`pylint` vorhanden, aber nicht im Workflow.
- **pytest-asyncio, pytest-timeout** — Projekt-Stil (MEMORY.md): sync-Tests + `run(coro)`-Helper,
  Timeouts groß, kein `--timeout`-Flag.
- **uv / poetry / hatch / pipx / tox / nox** — pip + venv reicht für dieses Setup.
- **pyright** — mypy ist die gewählte zweite Ansicht.
- **fd / fzf / bat / shellcheck** — kein interaktiver/Shell-Schwerpunkt im Projekt.

## Typen-/Quality-Gates (ab 2026-09-08 verfügbar)

```bash
source venv/bin/activate
mypy src/                            # Typen-Gate vor größeren Refactors
vulture src/ --min-confidence 80     # nach Dead-Code-Runden
bandit -q src/                       # vor Releases / bei security-relevantem Code
```

Erwartetes Rauschen: `mypy src/` → 5 `arg-type`-Fehler in `src/selma/runtime.py`
(`str | None` vs. `Literal['low','medium','high'] | None`, u. a. `rejected_thinking_level`).
Bestehen lassen, bis runtime.py gezielt refactoring bekommt — nicht „weglinten".

`vulture src/` meldet aktuell 2 Treffer (Confidence ≥80):
`app` in `gateway.py:39` und `yieldMs` in `my_tools.py:690` — vor dem Beseitigen
prüfen, ob sie Teil der Tool-Schema-API sind (dann ignorieren: `vulture` mit `--exclude`
oder `# noqa: vulture` am Platz).

## Falls später gebraucht

| Tool | Wann relevant | Install |
|---|---|---|
| `fd` | schnelle Dateisuche statt `find` | `sudo dnf install fd` |
| `pip-tools` | Requirements-Freeze / Lockdateien | `pip install pip-tools` |
| `tox` / `nox` | Multi-Env-Testmatrix | `pip install tox` |
| `pyright` | zweite Typen-Opinion, wenn mypy zu wenig | `pip install pyright` |
| `tokei` | LOC-Statistik | `pip install tokei` |
| `twine` | PyPI-Release | `pip install twine` (erst beim ersten Release) |
