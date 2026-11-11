from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import xlrd


SOURCE_PAGE = (
    "https://www.juntadeandalucia.es/organismos/agriculturapescaaguaydesarrollorural/"
    "servicios/estadistica-cartografia/estadisticas-agricolas/paginas/"
    "precios-agricolas-semanales.html"
)
USER_AGENT = "AceiteAlDia/1.0 (weekly public price history)"
QUALITY_PATTERNS = {
    "aove": ("VIRGEN-EXTRA", "VIRGEN EXTRA"),
    "virgen": ("VIRGENES - VIRGEN", "VIRGEN - VIRGEN"),
    "lampante": ("LAMPANTE",),
}
SPANISH_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().upper()


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.parts).strip()))
            self.href = None
            self.parts = []


def read_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=45) as response:
        return response.read()


def workbook_urls(page_html: bytes, year: int) -> dict[int, str]:
    parser = LinkParser()
    parser.feed(page_html.decode("utf-8", errors="replace"))
    links = [(urljoin(SOURCE_PAGE, href), text) for href, text in parser.links if href]

    current = next(
        (
            url for url, text in links
            if "P_AGR" in normalized(text)
            and str(year) in text
            and url.lower().endswith(".xls")
        ),
        None,
    )
    previous_year = year - 1
    previous = next(
        (
            url for url, text in links
            if text.strip() == str(previous_year)
            and url.lower().endswith(".xls")
        ),
        None,
    )
    if not current or not previous:
        raise RuntimeError(
            f"No se localizaron en la página oficial los Excel de {year} y {previous_year}."
        )
    return {previous_year: previous, year: current}


def quality_for(product: str) -> str | None:
    if "LAMPANTE" in product:
        return "lampante"
    if "VIRGEN-EXTRA" in product or "VIRGEN EXTRA" in product:
        return "aove"
    if "VIRGENES - VIRGEN" in product or "VIRGEN - VIRGEN" in product:
        return "virgen"
    return None


def numeric_value(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value).strip().replace(".", "").replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return None
    return round(number / 100, 4)  # The source workbook reports EUR per 100 kg.


def parse_year_workbook(content: bytes, year: int) -> dict[str, dict[str, float | None]]:
    workbook = xlrd.open_workbook(file_contents=content)
    sheet = next(
        (sheet for sheet in workbook.sheets() if normalized(sheet.name) == "AGRICOLA"),
        None,
    )
    if sheet is None:
        raise RuntimeError(f"El Excel de {year} no contiene la hoja Agrícola.")

    week_columns: dict[int, int] = {}
    for column in range(5, sheet.ncols):
        header = normalized(sheet.cell_value(3, column))
        match = re.search(r"SEM(?:ANA)?\.?\s*(\d+)", header)
        if match:
            week_columns[int(match.group(1))] = column
    if not week_columns:
        raise RuntimeError(f"No se encontraron columnas semanales en el Excel de {year}.")

    candidates: dict[str, list[tuple[str, dict[int, float | None]]]] = {
        "aove": [], "virgen": [], "lampante": []
    }
    for row_index in range(5, sheet.nrows):
        product = normalized(sheet.cell_value(row_index, 0))
        market = normalized(sheet.cell_value(row_index, 3))
        if "JAEN" not in market or not market.startswith("JA-"):
            continue
        quality = quality_for(product)
        if quality is None:
            continue
        position = normalized(sheet.cell_value(row_index, 4))
        values = {
            week: numeric_value(sheet.cell_value(row_index, column))
            for week, column in week_columns.items()
        }
        candidates[quality].append((position, values))

    result: dict[str, dict[str, float | None]] = {}
    for quality, matches in candidates.items():
        if not matches:
            raise RuntimeError(f"No se encontró la fila de Jaén para {quality} en {year}.")
        preferred = [item for item in matches if "A.I." in item[0]]
        chosen = preferred if preferred else matches
        if len(chosen) != 1:
            raise RuntimeError(
                f"Hay varias filas de Jaén para {quality} en {year}; se detiene para evitar mezclar mercados."
            )
        result[quality] = {str(week): value for week, value in chosen[0][1].items()}

    weekly: dict[str, dict[str, float | None]] = {}
    for week in sorted(week_columns):
        weekly[str(week)] = {
            quality: result[quality].get(str(week))
            for quality in ("aove", "virgen", "lampante")
        }
    return weekly


def main() -> None:
    year = datetime.now(ZoneInfo("Europe/Madrid")).year
    page_html = read_url(SOURCE_PAGE)
    urls = workbook_urls(page_html, year)
    weekly_by_year = {
        str(source_year): parse_year_workbook(read_url(url), source_year)
        for source_year, url in urls.items()
    }

    current_year = weekly_by_year[str(year)]
    published_weeks = [
        int(week) for week, prices in current_year.items()
        if any(value is not None for value in prices.values())
    ]
    if not published_weeks:
        raise RuntimeError(f"El Excel oficial de {year} no contiene precios publicados.")
    latest_week = max(published_weeks)
    latest = current_year[str(latest_week)]
    if any(latest[quality] is None for quality in ("aove", "virgen", "lampante")):
        raise RuntimeError("Falta una de las tres calidades en la última semana; no se publican datos parciales.")

    checked = datetime.now(ZoneInfo("Europe/Madrid"))
    month = SPANISH_MONTHS[checked.month - 1]
    source_info = {
        "checkedOn": f"{checked.day} {month} {checked.year}",
        "latestYear": year,
        "latestWeek": latest_week,
        "latestPrices": latest,
    }
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "weekly-data.js")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "window.aceiteWeeklyData = "
        + json.dumps(weekly_by_year, ensure_ascii=False, separators=(",", ":"))
        + ";\nwindow.aceiteSourceInfo = "
        + json.dumps(source_info, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(f"Datos generados: semana {latest_week} de {year} ({output}).")


if __name__ == "__main__":
    main()
