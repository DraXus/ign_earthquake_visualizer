"""Download and normalize the IGN earthquake catalogue used by the website."""

from __future__ import annotations

import argparse
import csv
import html as html_module
import io
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests


SEARCH_URL = (
    "https://www.ign.es/web/sis-catalogo-terremotos/-/catalogo-terremotos/"
    "searchTerremoto"
)
START_DATE = "14/08/2026"
TIME_ZONE = ZoneInfo("Europe/Madrid")
BOUNDS = {
    "latMin": "36.95",
    "latMax": "37.35",
    "longMin": "-3.90",
    "longMax": "-3.40",
}
REQUIRED_COLUMNS = {"evento", "fecha", "hora", "latitud", "longitud", "mag", "tipomag"}
OUTPUT_COLUMNS = (
    "Evento",
    "Fecha",
    "Hora",
    "Latitud",
    "Longitud",
    "Prof. (Km)",
    "Inten.",
    "Mag.",
    "Tipo Mag.",
    "Localización",
)


def normalize_heading(value: str) -> str:
    value = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(
        character
        for character in value
        if unicodedata.category(character) != "Mn" and character.isalnum()
    )


def today_in_madrid() -> str:
    return datetime.now(TIME_ZONE).strftime("%d/%m/%Y")


def build_query_params(end_date: str) -> dict[str, str]:
    return {
        **BOUNDS,
        "startDate": START_DATE,
        "endDate": end_date,
        "selIntensidad": "N",
        "intMin": "",
        "intMax": "",
        "selMagnitud": "N",
        "magMin": "",
        "magMax": "",
        "selProf": "N",
        "profMin": "",
        "profMax": "",
        "cond": "",
        "fases": "no",
    }


class DownloadFormParser(HTMLParser):
    """Extract the dynamically prefixed Liferay catalogue download form."""

    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.found_form = False
        self.action = ""
        self.fields: dict[str, str] = {}
        self.select_names: list[str] = []
        self.current_select: str | None = None
        self.has_csv_option = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and not self.found_form:
            form_id = attributes.get("id", "") or ""
            form_name = attributes.get("name", "") or ""
            if form_id.endswith("_fm") or form_name.endswith("_fm"):
                self.in_form = True
                self.found_form = True
                self.action = attributes.get("action", "") or ""
            return

        if not self.in_form:
            return

        if tag == "input" and attributes.get("name"):
            self.fields[attributes["name"]] = attributes.get("value", "") or ""
        elif tag == "select" and attributes.get("name"):
            self.current_select = attributes["name"]
            self.select_names.append(self.current_select)
        elif tag == "option" and self.current_select:
            if (attributes.get("value", "") or "").lower() == "csv":
                self.has_csv_option = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.current_select = None
        elif tag == "form" and self.in_form:
            self.in_form = False


class ResultsTableParser(HTMLParser):
    """Collect earthquake rows from an IGN search result page."""

    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cells: list[str] = []
        self.cell_parts: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.cells = []
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_cell:
            self.cells.append(" ".join("".join(self.cell_parts).split()))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if len(self.cells) >= 11 and re.fullmatch(r"(?:es\d{4}[a-z]+|\d+)", self.cells[0]):
                self.rows.append(self.cells)
            self.in_row = False


def parse_results_page(html: str) -> tuple[list[list[str]], int | None, int | None]:
    parser = ResultsTableParser()
    parser.feed(html)
    total_match = re.search(r"Se han encontrado\s+(\d+)\s+terremotos", html)
    next_match = re.search(r"changePage\([^;]+?'nextForm'\s*,\s*(\d+)\)", html)
    total = int(total_match.group(1)) if total_match else None
    next_index = int(next_match.group(1)) if next_match else None

    rows = []
    for cells in parser.rows:
        # The HTML includes local time, while the published CSV uses UTC only.
        rows.append(
            [
                cells[0], cells[1], cells[2], cells[4], cells[5], cells[6],
                cells[9], cells[7], cells[8], cells[10],
            ]
        )
    return rows, total, next_index


def parse_download_form(html: str) -> tuple[str, dict[str, str]]:
    parser = DownloadFormParser()
    parser.feed(html)
    if not parser.found_form:
        raise ValueError("IGN response does not contain the catalogue download form")

    form_date = next((name for name in parser.fields if name.endswith("formDate")), None)
    if not form_date:
        raise ValueError("IGN download form is missing formDate")

    download_name = next(
        (name for name in parser.fields if name.endswith("tipoDescarga")), None
    )
    if download_name is None:
        # Select elements are not normally submitted as hidden inputs.
        download_name = next(
            (name for name in parser.select_names if name.endswith("tipoDescarga")), None
        )
    if not download_name or not parser.has_csv_option:
        raise ValueError("IGN download form does not offer CSV output")

    parser.fields[download_name] = "csv"

    action = parser.action
    if not action:
        resource_action = re.search(
            r'A\.io\.request\("([^"\r\n]+p_p_lifecycle=(?:2|%32)[^"\r\n]*)"',
            html,
        )
        if resource_action:
            action = html_module.unescape(resource_action.group(1))
    if not action:
        raise ValueError("IGN download form is missing its resource URL")
    return action, parser.fields


def decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("IGN catalogue uses an unsupported character encoding")


def validate_and_normalize(content: bytes) -> tuple[str, int]:
    stripped = content.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html")):
        raise ValueError("IGN returned HTML instead of a CSV catalogue")

    text = decode_csv(content)
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not rows or not any(cell.strip() for cell in rows[0]):
        raise ValueError("IGN returned an empty catalogue")

    headings = [normalize_heading(cell) for cell in rows[0]]
    missing = REQUIRED_COLUMNS.difference(headings)
    if missing:
        raise ValueError(f"IGN catalogue is missing columns: {', '.join(sorted(missing))}")

    data_rows = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    if not data_rows:
        raise ValueError("IGN catalogue contains no earthquake records")

    width = len(rows[0])
    magnitude_type_index = headings.index("tipomag")
    for line_number, row in enumerate(data_rows, start=2):
        if len(row) != width:
            raise ValueError(f"Malformed CSV row {line_number}: expected {width} columns")
        if not row[magnitude_type_index].strip():
            raise ValueError(f"IGN catalogue row {line_number} is missing its magnitude type")

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow([cell.strip() for cell in rows[0]])
    writer.writerows([[cell.strip() for cell in row] for row in data_rows])
    return output.getvalue(), len(data_rows)


def fetch_catalogue(end_date: str, session: requests.Session | None = None) -> tuple[bytes, str]:
    client = session or requests.Session()
    client.headers.update({"User-Agent": "earthquake-visualizer/1.0 (GitHub Pages updater)"})

    result = client.get(SEARCH_URL, params=build_query_params(end_date), timeout=60)
    result.raise_for_status()
    search_html = decode_csv(result.content)
    action, fields = parse_download_form(search_html)
    download_url = urljoin(result.url, action) if action else result.url
    download = client.post(
        download_url,
        data=fields,
        headers={"Referer": result.url},
        timeout=60,
    )
    download.raise_for_status()
    try:
        validate_and_normalize(download.content)
        return download.content, download.headers.get("content-type", "")
    except ValueError:
        # The IGN resource endpoint occasionally returns only an empty fixed-width
        # header. Its paginated HTML still contains the complete official result.
        rows, expected_total, next_index = parse_results_page(search_html)
        seen = {row[0] for row in rows}
        while next_index is not None:
            page_params = build_query_params(end_date)
            page_params["indice"] = str(next_index)
            page = client.get(SEARCH_URL, params=page_params, timeout=60)
            page.raise_for_status()
            page_rows, _page_total, next_index = parse_results_page(decode_csv(page.content))
            for row in page_rows:
                if row[0] not in seen:
                    seen.add(row[0])
                    rows.append(row)

        if expected_total is None or len(rows) != expected_total:
            raise ValueError(
                f"IGN result count mismatch: expected {expected_total}, collected {len(rows)}"
            )

        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";", lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(rows)
        return output.getvalue().encode("utf-8"), "text/csv; charset=utf-8"


def write_catalogue(output_path: Path, content: bytes) -> int:
    normalized, count = validate_and_normalize(content)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=output_path.parent, delete=False
        ) as temporary:
            temporary.write(normalized)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Destination CSV path")
    parser.add_argument("--end-date", help="End date in DD/MM/YYYY (defaults to today in Madrid)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    end_date = args.end_date or today_in_madrid()
    try:
        datetime.strptime(end_date, "%d/%m/%Y")
        content, _content_type = fetch_catalogue(end_date)
        count = write_catalogue(args.output, content)
    except (requests.RequestException, ValueError, OSError) as error:
        print(f"Catalogue update failed: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {count} earthquakes ({START_DATE}–{end_date}) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
