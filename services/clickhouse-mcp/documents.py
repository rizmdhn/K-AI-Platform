"""create_document: the chat agent turns its answer (Markdown) or a SELECT into a DOCX, PDF, XLSX or CSV file.

Everything runs here, locally: no model call. A SELECT runs with the caller's own ClickHouse login through the same
gateway as execute_readonly_sql (SQL policy, readonly=1, query limits, audit). The caller gets a signed link that api
streams back (/api/kai-files/<id>.<ext>), like the labelling CSV.
ponytail: files live in MCP process memory for FILE_TTL (capped by MAX_STORE_BYTES, oldest dropped first) and are
lost on restart; move them to RustFS if links must survive restarts or the store needs to grow.
"""

import asyncio
import csv
import hmac
import io
import json
import os
import re
import secrets
import time
from typing import Annotated, Literal

from mcp_clickhouse.mcp_server import run_query_async
from pydantic import Field
from starlette.responses import JSONResponse, Response

from batch_jobs import _owner, _sign
from sql_policy import UnsafeSQL, validate
from tool_errors import GatewayError

MAX_ROWS = 50_000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_CONTENT_CHARS = 200_000
MAX_STORE_BYTES = 256 * 1024 * 1024
FILE_TTL = 24 * 3600
FILES_PER_DAY = 50  # per user; ponytail: in memory, resets on restart
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}
_files = {}  # file_id -> {owner, format, name, data, created}


def _safe_cell(value):
    """Spreadsheet formula injection guard: a text cell starting with = + - @ is stored as text."""
    return "'" + value if isinstance(value, str) and value[:1] in "=+-@" else value


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def blocks(markdown):
    """Markdown subset the agent writes: # headings, paragraphs, - / 1. lists, | tables |. Inline **bold** kept."""
    out, para, lines = [], [], markdown.replace("\r\n", "\n").split("\n")
    flush = lambda: para and (out.append(("p", " ".join(para))), para.clear())
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?[\s:|-]+\|?", lines[i + 1].strip()) and "-" in lines[i + 1]:
            flush()
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append(("table", rows))
            continue
        heading = re.match(r"(#{1,3})\s+(.*)", line)
        item = re.match(r"([-*•]|\d+[.)])\s+(.*)", line)
        if heading:
            flush()
            out.append((f"h{len(heading.group(1))}", heading.group(2)))
        elif item:
            flush()
            out.append(("ol" if item.group(1)[0].isdigit() else "li", item.group(2)))
        elif line:
            para.append(line)
        else:
            flush()
        i += 1
    flush()
    return out


def _runs(text):
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]."""
    return [(part, i % 2 == 1) for i, part in enumerate(text.split("**")) if part]


def _plain(text):
    return text.replace("**", "")


def to_docx(title, markdown):
    from docx import Document

    doc = Document()
    doc.add_heading(title, 0)
    for kind, value in blocks(markdown):
        if kind == "table":
            width = max(len(r) for r in value)
            table = doc.add_table(rows=0, cols=width)
            table.style = "Table Grid"
            for n, row in enumerate(value):
                cells = table.add_row().cells
                for cell, text in zip(cells, row):
                    cell.text = ""
                    for part, bold in _runs(text):
                        cell.paragraphs[0].add_run(part).bold = bold or n == 0
            continue
        if kind[0] == "h":
            doc.add_heading(_plain(value), int(kind[1]))
            continue
        paragraph = doc.add_paragraph(style={"li": "List Bullet", "ol": "List Number"}.get(kind))
        for part, bold in _runs(value):
            paragraph.add_run(part).bold = bold
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def to_pdf(title, markdown):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_font("body", "", FONT)
    pdf.add_font("body", "B", FONT_BOLD)
    pdf.add_font("body", "I", FONT)  # markdown=True may toggle __italic__; no italic face shipped
    pdf.add_font("body", "BI", FONT_BOLD)
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_font("body", "B", 18)
    pdf.multi_cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    number = 0
    for kind, value in blocks(markdown):
        number = number + 1 if kind == "ol" else 0
        if kind == "table":
            pdf.set_font("body", "", 9)
            width = max(len(r) for r in value)
            with pdf.table(first_row_as_headings=True, line_height=5) as table:
                for row in value:
                    cells = table.row()
                    for text in (row + [""] * width)[:width]:
                        cells.cell(_plain(text))
            pdf.ln(3)
            continue
        if kind[0] == "h":
            pdf.set_font("body", "B", {"h1": 15, "h2": 13, "h3": 11}[kind])
            pdf.ln(2)
            pdf.multi_cell(0, 7, _plain(value), new_x="LMARGIN", new_y="NEXT")
            continue
        pdf.set_font("body", "", 10)
        prefix = {"li": "•  ", "ol": f"{number}.  "}.get(kind, "")
        pdf.multi_cell(0, 5.5, prefix + value, markdown=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
    return bytes(pdf.output())


def to_xlsx(title, sheets):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    book.remove(book.active)
    for n, (columns, rows) in enumerate(sheets, 1):
        sheet = book.create_sheet((re.sub(r"[\[\]:*?/\\]", " ", title)[:28] or "Data") + (f" {n}" if len(sheets) > 1 else ""))
        sheet.append([_safe_cell(str(c)) for c in columns])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            # Whole numbers from Markdown tables become numbers; "1.200" stays text (thousands or decimal is ambiguous).
            row = [int(v) if isinstance(v, str) and re.fullmatch(r"-?\d{1,15}", v) else v for v in row]
            sheet.append([_safe_cell(v) if isinstance(v, (str, int, float, bool)) or v is None else str(v) for v in row])
        sheet.freeze_panes = "A2"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def to_csv(columns, rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows([_safe_cell(v) for v in row] for row in rows)
    return ("﻿" + out.getvalue()).encode()


def _tables(markdown):
    return [(rows[0], rows[1:]) for kind, rows in blocks(markdown) if kind == "table"]


async def _query(sql):
    try:
        validate(sql)
    except UnsafeSQL as error:
        raise GatewayError("UNSAFE_SQL", str(error)) from None
    inner = sql.strip().rstrip(";")
    payload = json.loads(await run_query_async(f"SELECT * FROM ({inner}) LIMIT {MAX_ROWS + 1}"))
    if len(payload["rows"]) > MAX_ROWS:
        raise GatewayError("QUERY_LIMIT", f"The query returns more than {MAX_ROWS:,} rows, the file limit. "
                           "Tell the user, then narrow it (filter, GROUP BY) or split it into several files.")
    return payload["columns"], payload["rows"]


def _prune():
    now = time.time()
    for file_id in [f for f, item in _files.items() if now - item["created"] > FILE_TTL]:
        del _files[file_id]
    while sum(len(item["data"]) for item in _files.values()) > MAX_STORE_BYTES:
        del _files[min(_files, key=lambda f: _files[f]["created"])]


def file_name(title, fmt):
    stem = re.sub(r"[^A-Za-z0-9 _-]+", "", title).strip().replace(" ", "-")[:60] or "dokumen"
    return f"{stem}.{fmt}"


async def create_document(
    format: Literal["docx", "pdf", "xlsx", "csv"],
    title: Annotated[str, Field(min_length=1, max_length=120)],
    content: Annotated[str, Field(max_length=MAX_CONTENT_CHARS)] = "",
    sql: str = "",
) -> str:
    """Make a downloadable file when the user asks for a document, report or spreadsheet.

    docx/pdf: content is the full document in Markdown (# headings, paragraphs, - lists, | tables |, **bold**).
    xlsx/csv: sql is one read-only SELECT whose rows fill the sheet (up to 50000 rows; preferred, exact data),
    or content holds Markdown tables (one sheet per table). Returns a link; give it to the user as a markdown link.
    """
    owner = _owner()
    _prune()
    if sum(item["owner"] == owner for item in _files.values()) >= FILES_PER_DAY:
        raise GatewayError("QUERY_LIMIT", f"Daily limit of {FILES_PER_DAY} files reached; try again tomorrow.")
    title = title.strip()
    if format in ("docx", "pdf"):
        if sql or not content.strip():
            raise GatewayError("INVALID_ARGUMENT", "For docx/pdf write the whole document in content (Markdown); sql is only for xlsx/csv.")
        data = await asyncio.to_thread(to_docx if format == "docx" else to_pdf, title, content)
    else:
        sheets = [await _query(sql)] if sql.strip() else _tables(content)
        if not sheets:
            raise GatewayError("INVALID_ARGUMENT", "For xlsx/csv give sql (a SELECT) or content with at least one Markdown table.")
        if format == "csv" and len(sheets) > 1:
            raise GatewayError("INVALID_ARGUMENT", "A CSV holds one table; make one CSV per table or use xlsx.")
        data = await asyncio.to_thread(to_csv, *sheets[0]) if format == "csv" else await asyncio.to_thread(to_xlsx, title, sheets)
    if len(data) > MAX_FILE_BYTES:
        raise GatewayError("QUERY_LIMIT", f"The file would be {len(data) // 1048576} MB, above the 10 MB limit. "
                           "Tell the user, then make it smaller (fewer rows or columns) or split it.")
    file_id = secrets.token_hex(16)
    _files[file_id] = {"owner": owner, "format": format, "name": file_name(title, format), "data": data, "created": time.time()}
    expires = int(time.time()) + FILE_TTL
    link = f"/api/kai-files/{file_id}.{format}?exp={expires}&sig={_sign(file_id, owner, expires)}"
    return json.dumps({"link": link, "file_name": _files[file_id]["name"], "bytes": len(data), "note": (
        "Give the user the link as a markdown link with the file name. It works for 24 hours and is lost if the "
        "server restarts; then make the file again.")}, ensure_ascii=False)


async def download_request(request):
    """GET /v1/files/{file_id}.{ext}?exp&sig — called only by api with the service bearer."""
    expected = os.getenv("CLICKHOUSE_MCP_AUTH_TOKEN", "")
    if not expected or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {expected}"):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    file_id, ext = request.path_params.get("file_id", ""), request.path_params.get("ext", "")
    exp, sig = request.query_params.get("exp", ""), request.query_params.get("sig", "")
    item = _files.get(file_id)
    if not item or item["format"] != ext or not exp.isdigit() or int(exp) < time.time() or not hmac.compare_digest(
            sig, _sign(file_id, item["owner"], int(exp))):
        return JSONResponse({"message": "Link expired or invalid. Ask the assistant to make the file again."}, status_code=403)
    return Response(item["data"], media_type=TYPES[ext], headers={
        "Content-Disposition": f'attachment; filename="{item["name"]}"', "Cache-Control": "no-store"})


def register_file_routes(mcp):
    mcp.custom_route("/v1/files/{file_id}.{ext}", methods=["GET"])(download_request)
