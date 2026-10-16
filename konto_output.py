"""Kontoauszüge nach ODS / CSV schreiben.

Teil 3/3 der Kontoauszug-Verarbeitung (Runde 2026-10-15):
  * konto_scanner.py — PDF → FileResult/Tx
  * konto_output.py  — dieses Modul: FileResult/Tx → ODS / CSV
  * konto_auszuege.py — CLI + Orchestrierung (Eintragspunkt)

ODS (primär):
  * 3 Blätter: „Buchungen" / „Status" / „Info"
  * Beträge sind ECHTE numerische Zellen (office:value-type=float,
    office:value in DE-semantischer Schreibweise), rechtsbündig,
    2 Dezimalstellen mit Komma.
  * Wichtiger odfpy-Fallenstein (2026-10-15 via Probe verifiziert):
    odfpy konstituiert NUMBERNS als datastyle-Namespace, aber die
    Attributte (value-type, decimal-places, …) gehören in den ODF-
    standard-Konvention `number:`-Namespace. LibreOffice ignoriert
    datastyle:-Attributte auf number:number → Format wirkt nicht.
    Deshalb handgeschriebene Elemente + setAttrNS, check_grammar=False.

CSV (alternativ): Semikulon + UTF-8 (BOM), öffnet direkt in LibreOffice Calc
und Excel (DE-Einstellungen) ohne Formatierungs-Bugs.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from konto_scanner import FileResult, Tx

# --- Gemeinsame Helfer -------------------------------------------------------


def f2(v: Decimal | None) -> str:
    """Decimal → deutsche Anzeige '1.234,56' (None/'' → '')."""
    if v is None:
        return ""
    s = f"{v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _sum_where(txs: list[Tx], pred) -> Decimal:
    return sum((t.betrag for t in txs if pred(t.betrag)), Decimal(0))


# --- ODS ----------------------------------------------------------------------


def write_ods(results: list[FileResult], out: Path) -> None:
    from odf.element import Element
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.style import ParagraphProperties, Style
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    _OFF = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    NN = "urn:oasis:names:tc:opendocument:xmlns:number:1.0"
    ST = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"

    doc = OpenDocumentSpreadsheet()

    # --- Number-Style (korrektes Namespace! siehe Modul-Docstring) -----------
    # Tausendertrennzeichen: number:grouping + grouping-separator (Rolf-Vorlage
    # 2026-10-16 in konto_auszuege.ods, Stil "N4"; Probe /tmp/probe_grp.ods:
    # Anzeige wird 5.000,00 in LibreOffice-Render — funktioniert, unlike
    # frühere Annahme). csv-Export zeigt roh values (normal, Format ist
    # Zellen-Property, nicht Dateiinhalt).
    numst = Element(qname=(NN, "number-style"), check_grammar=False)
    numst.setAttrNS(ST, "name", "amt_de")
    num = Element(qname=(NN, "number"), check_grammar=False)
    num.setAttrNS(NN, "value-type", "float")
    num.setAttrNS(NN, "decimal-places", "2")
    num.setAttrNS(NN, "min-decimal-places", "2")
    num.setAttrNS(NN, "decimal-separator", ",")
    num.setAttrNS(NN, "min-integer-digits", "1")
    num.setAttrNS(NN, "grouping", "true")
    num.setAttrNS(NN, "grouping-separator", ".")
    numst.addElement(num, check_grammar=False)
    doc.styles.addElement(numst, check_grammar=False)

    # --- Paragraph-Stil: rechtsbündig (für Beträge in Zellen) ----------------
    pAmt = Style(name="pAmt", family="paragraph")
    pAmt.addElement(ParagraphProperties(textalign="end"))
    doc.automaticstyles.addElement(pAmt, check_grammar=False)

    def cell_text(val: object) -> TableCell:
        tc = TableCell()
        tc.addElement(P(text="" if val in (None, "") else str(val)))
        return tc

    def cell_num(v: Decimal) -> TableCell:
        """Echte numerische Zelle: float-Wert + DE-Darstellung + rechtsbündig."""
        tc = TableCell(valuetype="float", value=str(v))
        tc.setAttrNS(ST, "data-style-name", "amt_de")
        p = P(text=f2(v))
        p.setAttrNS(ST, "style-name", "pAmt")
        tc.addElement(p)
        return tc

    def make_header(headers: list[str]) -> TableRow:
        tr = TableRow()
        for h in headers:
            tr.addElement(cell_text(h))
        return tr

    # --- Blatt 1: Buchungen ---------------------------------------------------
    # Kontonummer/IBAN bewusst NICHT hier: in allen Zeilen identisch und auch
    # im „Info"-Blatt — Rolf-Feedback 2026-10-16.
    t1 = Table(name="Buchungen")
    t1.addElement(make_header(["Datum", "Erklärung", "Detail", "Erläuterung", "Betrag EUR", "Soll", "Haben", "Quelle"]))
    amt_cols = {4, 5, 6}
    for r in (x for x in results if x.ok):
        for t in r.txs:
            b = t.betrag
            row = TableRow()
            for i, v in enumerate(
                [
                    t.date,
                    t.first,
                    t.detail,
                    t.note,
                    b,
                    (-b if b < 0 else None),
                    (b if b > 0 else None),
                    r.file.name,
                ]
            ):
                row.addElement(cell_num(v) if i in amt_cols and v is not None else cell_text("" if v is None else v))
            t1.addElement(row)
    doc.spreadsheet.addElement(t1)

    # --- Blatt 2: Status ------------------------------------------------------
    t2 = Table(name="Status")
    t2.addElement(
        make_header(
            [
                "Datei",
                "Status",
                "Transaktionen",
                "Start-Stand",
                "Σ Ein",
                "Σ Aus",
                "End-Stand",
                "Summen-Check",
                "Kontonummer",
                "Bemerkung",
            ]
        )
    )
    amt_cols2 = {3, 4, 5, 6}
    for r in results:
        ein = _sum_where(r.txs, lambda b: b > 0)
        aus = _sum_where(r.txs, lambda b: b < 0)
        status = "ok" if r.ok else f"FEHLER: {r.error or '?'}"
        row = TableRow()
        vals = [
            r.file.name,
            status,
            len(r.txs),
            r.saldo_start,
            ein,
            aus,
            r.saldo_end,
            r.check if r.ok else "",
            r.account or "",
            (f"Start {r.saldo_start_dt}  |  Ende {r.saldo_end_dt}" if r.ok else ""),
        ]
        for i, v in enumerate(vals):
            row.addElement(cell_num(v) if i in amt_cols2 and v is not None else cell_text("" if v is None else v))
        t2.addElement(row)
    doc.spreadsheet.addElement(t2)

    # --- Blatt 3: Info --------------------------------------------------------
    t3 = Table(name="Info")
    banks = sorted({r.bank for r in results if r.bank})
    accts = sorted({r.account for r in results if r.account})
    ibans = sorted({r.iban for r in results if r.iban})
    total_in = sum((_sum_where(r.txs, lambda b: b > 0) for r in results if r.ok), Decimal(0))
    total_out = sum((_sum_where(r.txs, lambda b: b < 0) for r in results if r.ok), Decimal(0))
    bads = sorted({r.file.name for r in results if (not r.ok) or r.check != "OK"})

    rows = [
        ("Quelle (Bank)", "; ".join(banks) if banks else "?"),
        ("Kontonummer", "; ".join(accts) if accts else "?"),
        ("IBAN", "; ".join(ibans) if ibans else "?"),
        ("Dateien gelesen", str(sum(1 for r in results if r.ok))),
        ("Summe Ein", f2(total_in)),
        ("Summe Aus", f2(total_out)),
        ("Problematik", ", ".join(bads) if bads else "keine"),
    ]
    for label, val in rows:
        row = TableRow()
        row.addElement(cell_text(label))
        if label in ("Summe Ein", "Summe Aus"):
            tc = TableCell(valuetype="float", value=val.replace(".", "").replace(",", "."))
            tc.setAttrNS(ST, "data-style-name", "amt_de")
            p = P(text=f2(Decimal(val.replace(".", "").replace(",", "."))))
            p.setAttrNS(ST, "style-name", "pAmt")
            row.addElement(tc)
        else:
            row.addElement(cell_text(val))
        t3.addElement(row)
    doc.spreadsheet.addElement(t3)

    doc.save(str(out))


# --- CSV ----------------------------------------------------------------------


def write_csv(results: list[FileResult], out: Path) -> None:
    """CSV (Semikulon, UTF-8-BOM) — Buchungen + Status-Block darunter."""
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        # --- Buchungen (Kontonummer/IBAN weggelassen, siehe Blatt „Buchungen"
        # im ODS oben — Rolf 2026-10-16) ---
        w.writerow(["Datum", "Erklärung", "Detail", "Erläuterung", "Betrag EUR", "Soll", "Haben", "Quelle"])
        for r in (x for x in results if x.ok):
            for t in r.txs:
                b = t.betrag
                w.writerow(
                    [
                        t.date,
                        t.first,
                        t.detail,
                        t.note,
                        f2(b),
                        f2(-b) if b < 0 else "",
                        f2(b) if b > 0 else "",
                        r.file.name,
                    ]
                )
        w.writerow([])
        # --- Status ---
        w.writerow(["STATUS"])
        w.writerow(["Datei", "Status", "Txn", "Start", "Σ Ein", "Σ Aus", "Ende", "Summen-Check"])
        for r in results:
            ein = _sum_where(r.txs, lambda b: b > 0)
            aus = _sum_where(r.txs, lambda b: b < 0)
            w.writerow(
                [
                    r.file.name,
                    "ok" if r.ok else f"FEHLER: {r.error or '?'}",
                    len(r.txs),
                    f2(r.saldo_start),
                    f2(ein),
                    f2(aus),
                    f2(r.saldo_end),
                    r.check if r.ok else "",
                ]
            )
