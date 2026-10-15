#!/usr/bin/env python3
"""Kontoauszüge aus PDF in ODS/CSV exportieren.

Teil 1/3 der Kontoauszug-Verarbeitung (Runde 2026-10-15):
  * konto_scanner.py — PDF-Erkenntnis (Zeilen, Transaktionen, Saldo-Check)
  * konto_output.py  — ODS / CSV-Ausgabe
  * konto_auszuege.py — dieses Modul: CLI + Orchestrierung

Modul-Trennung bewusst klein (Rolf-Anweisung 2026-10-14):
Main / Input-Scanner / Output, gemeinsame Helfer im Scanner-Modul.

Geprüft für: Kreissparkasse Mayen, Giro-Auszüge 2026, Konto MY 198401192
  (9 Monatsauszüge 01.–09., gemischte Endungen .pdf/.PDF).

Aufruf:
    python konto_auszuege.py                        # → ./konto_auszuege.ods
    python konto_auszuege.py -o meinauszug.ods      # eigenes Ziel
    python konto_auszuege.py --csv                  # CSV (Semikolon, UTF-8-BOM)
    python konto_auszuege.py --dir ~/2025           # anderes Verzeichnis
    python konto_auszuege.py --dir datei1.pdf datei2.pdf

Robustheit / Fehlerbehandlung (wichtig, weil später auch andere Jahrgänge):
  * Zeilen werden per Wort-Positionen gruppiert. Transaktion = Zeile, die
    links mit Datum TT.MM.JJJJ beginnt. Betrag = rechtsbündiges Zahlenwort.
    Detailtext-Zeilen, Gläubiger-IDs, „Übertrag:" etc. werden automatisch
    ignoriert → überlebt Formatänderungen im Detailtext.
  * Kontonummer/IBAN/Bank werden aus dem Kontokopf erkannt (Regex),
    nicht hart gecodet.
  * Pro Datei: Existenz, Dateigröße, Seitenzahl, erkannte Transaktionen
    und SUMMEN-CHECK (Start + Σ Beträge muss Endstand ergeben). Jede Datei
    wird einzeln gemeldet (ok / Fehler + Grund). Problematische Dateien
    brechen das Programm NICHT ab; sie landen im Blatt „Status" als
    FEHLER + Grund. Exit-Code: 0 wenn ≥1 Datei ok, sonst 1.

Abhängigkeiten:  pip install pdfplumber odfpy
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from konto_output import write_csv, write_ods
from konto_scanner import FileResult, scan

log = logging.getLogger("konto")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--dir",
        nargs="*",
        default=[Path.cwd()],
        metavar="PFAD",
        help="Pfad(e) mit PDF-Dateien, oder einzelne PDF-Dateien (Default: aktuelles Verzeichnis)",
    )
    ap.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="DATEI",
        help="Ziel-Datei  (Default: ./konto_auszuege.ods  bzw.  .csv bei --csv)",
    )
    ap.add_argument("--csv", action="store_true", help="CSV (Semikolon, UTF-8-BOM) statt ODS")
    ap.add_argument("-v", "--verbose", action="store_true", help="Debug-Logging")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")

    results: list[FileResult] = scan(args.dir)
    if not results:
        log.error("Keine PDF-Dateien gefunden.")
        return 1

    n_ok = sum(1 for r in results if r.ok)
    n_bad = len(results) - n_ok
    n_tx = sum(len(r.txs) for r in results if r.ok)

    for r in results:
        if not r.ok:
            log.error("  %s → FEHLER: %s", r.file.name, r.error)
        elif r.check != "OK":
            log.warning("  %s → Summen-Check: %s", r.file.name, r.check)

    if args.output:
        p = Path(args.output)
        out = p if p.suffix.lower() in {".ods", ".csv"} else Path(str(p) + (".csv" if args.csv else ".ods"))
    else:
        out = Path("konto_auszuege" + (".csv" if args.csv else ".ods"))

    (write_csv if args.csv else write_ods)(results, out)

    log.info("FERTIG: %d Dateien ok, %d Fehler, %d Transaktionen → %s", n_ok, n_bad, n_tx, out)
    if n_bad == len(results):
        log.error("Keine Datei verarbeitet → Exit 1.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
