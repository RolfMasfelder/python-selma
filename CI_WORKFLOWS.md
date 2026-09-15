# CI-Workflow-Entscheidung

Kontext: Nach dem Lösen des Forks von `gkvoelkl/python-selma` (siehe Git-Historie /
Chat vom 2026-09-15) wurde geprüft, welche GitHub-Actions-Workflows für dieses
Repo sinnvoll sind.

## Repo-Fakten (Stand 2026-09-15)

- Repo ist public (`fork: false`, `visibility: public`) → GitHub Actions laufen
  ohne Minuten-Limit-Problem.
- Lint/Format bereits lokal über `ruff` + `.pre-commit-config.yaml` abgedeckt.
- `tests/unit/` ist vollständig gemockt (kein echtes LLM, kein echter Browser —
  `tools.sync_playwright` wird in Tests gepatcht), läuft also ohne Secrets/Ollama.
- `tests/integration/` ist laut `tests/README.md` noch weitgehend leer, aber CI-tauglich.
- `tests/scripts/` sind manuelle Live-Skripte (brauchen laufendes Ollama) — nicht CI-tauglich.
- Ziel laut `TEST_COVERAGE_TODO.md`: ≥ 80 % Coverage, aktuell noch nicht überall erreicht.

## Ursprünglich erwogene Workflows

1. `ci.yml` — ruff (Lint + Format-Check) + `pytest tests/unit tests/integration --cov`
2. `pre-commit.yml` — `pre-commit run --all-files`
3. `bandit.yml` — SAST-Scan (`bandit -r src/selma`)
4. `codeql.yml` — GitHub CodeQL
5. `dependabot.yml` — automatische Dependency-Update-PRs

## Warum nicht alle 5 sofort?

- **1 vs. 2 (Redundanz):** `pre-commit.yml` würde dieselben ruff-Checks wie `ci.yml`
  ein zweites Mal ausführen → doppelte Laufzeit, doppelte rote Kreuze für denselben
  Fehler. Nur einen der beiden Wege pflegen, nicht beide parallel.
- **3 (bandit):** Bei einem Agent-Projekt mit Shell-/Browser-Tools (`my_tools.py`,
  `tools.py`) ist mit vielen False Positives zu rechnen. Ohne vorherige Baseline /
  `# nosec`-Pflege ist der Job von Anfang an rot und wird ignoriert (Alert-Fatigue).
- **4 (CodeQL):** Lange Laufzeit, erzeugt Security-Alerts im "Security"-Tab, die
  aktiv gepflegt werden müssen. Für ein Solo-/Kleinteam-Projekt ohne Compliance-
  Vorgabe aktuell geringer Zusatznutzen gegenüber ruff + bandit.
- **5 (Dependabot):** Viele Dependencies (`openai`, `playwright`, `fastapi`,
  `opentelemetry-*` …) → ohne `groups:`-Bündelung entstehen viele PRs pro Woche,
  die schnell veralten/verwaisen, wenn niemand sie zeitnah mergt.
- **Generell:** Alle 5 auf einmal bedeuten mehr YAML-Dateien und mehr rote Kreuze
  zu triagieren, während gerade ein Coverage-Refactor läuft (`REFACTOR_TODO.md`,
  `TEST_COVERAGE_TODO.md`). Das lenkt vom eigentlichen Ziel ab.

## Entscheidung

Nur **`ci.yml`** wird jetzt eingeführt (Lint via ruff + Unit-/Integrationstests via
pytest mit Coverage-Report). Pfad: `.github/workflows/ci.yml`.

Nicht umgesetzt (bewusst zurückgestellt):

- `pre-commit.yml` — verworfen, redundant zu `ci.yml`.
- `bandit.yml`, `dependabot.yml` — später nachziehen, sobald eine bewusste
  Baseline/Gruppierung existiert (Bandit-Ausnahmen bzw. Dependabot-`groups:`).
- `codeql.yml` — optional, nur bei konkretem Security-/Compliance-Bedarf.
