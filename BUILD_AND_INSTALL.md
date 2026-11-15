# Selma — Build & Install

Wie man aus dem selma-Repo eine installierbare Version (Wheel) baut und in den
Schwester-Projekten (die selma als Bibliothek nutzen) installiert bzw. aktualisiert.

Repo: das selma-Repo (eigene venv, git).
Schwester-Projekte: je ein eigenes `venv/`, selma wird dort per Wheel installiert.

---

## 1. Wheel bauen

```bash
cd ~/workspace/selma
source venv/bin/activate          # Projekt-venv (TOOLs.md: IMMER erst aktivieren)

# Vor dem Build: saubere, getestete Basis (optional, aber empfehlenswert)
python -m pytest tests/unit tests/integration -q
ruff check src/selma tests && ruff format --check src/selma tests
mypy src/selma
git status --short                # nur erwartete Änderungen

pip wheel . -w dist/ --no-deps    # baut dist/selma-<VERSION>-py3-none-any.whl
ls -la dist/                      # → selma-0.2.0-py3-none-any.whl (Beispiel)
```

`--no-deps`: Das Wheel enthält nur selma selbst, keine der Laufzeit-Abhängigkeiten
(pip etc. bleibt beim Install in den Projekten unverändert — das ist intentional, die
Abhängigkeiten sind dort bereits im jeweiligen venv).

Die Version kommt aus `pyproject.toml` → `[project] version = "0.2.0"` (aktuell).

---

## 2. In ein Schwester-Projekt installieren / aktualisieren

**Achtung — wichtig:** Die Wheel-Version und die bereits installierte Version
identisch (z. B. beide `0.2.0`, nur der Inhalt neu) → ein normales `pip install`
würde **"Requirement already satisfied"** melden und NICHTS aktualisieren.
Deshalb **`--force-reinstall`** bei Updates.

```bash
PROJ=~/workspace/<projekt>
SELMA_WHL=~/workspace/selma/dist/selma-0.2.0-py3-none-any.whl

$PROJ/venv/bin/pip install --force-reinstall --no-deps $SELMA_WHL
#  →  Successfully uninstalled selma-0.2.0
#  →  Successfully installed selma-0.2.0
```

- `--force-reinstall` → überschreibt die gleich-versionierte Installation mit dem
  neuen Wheel-Inhalt (sonst "already satisfied", Code wird NICHT getauscht).
- `--no-deps` → die vorhandenen Laufzeit-Abhängigkeiten im venv bleiben unangetastet
  (sonst würde pip die Dependencies neu auflösen/überprüfen — bei `--force-reinstall`
  sogar neu installieren; das soll hier nicht passieren).

Neue/frische Installation (noch kein selma im venv):

```bash
$PROJ/venv/bin/pip install --no-deps $SELMA_WHL
```

---

## 3. Verifikation im Projekt

```bash
$PROJ/venv/bin/python -c "
import selma
from selma.agent import Agent
assert hasattr(Agent, '_ensure_user_query'), 'Guard fehlt — altes selma?'
print('selma-OK', selma.__file__)
"
```

- Ausgabe zeigt den **installierten Ort** (muss unter `<projekt>/venv/.../site-packages/selma` sein).
- `Guard OK` bzw. die `assert` bestätigen, dass der aktuelle Stand (mit Qwen-Guard)
  installiert ist und nicht eine ältere Version.

Audio-Schnittstelle (nur wenn im Projekt `piper`/`faster-whisper` installiert sind):

```bash
$PROJ/venv/bin/python -c "import piper, faster_whisper; print('audio-OK')" \
    2>/dev/null || echo "audio nicht installiert (ok, optional)"
```

---

## 4. Alle vier Schwester-Projekte auf einmal aktualisieren

'Schwester-Projekte' sind Projekte parallel zu selma, also z. B. `~/workspace/<a>`, `~/workspace/<b>` etc., in denen selma installiert ist, aber als verschiedene Personas auf unterschiedliche Aufgaben spezialisiert sind. Jedes Schwester-Projekt kann eine vollständig unabhängige selma-Installation haben (z. B. unterschiedliche Audio-Engines, unterschiedliche Konfigurationen).

```bash
cd ~/workspace/selma
WHL=dist/selma-0.2.0-py3-none-any.whl
for d in <a> <b> <c> <d>; do
  echo "=== $d ==="
  ~/workspace/$d/venv/bin/pip install --force-reinstall --no-deps $WHL \
    2>&1 | grep -E "Successfully|ERROR"
done
# Danach je Verifikation aus Schritt 3.
```

---

## Häufige Stolpersteine

| Symptom | Ursache / Fix |
|---|---|
| `Requirement already satisfied` bei Update | gleicher Version-String → **`--force-reinstall`** fehlt |
| Code nicht aktualisiert, Version gleich | wie oben; ohne `--force-reinstall` ersetzt pip gleich-versionierte Pkgs NICHT |
| Abhängigkeiten wurden mit uminstalliert | `--no-deps` vergessen |
| `selma` importiert nicht im Projekt | Wheel wurde ins falsche venv installiert → `~/workspace/<projekt>/venv/bin/pip` statt `pip` aus dem selma-venv |
| `assert 'Guard fehlt'` | altes selma aktiv; Wheel-Version prüfen, `pip show selma` Location lesen |
