"""Kontoauszüge (PDF) einlesen: Zeilen-Gruppierung, Transaktionen, Saldo-Check.

Teil 2/3 der Kontoauszug-Verarbeitung (Runde 2026-10-15):
  * konto_scanner.py — dieses Modul: PDF → FileResult/Tx
  * konto_output.py  — FileResult/Tx → ODS / CSV
  * konto_auszuege.py — CLI + Orchestrierung (Eintragspunkt)

Geprüft für: Kreissparkasse Mayen, Giro-Auszüge 2026 (Konto MY 198401192).
Robustheit:  Zeilen werden per Wort-Positionen gruppiert. Transaktion = Zeile,
die links mit Datum TT.MM.JJJJ beginnt (Abspalter-Fall: erste Ziffer als
eigenes Wort). Betrag = rechtsbündiges Zahlenwort. Pro Datei: Existenz,
Dateigröße, Seitenzahl, Summen-Check (Start + Σ = Endstand). Fehlgeschlagene
Dateien brechen nichts ab, sondern landen im Status-Blatt.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import pdfplumber
except ImportError as err:
    raise SystemExit("pdfplumber fehlt.  Installation:  pip install pdfplumber") from err

log = logging.getLogger("konto")

DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}")
DATE_PART_RE = re.compile(r"^\d{1,2}\.\d")  # Absplitter: '2' + '1.04.2026…'
AMT_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d{2})$")
X_LEFT = 100.0  # Transaktionszeilen beginnen links
X_DATE_DIGIT = 75.0  # erste Ziffer des Datums
X_AMT_MIN = 250.0  # Beträge stehen rechts
ROW_TOL = 2.5  # y-Toleranz (pt) für Zeilen-Gruppierung


@dataclass
class Tx:
    """Eine Buchung."""

    date: str  # TT.MM.JJJJ
    first: str  # erstes Wort nach dem Datum (z. B. „SEPA-Basis-LS")
    detail: str  # Detail-Zeilen der Buchung
    betrag: Decimal  # Vorzeichen: negativ = Auszahlung
    page: int


@dataclass
class FileResult:
    """Ergebnis für genau eine Auszugs-Datei (ok oder Fehler)."""

    file: Path
    ok: bool = True
    error: str | None = None
    txs: list[Tx] = field(default_factory=list)
    saldo_start: Decimal | None = None
    saldo_start_dt: str | None = None
    saldo_end: Decimal | None = None
    saldo_end_dt: str | None = None
    account: str | None = None
    iban: str | None = None
    bank: str | None = None
    check: str = "-"

    def total(self) -> Decimal:
        return sum((t.betrag for t in self.txs), Decimal(0))

    def validate(self) -> None:
        s, e = self.saldo_start, self.saldo_end
        if s is None or e is None:
            self.check = "Kein Start-/End-Stand gefunden"
            return
        calc = s + self.total()
        diff = calc - e
        if abs(diff) <= Decimal("0.01"):
            self.check = "OK"
        else:
            self.check = f"Δ={diff:+,.2f}  (Start {s:,.2f} + Σ {self.total():,.2f} = {calc:,.2f} ≠ Ende {e:,.2f})"


# --- PDF-Leser ---------------------------------------------------------------


def _rows(page) -> list[tuple[float, list]]:
    """Worte in visuelle Zeilen gruppieren → [(top, [Wörter nach x0]), …]."""
    acc: dict[float, list] = {}
    for w in page.extract_words():
        for k in acc:
            if abs(k - w["top"]) <= ROW_TOL:
                acc[k].append(w)
                break
        else:
            acc[w["top"]] = [w]
    for r in acc.values():
        r.sort(key=lambda w: w["x0"])
    return sorted(acc.items(), key=lambda kv: kv[0])


def _amt(s: str) -> Decimal | None:
    if not AMT_RE.match(s):
        return None
    try:
        return Decimal(s.replace(".", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _header(pdf) -> tuple[str | None, str | None, str | None]:
    """(Bank, Kontonummer, IBAN) aus den Kopfzeilen der ersten Seiten."""
    bank: str | None = None
    acct: str | None = None
    iban: str | None = None
    for pg in pdf.pages[:2]:
        text = pg.extract_text() or ""
        if iban is None:
            m = re.search(r"([A-Z]{2}\d{2}(?:[\dA-Z]{4}){4,5})", text.replace(" ", ""))
            if m:
                iban = m.group(0)
        if acct is None:
            m = re.search(r"[A-Z]{2,4}-?\s?(?:Giro|Spar)\s*([A-Z0-9]{4,12})\s*,", text)
            if m:
                acct = m.group(1)
        if bank is None:
            for ln in text.splitlines()[:8]:
                if any(k in ln for k in ("Sparkasse", "Bank", "Volksbank", "Kreissparkasse")):
                    bank = ln.strip()
                    break
    return bank, acct, iban


def _saldo_candidate(row: list) -> tuple[Decimal | None, str | None]:
    """Zeile = laufender Kontostand? → (Betrag, Datum) sonst (None, None).

    Monatsabschlusszeilen ('Kontostand/Rechnungsabschluss …') werden ignoriert.
    """
    full = " ".join(w["text"] for w in row)
    if "Kontostand/" in full or "Rechnungsabschluss" in full:
        return None, None
    m = re.search(r"Kontostand\s+am\s+(\d{2}\.\d{2}\.\d{4})", full)
    if not m:
        return None, None
    cands = [w for w in row if AMT_RE.match(w["text"])]
    if not cands:
        return None, None
    return _amt(cands[-1]["text"]), m.group(1)


def _first_saldo(pdf) -> tuple[Decimal | None, str | None]:
    for pi in range(min(2, len(pdf.pages))):
        for _top, row in _rows(pdf.pages[pi]):
            val = _saldo_candidate(row)
            if val[0] is not None:
                return val
    return None, None


def _last_saldo(pdf) -> tuple[Decimal | None, str | None]:
    """Letzte 'Kontostand am …'-Zeile des Dokuments = Endstand."""
    best: tuple[Decimal, str, int, float] | None = None
    for pi in range(len(pdf.pages)):
        for top, row in _rows(pdf.pages[pi]):
            d, dt = _saldo_candidate(row)
            if d is not None and (best is None or (pi, top) > (best[2], best[3])):
                best = (d, dt, pi, top)
    if best:
        return best[0], best[1]
    return None, None


def extract_file(path: Path) -> FileResult:
    """Eine Auszugs-Datei einlesen → FileResult (niemals werfen)."""
    res = FileResult(file=path)

    if not path.is_file():
        res.ok, res.error = False, "Datei nicht vorhanden"
        return res
    if path.stat().st_size == 0:
        res.ok, res.error = False, "leere Datei (0 Bytes)"
        return res
    if path.suffix.lower() != ".pdf":
        res.ok, res.error = False, f"keine PDF-Endung ({path.suffix})"
        return res

    try:
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                res.ok, res.error = False, "leeres PDF (0 Seiten)"
                return res
            if getattr(pdf, "is_encrypted", False) and not pdf.is_encrypted():
                pass  # leeres Passwort funktioniert

            res.bank, res.account, res.iban = _header(pdf)
            res.saldo_start, res.saldo_start_dt = _first_saldo(pdf)

            for pi, page in enumerate(pdf.pages):
                for _top, row in _rows(page):
                    if len(row) < 2:
                        continue
                    first = row[0]
                    # Transaktionszeile: links das Datum — entweder
                    # ('21.04.2026SEPA-Basis-LS') oder mit abgesplitterter
                    # erster Ziffer ('2' + '1.04.2026SEPA-Basis-LS').
                    if first["x0"] <= X_LEFT and DATE_RE.match(first["text"]):
                        date_raw = first["text"]
                        rest = row[1:]
                    elif (
                        len(row) >= 3
                        and first["x0"] <= X_DATE_DIGIT
                        and first["text"].isdigit()
                        and row[1]["x0"] <= X_LEFT
                        and DATE_PART_RE.match(row[1]["text"])
                    ):
                        date_raw = first["text"] + row[1]["text"]
                        rest = row[2:]
                    else:
                        continue
                    amts = [w for w in rest if AMT_RE.match(w["text"]) and w["x0"] > X_AMT_MIN]
                    b = _amt(amts[-1]["text"]) if amts else None
                    if b is None:
                        log.warning(
                            "%s p%d: Zeile ohne Betrag ignoriert: %s",
                            path.name,
                            pi + 1,
                            " ".join(w["text"] for w in row[:3]),
                        )
                        continue
                    date_iso = date_raw[:10]  # TT.MM.JJJJ
                    date_rest = date_raw[10:].strip()  # Festhaftender Text
                    detail_words = [w["text"] for w in rest if not (AMT_RE.match(w["text"]) and w["x0"] > X_AMT_MIN)]
                    detail = re.sub(r"\s+", " ", " ".join(detail_words)).strip()
                    first_word = date_rest or next(iter(detail_words), "")
                    res.txs.append(Tx(date_iso, first_word, detail, b, pi + 1))

            res.saldo_end, res.saldo_end_dt = _last_saldo(pdf)
    except Exception as e:
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"
        if os.environ.get("KONTO_DEBUG"):
            import traceback

            traceback.print_exc()
        return res

    res.validate()
    if not res.txs:
        res.ok = False
        res.error = "keine Transaktionszeilen erkannt (unbekanntes Format?)"
    return res


# --- Dateisammlung -----------------------------------------------------------


def collect_files(dirs: list[Path]) -> list[Path]:
    """Verzeichnisse/Dateien scannen → sortierte PDF-Liste (case-insensitiv)."""
    files: list[Path] = []
    for d in dirs:
        p = Path(d)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for f in sorted(p.iterdir()):
                if f.is_file() and f.suffix.lower() == ".pdf":
                    files.append(f)
        else:
            log.warning("Pfad nicht gefunden: %s", d)
    return files


def scan(dirs: list[Path]) -> list[FileResult]:
    """Gesamte Scanning-Runde: Dateisammlung + Einlesen + Sortierung."""
    files = collect_files(dirs)
    results = [extract_file(f) for f in files]
    now = datetime.now().isoformat(timespec="seconds")
    # (Zeitstempel wird nur für das Info-Blatt gebraucht)
    for r in results:
        r.txs.sort(key=lambda t: (t.page, t.date))
    log.info("Gescannt: %s → %d Datei(en)", now, len(results))
    return results
