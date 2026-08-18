#!/usr/bin/env python3
"""Genera una animación MP4 cuadrada a partir de un catálogo de terremotos."""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent / ".matplotlib_cache"))

import imageio_ffmpeg
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.animation import FFMpegWriter
from matplotlib.colors import LinearSegmentedColormap, Normalize
from PIL import Image, ImageEnhance, ImageOps


REQUIRED = {"fecha", "hora", "latitud", "longitud", "mag"}
SPANISH_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
HEADER_ALIASES = {
    "fecha": "fecha", "hora": "hora", "latitud": "latitud", "longitud": "longitud",
    "prof km": "prof km", "prof": "prof km", "mag": "mag", "localizacion": "localizacion",
    "evento": "evento", "inten": "inten", "tipo mag": "tipo mag",
}


@dataclass(frozen=True)
class Earthquake:
    event_id: str
    timestamp: datetime
    latitude: float
    longitude: float
    depth_km: float | None
    magnitude: float
    location: str


def format_spanish_datetime(value: datetime) -> str:
    return f"{value.day:02d} {SPANISH_MONTHS[value.month - 1]} {value.year} · {value:%H:%M:%S}"


def format_decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def magnitude_sizes(values: np.ndarray, minimum: float) -> np.ndarray:
    """Convierte magnitudes en áreas de burbuja expresadas en puntos cuadrados."""
    return 35 + 22 * np.maximum(0, values - minimum) ** 1.7


def normalize_text(value: str) -> str:
    value = value.strip().replace("\ufffd", "")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def detect_encoding(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    return "latin-1"


def load_catalog(path: Path) -> tuple[list[Earthquake], list[str]]:
    encoding = detect_encoding(path)
    with path.open("r", encoding=encoding, newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        rows = csv.reader(handle, dialect)
        raw_headers = next(rows, [])
        headers = []
        for header in raw_headers:
            key = re.sub(r"[^a-z0-9]+", " ", normalize_text(header)).strip()
            headers.append(HEADER_ALIASES.get(key, key))
        missing = REQUIRED - set(headers)
        if missing:
            raise ValueError(f"Faltan columnas obligatorias en el CSV: {', '.join(sorted(missing))}")
        index = {name: i for i, name in enumerate(headers)}
        warnings: list[str] = []
        earthquakes: list[Earthquake] = []

        def cell(row: list[str], name: str) -> str:
            i = index.get(name)
            return row[i].strip() if i is not None and i < len(row) else ""

        for line_number, row in enumerate(rows, 2):
            try:
                timestamp = datetime.strptime(cell(row, "fecha") + " " + cell(row, "hora"), "%d/%m/%Y %H:%M:%S")
                latitude = float(cell(row, "latitud").replace(",", "."))
                longitude = float(cell(row, "longitud").replace(",", "."))
                magnitude = float(cell(row, "mag").replace(",", "."))
                depth_raw = cell(row, "prof km")
                depth = float(depth_raw.replace(",", ".")) if depth_raw else None
                if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                    raise ValueError("coordinates out of range")
                earthquakes.append(Earthquake(cell(row, "evento"), timestamp, latitude, longitude, depth,
                                              magnitude, cell(row, "localizacion")))
            except (ValueError, IndexError) as exc:
                warnings.append(f"línea {line_number}: fila descartada ({exc})")
    earthquakes.sort(key=lambda event: event.timestamp)
    if not earthquakes:
        raise ValueError("No se han encontrado terremotos válidos")
    return earthquakes, warnings


def lon_to_x(lon: float, zoom: int) -> float:
    return (lon + 180.0) / 360.0 * (2**zoom)


def lat_to_y(lat: float, zoom: int) -> float:
    lat = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(lat)
    return (1 - math.asinh(math.tan(radians)) / math.pi) / 2 * (2**zoom)


def choose_zoom(events: Iterable[Earthquake], target_pixels: int) -> int:
    events = list(events)
    for zoom in range(3, 17):
        width = max(lon_to_x(e.longitude, zoom) for e in events) - min(lon_to_x(e.longitude, zoom) for e in events)
        height = max(lat_to_y(e.latitude, zoom) for e in events) - min(lat_to_y(e.latitude, zoom) for e in events)
        if max(width, height) * 256 >= target_pixels * 1.2:
            return zoom
    return 16


def fetch_basemap(events: Iterable[Earthquake], cache_dir: Path, zoom: int = 10,
                  map_style: str = "grayscale") -> tuple[Image.Image, tuple[float, float, float, float]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    lons = [e.longitude for e in events]
    lats = [e.latitude for e in events]
    x_values = [lon_to_x(lon, zoom) for lon in lons]
    y_values = [lat_to_y(lat, zoom) for lat in lats]
    pad = 0.08
    x0, x1 = min(x_values), max(x_values)
    y0, y1 = min(y_values), max(y_values)
    width, height = max(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x0, x1 = cx - width * (0.5 + pad), cx + width * (0.5 + pad)
    y0, y1 = cy - height * (0.5 + pad), cy + height * (0.5 + pad)
    tile_min_x, tile_max_x = math.floor(x0), math.floor(x1)
    tile_min_y, tile_max_y = math.floor(y0), math.floor(y1)
    canvas = Image.new("RGB", ((tile_max_x - tile_min_x + 1) * 256, (tile_max_y - tile_min_y + 1) * 256), (235, 235, 235))
    session = requests.Session()
    session.headers["User-Agent"] = "earthquake-visualizer/1.0 (personal visualization)"
    for tx in range(tile_min_x, tile_max_x + 1):
        for ty in range(tile_min_y, tile_max_y + 1):
            tile_path = cache_dir / str(zoom) / str(tx) / f"{ty}.png"
            tile_path.parent.mkdir(parents=True, exist_ok=True)
            if tile_path.exists():
                tile = Image.open(tile_path).convert("RGB")
            else:
                url = f"https://tile.openstreetmap.org/{zoom}/{tx % (2**zoom)}/{ty}.png"
                response = session.get(url, timeout=20)
                response.raise_for_status()
                tile = Image.open(io.BytesIO(response.content)).convert("RGB")
                tile.save(tile_path)
            canvas.paste(tile, ((tx - tile_min_x) * 256, (ty - tile_min_y) * 256))
    # Crop the tile mosaic to the exact square data extent.  The returned
    # extent remains in global Web Mercator tile coordinates, matching the
    # coordinates used for the earthquake points.
    crop_box = (
        round((x0 - tile_min_x) * 256),
        round((y0 - tile_min_y) * 256),
        round((x1 - tile_min_x) * 256),
        round((y1 - tile_min_y) * 256),
    )
    cropped = canvas.crop(crop_box)
    if map_style == "grayscale":
        cropped = ImageOps.grayscale(cropped)
        cropped = ImageEnhance.Contrast(cropped).enhance(0.82)
        cropped = ImageEnhance.Brightness(cropped).enhance(1.08).convert("RGB")
    extent = (x0, x1, y1, y0)
    return cropped, extent


def render(events: list[Earthquake], output: Path, duration: float, fps: int, size: int,
           cache_dir: Path, map_style: str) -> None:
    zoom = choose_zoom(events, size)
    try:
        background, extent = fetch_basemap(events, cache_dir, zoom, map_style)
    except requests.RequestException as exc:
        raise RuntimeError(f"No se han podido descargar los mosaicos de OpenStreetMap: {exc}. Comprueba la conexión o rellena primero --cache-dir.") from exc
    frame_count = max(1, round(duration * fps))
    magnitudes = np.array([event.magnitude for event in events])
    norm = Normalize(vmin=float(magnitudes.min()), vmax=float(magnitudes.max()) or float(magnitudes.min() + 1))
    cmap = LinearSegmentedColormap.from_list(
        "contraste_sismico",
        ("#00BFEA", "#00A878", "#FFE45E", "#FF7A00", "#C7005C"),
    )
    x_points = np.array([lon_to_x(e.longitude, zoom) for e in events])
    y_points = np.array([lat_to_y(e.latitude, zoom) for e in events])
    points = np.column_stack((x_points, y_points))
    sizes = magnitude_sizes(magnitudes, float(magnitudes.min()))
    first, last = events[0].timestamp, events[-1].timestamp
    span = max((last - first).total_seconds(), 1)
    event_seconds = np.array([(event.timestamp - first).total_seconds() for event in events])
    fig, ax = plt.subplots(figsize=(size / 100, size / 100), dpi=100)
    fig.subplots_adjust(0, 0, 1, 1)
    ax.set_axis_off()
    ax.imshow(background, extent=extent, aspect="auto", alpha=0.96)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    quake_points = ax.scatter([], [], s=[], c=[], cmap=cmap, norm=norm, alpha=0.78,
                              edgecolors="white", linewidths=0.9)
    pulse_ring = ax.scatter([], [], s=[], facecolors="none", edgecolors="white", linewidths=2.2)
    ax.text(0.04, 0.95, "ACTIVIDAD SÍSMICA", transform=ax.transAxes, color="white", fontsize=20,
            weight="bold", va="top", bbox=dict(facecolor="#17202a", alpha=0.82, pad=8, edgecolor="none"))
    status_text = ax.text(0.04, 0.875, "", transform=ax.transAxes, color="white", fontsize=13, va="top",
                          bbox=dict(facecolor="#17202a", alpha=0.72, pad=4, edgecolor="none"))
    counter_text = ax.text(0.96, 0.855, "0", transform=ax.transAxes, color="white", fontsize=28,
                           weight="bold", va="top", ha="right",
                           bbox=dict(facecolor="#17202a", alpha=0.82, pad=7, edgecolor="none"))
    legend_values = np.array([magnitudes.min(), np.median(magnitudes), magnitudes.max()])
    legend_ax = ax.inset_axes([0.72, 0.89, 0.25, 0.085])
    legend_ax.set_facecolor((23 / 255, 32 / 255, 42 / 255, 0.86))
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)
    legend_ax.set_xticks([])
    legend_ax.set_yticks([])
    for spine in legend_ax.spines.values():
        spine.set_visible(False)
    legend_ax.text(0.06, 0.80, "MAGNITUD", color="white", fontsize=11, weight="bold", va="center")
    legend_x = np.array([0.30, 0.57, 0.84])
    legend_ax.scatter(legend_x, np.full(3, 0.48),
                      s=magnitude_sizes(legend_values, float(magnitudes.min())),
                      c=legend_values, cmap=cmap, norm=norm, alpha=0.9,
                      edgecolors="white", linewidths=1.0)
    for x, value in zip(legend_x, legend_values):
        legend_ax.text(x, 0.12, format_decimal(float(value)), color="white", fontsize=9,
                       ha="center", va="center")
    ax.text(0.04, 0.035, "Datos: IGN | Mapa: OSM",
            transform=ax.transAxes, color="white", fontsize=9, va="bottom",
            bbox=dict(facecolor="#17202a", alpha=0.75, pad=4, edgecolor="none"))
    ax.text(0.96, 0.035, "linkedin.com/in/draxus", transform=ax.transAxes, color="white",
            fontsize=11, va="bottom", ha="right", weight="bold",
            bbox=dict(facecolor="#0a66c2", alpha=0.92, pad=5, boxstyle="round,pad=0.35", edgecolor="none"))
    writer = FFMpegWriter(fps=fps, codec="libx264", bitrate=5000, extra_args=["-pix_fmt", "yuv420p"])
    plt.rcParams["animation.ffmpeg_path"] = imageio_ffmpeg.get_ffmpeg_exe()
    with writer.saving(fig, str(output), fig.dpi):
        for frame in range(frame_count):
            progress = frame / max(frame_count - 1, 1)
            current_seconds = progress * span
            visible_count = int(np.searchsorted(event_seconds, current_seconds, side="right"))
            quake_points.set_offsets(points[:visible_count])
            quake_points.set_sizes(sizes[:visible_count])
            quake_points.set_array(magnitudes[:visible_count])
            if visible_count:
                newest = visible_count - 1
                age_frames = frame - round(((events[newest].timestamp - first).total_seconds() / span) * (frame_count - 1))
                if 0 <= age_frames < max(3, round(fps * 0.7)):
                    pulse = age_frames / max(1, fps * 0.7)
                    pulse_ring.set_offsets(points[newest:newest + 1])
                    pulse_ring.set_sizes([110 + pulse * 420])
                    pulse_ring.set_alpha(max(0.0, 1.0 - pulse))
                else:
                    pulse_ring.set_offsets(np.empty((0, 2)))
            current_event = events[visible_count - 1] if visible_count else events[0]
            status_text.set_text(format_spanish_datetime(current_event.timestamp))
            counter_text.set_text(str(visible_count))
            writer.grab_frame(facecolor="#17202a")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Catálogo CSV de entrada")
    parser.add_argument("-o", "--output", type=Path, default=Path("earthquake_animation.mp4"), help="Archivo MP4 de salida")
    parser.add_argument("--duration", type=float, default=30, help="Duración en segundos")
    parser.add_argument("--fps", type=int, default=30, help="Fotogramas por segundo")
    parser.add_argument("--size", type=int, default=1080, help="Tamaño del lado del vídeo cuadrado en píxeles")
    parser.add_argument("--cache-dir", type=Path, default=Path(".map_cache"), help="Directorio de caché de los mosaicos")
    parser.add_argument("--map-style", choices=("grayscale", "color"), default="grayscale",
                        help="Aspecto del mapa base (predeterminado: grayscale)")
    parser.add_argument("--max-events", type=int, help="Limita la animación a los primeros N terremotos")
    parser.add_argument("--start-magnitude", type=float,
                        help="Descarta el tramo inicial hasta el primer terremoto que alcance esta magnitud")
    args = parser.parse_args()
    if args.duration <= 0 or args.fps <= 0 or args.size <= 0:
        parser.error("la duración, los FPS y el tamaño deben ser positivos")
    if args.max_events is not None and args.max_events <= 0:
        parser.error("--max-events debe ser un número positivo")
    try:
        events, warnings = load_catalog(args.csv)
        if args.start_magnitude is not None:
            start_index = next(
                (index for index, event in enumerate(events) if event.magnitude >= args.start_magnitude),
                None,
            )
            if start_index is None:
                raise ValueError(
                    f"Ningún terremoto alcanza la magnitud inicial {format_decimal(args.start_magnitude)}"
                )
            events = events[start_index:]
        if args.max_events is not None:
            events = events[:args.max_events]
        for warning in warnings:
            print(f"aviso: {warning}", file=sys.stderr)
        print(f"Generando {len(events)} terremotos en {args.output}...")
        render(events, args.output, args.duration, args.fps, args.size, args.cache_dir, args.map_style)
        print(f"Creado: {args.output}")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
