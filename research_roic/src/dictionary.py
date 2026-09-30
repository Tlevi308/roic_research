"""Read the Word data dictionary completely (paragraphs, tables and equations).

python-docx drops Office-Math (OMML) content, which silently truncates numbers
and formulas (e.g. the Shapley weights). This module walks the raw XML in
document order and linearises OMML: fractions -> (a)/(b), subscripts -> x_i,
superscripts -> x^k, delimiters -> (...).

Outputs
-------
* ``read_docx_blocks``   : ordered list of paragraphs / tables with full text
* ``build_variable_catalogue`` : one row per documented column with meaning,
  formula, unit, possible values, quality-flag marker and invalidity conditions
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_BIDI = re.compile("[‎‏‪-‮⁦-⁩]")
_MATH_PROPS = {"rPr", "ctrlPr", "fPr", "dPr", "sSubPr", "sSupPr", "sSubSupPr", "naryPr", "radPr",
               "oMathParaPr", "accPr", "barPr", "boxPr", "funcPr", "limLowPr", "limUppPr", "eqArrPr", "mPr"}


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _clean(text: str) -> str:
    text = _BIDI.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _math(el: etree._Element) -> str:
    """Linearise an OMML element."""
    tag = _local(el.tag)
    if tag in _MATH_PROPS:
        return ""
    if tag == "t":
        return el.text or ""
    kids = {_local(k.tag): k for k in el}
    if tag == "f":
        return f"({_math(kids['num'])})/({_math(kids['den'])})" if "num" in kids and "den" in kids else ""
    if tag == "sSub":
        return f"{_math(kids['e'])}_{_wrap(_math(kids['sub']))}"
    if tag == "sSup":
        return f"{_math(kids['e'])}^{_wrap(_math(kids['sup']))}"
    if tag == "sSubSup":
        return f"{_math(kids['e'])}_{_wrap(_math(kids['sub']))}^{_wrap(_math(kids['sup']))}"
    if tag == "d":
        beg, end, sep = "(", ")", ","
        pr = el.find(f"{{{M_NS}}}dPr")
        if pr is not None:
            for name, default in (("begChr", "("), ("endChr", ")"), ("sepChr", ",")):
                node = pr.find(f"{{{M_NS}}}{name}")
                if node is not None:
                    val = node.get(f"{{{M_NS}}}val", default)
                    beg, end, sep = (val, end, sep) if name == "begChr" else (beg, val, sep) if name == "endChr" else (beg, end, val)
        parts = [_math(k) for k in el if _local(k.tag) == "e"]
        return f"{beg}{(sep + ' ').join(parts)}{end}"
    if tag == "nary":
        pr = el.find(f"{{{M_NS}}}naryPr")
        sym = "∫"
        if pr is not None and pr.find(f"{{{M_NS}}}chr") is not None:
            sym = pr.find(f"{{{M_NS}}}chr").get(f"{{{M_NS}}}val", sym)
        sub = _math(kids["sub"]) if "sub" in kids else ""
        sup = _math(kids["sup"]) if "sup" in kids else ""
        rng = f"[{sub}..{sup}]" if (sub or sup) else ""
        return f"{sym}{rng} {_math(kids['e'])}" if "e" in kids else sym
    if tag == "rad":
        deg = _math(kids["deg"]) if "deg" in kids else ""
        body = _math(kids["e"]) if "e" in kids else ""
        return f"root[{deg}]({body})" if deg else f"sqrt({body})"
    return "".join(_math(k) for k in el)


def _wrap(s: str) -> str:
    return s if len(s) <= 1 else "{" + s + "}"


def _text(el: etree._Element) -> str:
    """Text of a WordprocessingML element, with OMML linearised in place."""
    out: list[str] = []
    for node in el:
        ns_tag = node.tag
        tag = _local(ns_tag)
        if ns_tag.startswith(f"{{{M_NS}}}"):
            out.append(_math(node))
        elif tag == "t":
            out.append(node.text or "")
        elif tag == "tab":
            out.append(" ")
        elif tag in ("br", "cr"):
            out.append("\n")
        elif tag in ("rPr", "pPr", "tblPr", "trPr", "tcPr"):
            continue
        else:
            out.append(_text(node))
    return "".join(out)


def read_docx_blocks(path: Path) -> list[dict[str, Any]]:
    """Ordered blocks: {'kind': 'p', 'style', 'text'} or {'kind': 'table', 'rows': [[cell,...],...]}."""
    with zipfile.ZipFile(path) as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{{{W_NS}}}body")
    blocks: list[dict[str, Any]] = []
    table_no = 0
    for child in body:
        tag = _local(child.tag)
        if tag == "p":
            style_el = child.find(f"{{{W_NS}}}pPr/{{{W_NS}}}pStyle")
            style = style_el.get(f"{{{W_NS}}}val") if style_el is not None else "Normal"
            text = _clean(_text(child))
            if text:
                blocks.append({"kind": "p", "style": style, "text": text})
        elif tag == "tbl":
            table_no += 1
            rows = [[_clean(_text(tc)) for tc in tr.findall(f"{{{W_NS}}}tc")] for tr in child.findall(f"{{{W_NS}}}tr")]
            blocks.append({"kind": "table", "table_no": table_no, "rows": rows})
    return blocks


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for b in blocks:
        if b["kind"] == "p":
            lines.append(f"[{b['style']}] {b['text']}")
        else:
            lines.append(f"=== TABLE {b['table_no']} ===")
            lines.extend(" | ".join(r) for r in b["rows"])
            lines.append("=== END TABLE ===")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------
_HEADER_MAP = {
    "עמודה": "variable", "טיפוס": "type", "משמעות": "meaning", "משמעות וערכים": "meaning",
    "ערכים אפשריים": "possible_values", "ערכים": "possible_values", "מפתח ב-API": "api_key",
    "section / key": "api_key", "חישוב": "formula",
}
_SECTION_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?)\.?\s+(\D.{0,70})$")
_VAR_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Invalidity / interpretation caveats stated in dictionary prose (section references in brackets).
_PROSE_CAVEATS = {
    "calc_raw_tax_rate_quarterly": "Not clipped to [0,1]; negative (tax benefit) or >1 possible; NaN when pretax_income = 0 [4].",
    "calc_nopat_quarterly": "Uses the unclipped quarterly tax rate; a tax rate >100% makes NOPAT move opposite to EBIT [5].",
    "calc_ic_raw": "Can be negative when current liabilities exceed assets [4].",
    "calc_average_ic_raw_quarterly": "NaN unless the previous record is the consecutive previous quarter [4].",
    "calc_roic_pretax_quarterly_ic_raw": "Quarterly ratio (single-quarter numerator / average capital). Negative IC gives a mechanical, non-economic ratio [6.7].",
    "calc_roic_posttax_quarterly_ic_raw": "Quarterly ratio. Negative IC -> mechanical ratio; tax rate out of range is flagged, not clipped [6.7].",
    "calc_roic_posttax_change_quarterly": "NaN when calc_roic_decomposition_status != VALID; a classified row needs 3 consecutive quarters [6.1].",
    "calc_roic_ebit_contribution": "NaN when status != VALID. Effect sign is not the EBIT movement direction (tax rate >100% flips it) [5, 6.3].",
    "calc_roic_tax_contribution": "NaN when status != VALID. Contribution of Q = 1 - T, so a lower tax rate is a positive contribution [6.2, 6.4].",
    "calc_roic_ic_contribution": "NaN when status != VALID. A fall in IC is NOT always positive: with negative NOPAT a smaller denominator lowers ROIC. The sign is not the direction of IC [6.3].",
    "calc_roic_quality_flag": "VALID only when the pair is economically interpretable; NEGATIVE_IC_MECHANICAL_ONLY and TAX_RATE_OUT_OF_RANGE are computable but not economic [6.7].",
    "calc_roic_economic_interpretation_valid": "Empty when decomposition status != VALID [6.7].",
    "calc_ic_movement": "Direction of average IC itself (I1 - I0), depends on ROIC status [6.4].",
    "calc_roic_dominant_driver": "BALANCED when top two absolute shares differ by <= 0.05; NONE when all contributions neutral [6.6].",
    "calc_debt_to_equity_quarterly": "NaN when equity = 0; negative when equity negative [4].",
    "calc_ev_to_fcf_quarterly": "Negative (meaningless) when TTM FCF < 0; NaN when FCF = 0 [4].",
    "calc_roic_posttax_annualized_ic_raw": "NaN when quarterly ratio <= -100% [7.1].",
    "calc_roic_pretax_annualized_ic_raw": "NaN when quarterly ratio <= -100% [7.1].",
    "calc_wacc_cost_of_debt": "Biased downward when interest is reported as zero despite debt (flag DEBT_WITHOUT_INTEREST) [7.6].",
    "calc_wacc_annual": "Not computed for INSUFFICIENT_HISTORY / MISSING_MARKET_CAP / MISSING_DEBT; DEBT_WITHOUT_INTEREST biases it down [7.6].",
    "interest_expense": "Negative sign = expense; reported as zero in many quarters even with debt [2, 7.6].",
    "tax_provision": "GuruFocus reports tax expense with a negative sign [2].",
}


def _unit_for(var: str, section: str, vtype: str) -> str:
    if var.startswith(("calc_nopat_combo__", "calc_raw_combo__", "calc_roic_combo__")):
        return "indicator int8 (0/1)"
    if vtype.startswith("טקסט YYYY"):
        return "date (YYYY-MM-DD text)"
    if re.search(r"(status|flag|effect|movement|direction|regime|combination|structure|classification|dominant_driver$|dominant_driver_effect|explanation)", var):
        return "category / text"
    if re.search(r"(interpretation_valid|inputs_complete|creates_value|has_opposing)", var):
        return "boolean"
    if var.endswith("_count"):
        return "count (0-3)"
    if var in ("symbol", "company", "sector", "industry", "period_key", "period_quarter"):
        return "text"
    if var == "period_year":
        return "integer year"
    if var == "valuations__per_share_data__month_end_stock_price":
        return "price per share (currency not stated in dictionary)"
    if var == "valuations__per_share_data__shares_outstanding":
        return "number of shares (scale not stated in dictionary)"
    if var.startswith("calc_roic_") and "annualized" in var:
        return "annual ratio (decimal)"
    is_wacc_rate = var.startswith("calc_wacc_") and re.search(r"(?:rate|weight|cost|premium|annual|quarterly)", var)
    if var.startswith("calc_roic_") or is_wacc_rate:
        return "quarterly ratio (decimal)" if "quarterly" in var else "ratio (decimal)"
    if "tax_rate" in var or "debt_to_equity" in var or "ev_to_fcf" in var or var.endswith("_ratio"):
        return "ratio (decimal)"
    return "API reporting currency units (USD millions for AAPL per dictionary)"


def _invalid_conditions(var: str, meaning: str, values: str, formula: str) -> str:
    pieces: list[str] = []
    for source in (meaning, values):
        for frag in re.split(r"(?<!\d)[.](?!\d)|[·;]", source or ""):
            f = frag.strip()
            if re.search(r"(?:NaN|ריק|חסר משמעות|מכני|אינו תקף|UNCLASSIFIED|מוטה|שלילי כש)", f):
                pieces.append(f)
    if var in _PROSE_CAVEATS:
        pieces.append(_PROSE_CAVEATS[var])
    seen: list[str] = []
    for p in pieces:
        if p and p not in seen:
            seen.append(p)
    return " | ".join(seen)


def build_variable_catalogue(blocks: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per documented column (tables + dummy-name paragraphs)."""
    rows: list[dict[str, Any]] = []
    section = ""
    classification_values: list[str] = []
    in_classification = False
    for b in blocks:
        if b["kind"] == "p":
            text = b["text"]
            m = _SECTION_RE.match(text)
            if m and "calc_" not in text:
                section = f"{m.group(1)} {m.group(2)}"
                in_classification = False
            elif b["style"].lower().startswith("heading"):
                section = f"{section.split(' ')[0]} / {text}" if section else text
            if "הערכים של calc_roic_business_classification" in text:
                in_classification = True
                continue
            if in_classification:
                tokens = [t.strip() for t in re.split(r"[·—]", text)]
                classification_values.extend(t for t in tokens if re.fullmatch(r"[A-Z_]+", t) and len(t) > 3)
            if re.fullmatch(r"calc_(nopat|raw|roic)_combo__[a-z_]+", text):
                rows.append({"variable": text, "section": "8 עמודות הדמה", "type": "int8",
                             "meaning": "Dummy indicator of one effect/movement combination",
                             "formula": "itertools.product over the sign/movement categories",
                             "possible_values": "0 · 1 (all 0 when row is unclassified)", "api_key": ""})
            continue
        header = b["rows"][0] if b["rows"] else []
        if not header or header[0] != "עמודה":
            continue
        names = [_HEADER_MAP.get(h, h) for h in header]
        for r in b["rows"][1:]:
            rec = dict(zip(names, r))
            var = rec.get("variable", "").strip()
            if not _VAR_RE.match(var):
                continue
            rec.update({"section": section, "table_no": b["table_no"]})
            rows.append(rec)
    cat = pd.DataFrame(rows)
    for col in ("type", "api_key", "formula", "meaning", "possible_values"):
        if col not in cat.columns:
            cat[col] = ""
        cat[col] = cat[col].fillna("")
    if classification_values:
        mask = cat["variable"] == "calc_roic_business_classification"
        cat.loc[mask, "possible_values"] = " · ".join(dict.fromkeys(classification_values))
    cat["unit"] = [_unit_for(v, s, t) for v, s, t in zip(cat["variable"], cat["section"], cat["type"])]
    cat["is_quality_or_status_flag"] = cat["variable"].str.contains(
        r"(?:status|quality_flag|interpretation_valid|inputs_complete|residual)", regex=True)
    cat["invalid_or_caveat_conditions"] = [
        _invalid_conditions(v, m, pv, f) for v, m, pv, f in
        zip(cat["variable"], cat["meaning"], cat["possible_values"], cat["formula"])]
    cat = cat.drop_duplicates(subset="variable", keep="first").reset_index(drop=True)
    order = ["variable", "section", "table_no", "type", "api_key", "meaning", "formula", "unit",
             "possible_values", "is_quality_or_status_flag", "invalid_or_caveat_conditions"]
    return cat[[c for c in order if c in cat.columns]]


def declared_column_count(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals stated in the dictionary header (e.g. '174 columns') and the group table."""
    total = None
    for b in blocks:
        if b["kind"] == "p":
            m = re.search(r"(\d+)\s*עמודות", b["text"])
            if m:
                total = int(m.group(1))
                break
    groups: dict[str, int] = {}
    for b in blocks:
        if b["kind"] == "table" and b["rows"] and b["rows"][0][:2] == ["קבוצה", "כמות"]:
            for r in b["rows"][1:]:
                if len(r) >= 2 and r[1].isdigit():
                    groups[r[0]] = int(r[1])
            break
    return {"declared_total": total, "declared_groups": groups}
