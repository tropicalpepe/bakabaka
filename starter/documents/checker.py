"""Document content checker. Runs configured rules against extracted units.

All logic is deterministic: no AI, no network access.
"""
import hashlib
import json
import logging
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

PARSER_VERSION = "v1"
MAX_BYTES = settings.MAX_UPLOAD_BYTES
MAX_PDF_PAGES = settings.MAX_PDF_PAGES
MAX_CHARS = settings.MAX_EXTRACTED_CHARS
TIMEOUT = settings.PARSER_TIMEOUT_SECONDS


def validate_container(path: Path) -> str:
    """Validate file and return format string ('pdf' or 'docx'). Raises ValueError."""
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError("file_too_large")

    suffix = path.suffix.lower()
    data = path.read_bytes()

    if suffix == ".pdf":
        if not data[:1024].lstrip().startswith(b"%PDF-"):
            raise ValueError("type_mismatch")
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise ValueError("encrypted_document")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError("too_many_pages")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("invalid_pdf") from exc
        return "pdf"

    if suffix == ".docx":
        if not data[:4] == b"PK\x03\x04":  # ZIP magic bytes
            raise ValueError("type_mismatch")
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                names = {x.filename for x in infos}
                if not {"[Content_Types].xml", "word/document.xml"} <= names:
                    raise ValueError("invalid_docx")
                if len(infos) > 2000:
                    raise ValueError("archive_limit")
                total_decompressed = sum(x.file_size for x in infos)
                if total_decompressed > 100 * 1024 * 1024:
                    raise ValueError("archive_limit")
                for item in infos:
                    parts = item.filename.replace("\\", "/").split("/")
                    if item.filename.startswith(("/", "\\")) or ".." in parts:
                        raise ValueError("archive_path")
                    if item.file_size > 10 * 1024 * 1024:
                        raise ValueError("archive_limit")
                    ratio = item.file_size / max(item.compress_size, 1)
                    if ratio > 300:
                        raise ValueError("archive_limit")
                if any("vbaProject" in n for n in names):
                    raise ValueError("macros_unsupported")
        except zipfile.BadZipFile as exc:
            raise ValueError("invalid_docx") from exc
        return "docx"

    raise ValueError("unsupported_type")


def extract_units(path: Path) -> dict:
    """Extract normalized text units from PDF or DOCX.

    Returns {format, units, parser_version, limitations}.
    Each unit: {id, kind, page, text, heading, font_sizes}.
    """
    fmt = validate_container(path)
    units = []
    limitations = []

    if fmt == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        for idx, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            units.append({
                "id": f"pdf:{idx}",
                "kind": "pdf_page",
                "page": idx,
                "text": text,
                "heading": False,
                "font_sizes": None,
            })
        if not any(u["text"].strip() for u in units):
            raise ValueError("text_layer_unavailable")

    else:  # docx
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(str(path))

        def _style_size(style):
            seen = set()
            while style is not None and style.style_id not in seen:
                seen.add(style.style_id)
                if style.font.size is not None:
                    return style.font.size.pt
                style = style.base_style
            return None

        def _effective_size(run, paragraph):
            if run.font.size is not None:
                return run.font.size.pt
            for st in (run.style, paragraph.style):
                sz = _style_size(st)
                if sz is not None:
                    return sz
            nodes = doc.styles.element.xpath("./w:docDefaults/w:rPrDefault/w:rPr/w:sz")
            if nodes:
                return float(nodes[0].get(qn("w:val"))) / 2
            return None

        para_idx = 0
        table_idx = 0

        for element in doc.element.body:
            tag = element.tag
            if tag == qn("w:p"):
                para_idx += 1
                p = Paragraph(element, doc)
                name = p.style.name.casefold()
                is_heading = name.startswith("heading") or name in ("title", "subtitle")
                font_sizes = [_effective_size(r, p) for r in p.runs if r.text.strip()]
                units.append({
                    "id": f"p:{para_idx}",
                    "kind": "paragraph",
                    "page": None,
                    "text": p.text,
                    "heading": is_heading,
                    "font_sizes": font_sizes,
                })
            elif tag == qn("w:tbl"):
                table_idx += 1
                t = Table(element, doc)
                seen_cells = set()
                for r_idx, row in enumerate(t.rows, 1):
                    for c_idx, cell in enumerate(row.cells, 1):
                        if cell._tc in seen_cells:
                            continue
                        seen_cells.add(cell._tc)
                        for p_idx, p in enumerate(cell.paragraphs, 1):
                            name = p.style.name.casefold()
                            is_heading = name.startswith("heading")
                            font_sizes = [_effective_size(r, p) for r in p.runs if r.text.strip()]
                            units.append({
                                "id": f"table:{table_idx}:row:{r_idx}:cell:{c_idx}:p:{p_idx}",
                                "kind": "table_cell",
                                "page": None,
                                "text": p.text,
                                "heading": is_heading,
                                "font_sizes": font_sizes,
                            })

    total_chars = sum(len(u["text"]) for u in units)
    if total_chars > MAX_CHARS:
        raise ValueError("extracted_text_limit")

    limitations.append(
        "Demonstrācijas noteikumi; nav plaģiāta vai pilna noformējuma pārbaude."
    )

    return {
        "format": fmt,
        "units": units,
        "parser_version": PARSER_VERSION,
        "limitations": limitations,
    }


def _normalize(text: str) -> str:
    return " ".join(text.casefold().strip().split())


def run_checks(extraction: dict, ruleset: dict) -> dict:
    """Run all configured rules. Returns {rule_results, findings}."""
    units = extraction["units"]
    fmt = extraction["format"]
    lines = [(u, line.strip()) for u in units for line in u["text"].splitlines() if line.strip()]

    bibliography_aliases = {"izmantotā literatūra", "literatūras saraksts"}
    bibliography_index = next(
        (i for i, (_, line) in enumerate(lines) if _normalize(line) in bibliography_aliases),
        None,
    )
    body = lines if bibliography_index is None else lines[:bibliography_index]
    bibliography = [] if bibliography_index is None else lines[bibliography_index + 1:]

    rule_results = {}
    findings = []

    def _finding(rule, unit, evidence, explanation, action):
        findings.append({
            "rule_id": rule["id"],
            "severity": rule["severity"],
            "location": {
                "unit_id": unit["id"] if unit else "document",
                "page": unit.get("page") if unit else None,
            },
            "evidence": evidence,
            "explanation": explanation,
            "action": action,
        })

    for rule in ruleset.get("rules", []):
        kind = rule["kind"]
        rule_id = rule["id"]
        outcome = "passed"
        explanation = "Pārbaude izpildīta."

        if kind == "required_heading":
            aliases = {_normalize(a) for a in rule["aliases"]}
            found = any(_normalize(line) in aliases for _, line in lines)
            if not found:
                outcome = "failed"
                explanation = f"Nav atrasta nepieciešamā sadaļa: {rule['aliases'][0]}."
                _finding(rule, None, "", explanation, f"Pievienojiet sadaļu ar virsrakstu: {rule['aliases'][0]}")

        elif kind == "numeric_references":
            cited = [(u, n) for u, line in body for n in re.findall(r"\[(\d+)\]", line)]
            entries = [n for _, line in bibliography for n in re.findall(r"^\[(\d+)\]", line)]
            if not cited:
                outcome = "not_assessed"
                explanation = "Tekstā nav skaitlisko atsauču."
            else:
                for unit, n in cited:
                    count = entries.count(n)
                    if count != 1:
                        outcome = "failed"
                        explanation = "Atsaucei nav viennozīmīga literatūras ieraksta."
                        _finding(rule, unit, f"[{n}]", explanation, f"Pārbaudiet literatūras ierakstu [{n}].")

        elif kind == "body_font_size":
            if fmt not in rule.get("formats", []):
                outcome = "not_assessed"
                explanation = "Šim formātam fonta izmērs netiek izvērtēts."
            else:
                bib_unit_ids = {u["id"] for u, _ in bibliography}
                eligible = False
                unresolved = False
                for unit in units:
                    if unit["heading"] or not unit["text"].strip():
                        continue
                    if unit["id"] in bib_unit_ids:
                        continue
                    if _normalize(unit["text"]) in bibliography_aliases:
                        continue
                    sizes = unit.get("font_sizes") or []
                    if not sizes:
                        unresolved = True
                        continue
                    eligible = True
                    if any(s is None for s in sizes):
                        unresolved = True
                    if any(s is not None and abs(s - rule["points"]) > rule["tolerance"] for s in sizes):
                        outcome = "failed"
                        explanation = "Pamatteksta fonta izmērs neatbilst konfigurācijai."
                        _finding(rule, unit, unit["text"][:100], explanation, "Lietojiet 12 punktu pamattekstu.")
                if outcome == "passed" and (unresolved or not eligible):
                    outcome = "not_assessed"
                    explanation = "Daļu formatējuma nevarēja droši noteikt."
        else:
            outcome = "not_assessed"
            explanation = "Neatbalstīts pārbaudes veids."

        rule_results[rule_id] = {"outcome": outcome, "explanation": explanation}

    return {"rule_results": rule_results, "findings": findings}


def select_advice_units(units: list, max_budget_tokens: int = 12000) -> tuple[list, list]:
    """Select a bounded subset of units for AI advice input.

    Returns (selected_units, omitted_unit_ids).
    Uses UTF-8 length / 4 as a conservative token estimate.
    """
    BUDGET = max_budget_tokens
    CHUNK_BUDGET = 2000  # max tokens per chunk

    selected = []
    omitted = []
    used = 0

    # Prioritize intro, methods, results sections
    priority_kws = ["ievads", "metodoloģija", "metodes", "rezultāti", "secinājumi"]
    priority_units = []
    other_units = []
    for u in units:
        if any(kw in u["text"].casefold()[:100] for kw in priority_kws) or u.get("heading"):
            priority_units.append(u)
        else:
            other_units.append(u)

    for u in priority_units + other_units:
        tokens = len(u["text"].encode()) // 4
        if used + tokens > BUDGET:
            omitted.append(u["id"])
            continue
        if tokens > CHUNK_BUDGET:
            omitted.append(u["id"])
            continue
        selected.append(u)
        used += tokens

    return selected, omitted
