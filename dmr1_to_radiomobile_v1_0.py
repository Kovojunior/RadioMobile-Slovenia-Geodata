#!/usr/bin/env python
"""GURS DMR1 -> nastavljiv BIL za Radio Mobile in druge namene.

Združena različica V1.0:
  - prenese/prevzorči DMR1 neposredno iz javnega GURS ImageServerja;
  - izdela 1° BIL ploščice in skupni VRT;
  - izvede strukturno preverjanje kakovosti in opcijsko primerjavo z referenco;
  - po želji iste BIL-e brez prevzorčenja razreže na manjše dele ter preveri
    popolno enakost višinskih vrednosti;
  - ločljivost, razrez, prenos, vzporednost in kontrolni parametri so nastavljivi.

Privzeta ločljivost za Radio Mobile Deluxe je 1/9 ločne sekunde. Gostejši
izhod je dovoljen samo z --allow-finer-than-rm, ker ni namenjen neposredni
uporabi kot Radio Mobile BIL.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from osgeo import gdal, osr


SERVICE_URL = (
    "https://geohub.gov.si/image/rest/services/"
    "TEMELJNI_RASTRI/DMR1/ImageServer/exportImage"
)

# Prenosljiva privzeta struktura: vsi podatki so v ``workspace`` ob skripti.
# Setup lahko uporabi drugo lokacijo prek RMSLO_WORKSPACE.
PROJECT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = Path(
    os.environ.get("RMSLO_WORKSPACE", str(PROJECT_DIR / "workspace"))
).expanduser().resolve()
GEODATA_ROOT = WORKSPACE_ROOT / "Geodata"
DEFAULT_OUTPUT = GEODATA_ROOT / "DMR1" / "OUTPUT"
DEFAULT_REFERENCE = (
    GEODATA_ROOT / "DMV5" / "OUTPUT" / "RADIO_MOBILE" / "0p16"
    / "Slovenija_DMV_0p16_TILED.vrt"
)

SLOVENIA_TILES = (
    "N45E013", "N45E014", "N45E015", "N45E016",
    "N46E013", "N46E014", "N46E015", "N46E016",
)

# Privzeta in najgostejša neposredna Radio Mobile nastavitev.
RM_FINEST_ARCSEC = 1.0 / 9.0
DEFAULT_ARCSEC = RM_FINEST_ARCSEC

# Te tri vrednosti se nastavijo ob zagonu glede na --arcseconds.
PIXELS_PER_DEGREE = 32400
ARCSEC = 3600.0 / PIXELS_PER_DEGREE
DEG_PER_PIXEL = 1.0 / PIXELS_PER_DEGREE
RESOLUTION_LABEL = "1over9"
INTERPOLATION_ENUM = "RSP_BilinearInterpolation"
STRIPE_ROWS = 256

# Conservative request layout. 12 x 12 gives 2700 x 2700 px per request.
# This is well below the advertised 15000 x 4100 export limit and keeps
# each raw Float32 response to ~27.8 MiB before lossless TIFF compression.
DEFAULT_CHUNK_COLS = 12
DEFAULT_CHUNK_ROWS = 12

NODATA_FLOAT_REQUEST = -32768.0
NODATA_INT16 = -32768


@dataclass(frozen=True)
class Chunk:
    row: int
    col: int
    xoff: int
    yoff: int
    width: int
    height: int
    west: float
    south: float
    east: float
    north: float

    @property
    def key(self) -> str:
        return f"r{self.row:02d}c{self.col:02d}"


def epsg_wkt(epsg: int) -> str:
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    return srs.ExportToWkt()


def parse_tile_name(name: str) -> tuple[int, int]:
    name = name.upper().strip()
    if len(name) != 7 or name[0] not in "NS" or name[3] not in "EW":
        raise ValueError(f"Neveljavno ime tile-a: {name}")
    lat = int(name[1:3])
    lon = int(name[4:7])
    if name[0] == "S":
        lat = -lat
    if name[3] == "W":
        lon = -lon
    return lat, lon


def tile_bounds(name: str) -> tuple[float, float, float, float]:
    lat, lon = parse_tile_name(name)
    return float(lon), float(lat), float(lon + 1), float(lat + 1)


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--:--:--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def remove_related_bil_files(path: Path) -> None:
    for p in (
        path,
        path.with_suffix(".hdr"),
        path.with_suffix(".prj"),
        path.with_suffix(".aux.xml"),
    ):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def chunk_plan(tile_name: str, cols: int, rows: int) -> list[Chunk]:
    """Razdeli ploščico na poljubno število zahtev, tudi če mreža ni deljiva."""
    west, south, east, north = tile_bounds(tile_name)
    chunks: list[Chunk] = []
    for r in range(rows):
        y0 = int(round(r * PIXELS_PER_DEGREE / rows))
        y1 = int(round((r + 1) * PIXELS_PER_DEGREE / rows))
        ch = y1 - y0
        c_north = north - y0 * DEG_PER_PIXEL
        c_south = north - y1 * DEG_PER_PIXEL
        for c in range(cols):
            x0 = int(round(c * PIXELS_PER_DEGREE / cols))
            x1 = int(round((c + 1) * PIXELS_PER_DEGREE / cols))
            cw = x1 - x0
            c_west = west + x0 * DEG_PER_PIXEL
            c_east = west + x1 * DEG_PER_PIXEL
            chunks.append(
                Chunk(
                    row=r, col=c, xoff=x0, yoff=y0, width=cw, height=ch,
                    west=c_west, south=c_south, east=c_east, north=c_north,
                )
            )
    return chunks


def build_export_url(service_url: str, chunk: Chunk) -> str:
    params = {
        "bbox": (
            f"{chunk.west:.15f},{chunk.south:.15f},"
            f"{chunk.east:.15f},{chunk.north:.15f}"
        ),
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{chunk.width},{chunk.height}",
        "format": "tiff",
        "pixelType": "F32",
        "noData": str(int(NODATA_FLOAT_REQUEST)),
        "interpolation": INTERPOLATION_ENUM,
        # Lossless TIFF compression. This materially reduces transfer size while
        # preserving the Float32 values returned by the ImageServer.
        "compression": "LZ77",
        "adjustAspectRatio": "false",
        "f": "image",
    }
    return service_url + "?" + urlencode(params)


def progress_line(
    tile_name: str,
    chunk_index: int,
    total_chunks: int,
    chunk_fraction: float,
    overall_done_before_tile: int,
    overall_total_chunks: int,
    global_start: float,
    downloaded: int = 0,
    total_bytes: int | None = None,
) -> str:
    tile_fraction = (chunk_index + chunk_fraction) / total_chunks
    overall_fraction = (
        overall_done_before_tile + chunk_index + chunk_fraction
    ) / max(1, overall_total_chunks)
    elapsed = max(0.001, time.perf_counter() - global_start)
    eta = elapsed * (1.0 - overall_fraction) / overall_fraction if overall_fraction > 0 else None

    if total_bytes and total_bytes > 0:
        dl_text = f"  download={100.0 * downloaded / total_bytes:5.1f}%"
    elif downloaded:
        dl_text = f"  download={downloaded / 1024 / 1024:.1f} MiB"
    else:
        dl_text = ""

    return (
        f"\r{tile_name} | chunk {chunk_index + 1:02d}/{total_chunks:02d} | "
        f"tile={100.0 * tile_fraction:6.2f}% | "
        f"overall={100.0 * overall_fraction:6.2f}% | "
        f"ETA={fmt_duration(eta)}{dl_text}"
    )


def download_file(
    url: str,
    target: Path,
    tile_name: str,
    chunk_index: int,
    total_chunks: int,
    overall_done_before_tile: int,
    overall_total_chunks: int,
    global_start: float,
    timeout: int,
    retries: int,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            try:
                part.unlink()
            except FileNotFoundError:
                pass

            req = Request(url, headers={"User-Agent": "RadioMobile-DMR1/1.1"})
            with urlopen(req, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise RuntimeError(f"HTTP {status}")
                length_header = response.headers.get("Content-Length")
                total_bytes = int(length_header) if length_header else None
                content_type = response.headers.get("Content-Type", "")

                downloaded = 0
                last_print = 0.0
                with part.open("wb") as f:
                    while True:
                        block = response.read(4 * 1024 * 1024)
                        if not block:
                            break
                        f.write(block)
                        downloaded += len(block)
                        now = time.perf_counter()
                        if now - last_print >= 0.5:
                            frac = (
                                min(1.0, downloaded / total_bytes)
                                if total_bytes else 0.0
                            )
                            print(
                                progress_line(
                                    tile_name, chunk_index, total_chunks, frac,
                                    overall_done_before_tile, overall_total_chunks,
                                    global_start, downloaded, total_bytes,
                                ),
                                end="",
                                flush=True,
                            )
                            last_print = now

                if downloaded < 1024:
                    raise RuntimeError(
                        f"Premajhen odgovor ({downloaded} B, Content-Type={content_type})"
                    )
                if total_bytes is not None and downloaded != total_bytes:
                    raise RuntimeError(
                        f"Nepopoln download: {downloaded} / {total_bytes} B"
                    )

            part.replace(target)
            return

        except Exception as exc:
            last_exc = exc
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            if attempt >= retries:
                break
            wait = min(60, 5 * (2 ** (attempt - 1)))
            print(
                f"\n  Poskus {attempt}/{retries} ni uspel: {exc}\n"
                f"  Ponovim cez {wait} s ...",
                flush=True,
            )
            time.sleep(wait)

    raise RuntimeError(f"Download ni uspel po {retries} poskusih: {last_exc}")


def is_epsg4326(ds) -> bool:
    srs = osr.SpatialReference()
    srs.ImportFromWkt(ds.GetProjection())
    expected = osr.SpatialReference()
    expected.ImportFromEPSG(4326)
    try:
        return bool(srs.IsSame(expected))
    except Exception:
        return False


def validate_chunk(path: Path, chunk: Chunk) -> None:
    ds = gdal.Open(str(path), gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"GDAL ne more odpreti chunk-a: {path}")

    errors: list[str] = []
    if ds.RasterXSize != chunk.width or ds.RasterYSize != chunk.height:
        errors.append(
            f"size={ds.RasterXSize}x{ds.RasterYSize}, "
            f"pricakovano {chunk.width}x{chunk.height}"
        )
    if not is_epsg4326(ds):
        errors.append("CRS ni EPSG:4326")

    gt = ds.GetGeoTransform()
    expected = (
        chunk.west,
        DEG_PER_PIXEL,
        0.0,
        chunk.north,
        0.0,
        -DEG_PER_PIXEL,
    )
    labels = ("west", "pixelX", "rotX", "north", "rotY", "pixelY")
    for got, wanted, label in zip(gt, expected, labels):
        if abs(got - wanted) > 2e-10:
            errors.append(f"{label}={got:.15g}, pricakovano {wanted:.15g}")

    band = ds.GetRasterBand(1)
    dtype = gdal.GetDataTypeName(band.DataType)
    if dtype != "Float32":
        errors.append(f"dtype={dtype}, pricakovano Float32")
    ds = None

    if errors:
        raise RuntimeError("Chunk QA FAIL: " + "; ".join(errors))


def create_or_open_output(
    bil_path: Path,
    tile_name: str,
    resume_allowed: bool,
) -> tuple[object, bool]:
    west, south, east, north = tile_bounds(tile_name)

    if bil_path.exists():
        if not resume_allowed:
            raise FileExistsError(
                f"{bil_path} ze obstaja brez resumable state-a. "
                "Uporabi --overwrite, ce ga zelis ponovno izdelati."
            )
        ds = gdal.Open(str(bil_path), gdal.GA_Update)
        if ds is None:
            raise RuntimeError(f"Ne morem odpreti za resume: {bil_path}")
        return ds, False

    bil_path.parent.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("EHdr")
    ds = driver.Create(
        str(bil_path), PIXELS_PER_DEGREE, PIXELS_PER_DEGREE,
        1, gdal.GDT_Int16,
    )
    if ds is None:
        raise RuntimeError(f"EHdr create ni uspel: {bil_path}")
    ds.SetGeoTransform((west, DEG_PER_PIXEL, 0.0, north, 0.0, -DEG_PER_PIXEL))
    ds.SetProjection(epsg_wkt(4326))
    ds.SetMetadataItem("AREA_OR_POINT", "Area")
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA_INT16)
    band.FlushCache()
    ds.FlushCache()
    return ds, True


def write_chunk_to_bil(chunk_path: Path, out_ds, chunk: Chunk, stripe_rows: int | None = None) -> None:
    if stripe_rows is None:
        stripe_rows = STRIPE_ROWS
    src = gdal.Open(str(chunk_path), gdal.GA_ReadOnly)
    if src is None:
        raise RuntimeError(f"Ne morem odpreti {chunk_path}")
    src_band = src.GetRasterBand(1)
    src_nodata = src_band.GetNoDataValue()
    mask_band = src_band.GetMaskBand()
    mask_flags = src_band.GetMaskFlags()
    out_band = out_ds.GetRasterBand(1)

    for sy in range(0, chunk.height, stripe_rows):
        sh = min(stripe_rows, chunk.height - sy)
        arr = src_band.ReadAsArray(0, sy, chunk.width, sh)
        if arr is None:
            raise RuntimeError(f"ReadAsArray ni uspel: {chunk_path}, y={sy}")
        arr = np.asarray(arr, dtype=np.float32)
        invalid = ~np.isfinite(arr)

        if src_nodata is not None:
            invalid |= np.isclose(arr, float(src_nodata), rtol=0.0, atol=1e-5)
        # We explicitly request -32768 as service NoData; handle it even if the
        # TIFF does not expose a NoData metadata tag.
        invalid |= arr <= -32767.5

        if mask_band is not None and not (mask_flags & gdal.GMF_ALL_VALID):
            mask = mask_band.ReadAsArray(0, sy, chunk.width, sh)
            if mask is not None:
                invalid |= np.asarray(mask) == 0

        rounded = np.rint(arr)
        np.clip(rounded, -32767, 32767, out=rounded)
        out = rounded.astype(np.int16)
        out[invalid] = NODATA_INT16

        err = out_band.WriteArray(out, chunk.xoff, chunk.yoff + sy)
        if err not in (0, None):
            raise RuntimeError(f"WriteArray napaka {err}")

    out_band.FlushCache()
    out_ds.FlushCache()
    src = None


def load_state(state_path: Path, tile_name: str, cols: int, rows: int) -> set[str]:
    if not state_path.exists():
        return set()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    same_plan = (
        data.get("tile") == tile_name
        and data.get("cols") == cols
        and data.get("rows") == rows
        and data.get("pixels_per_degree") == PIXELS_PER_DEGREE
        and abs(float(data.get("arcsec", -1.0)) - ARCSEC) <= 1e-15
    )
    if not same_plan:
        raise RuntimeError(
            f"Kontrolna točka se ne ujema s trenutno ločljivostjo ali načrtom zahtev: {state_path}"
        )
    return set(data.get("completed", []))


def save_state(state_path: Path, tile_name: str, cols: int, rows: int, completed: set[str]) -> None:
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "tile": tile_name,
                "pixels_per_degree": PIXELS_PER_DEGREE,
                "arcsec": ARCSEC,
                "cols": cols,
                "rows": rows,
                "completed": sorted(completed),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(state_path)


def validate_bil(path: Path, tile_name: str) -> dict:
    ds = gdal.Open(str(path), gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"GDAL ne more odpreti {path}")

    west, south, east, north = tile_bounds(tile_name)
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    dtype = gdal.GetDataTypeName(band.DataType)
    nodata = band.GetNoDataValue()
    errors: list[str] = []

    if ds.RasterXSize != PIXELS_PER_DEGREE or ds.RasterYSize != PIXELS_PER_DEGREE:
        errors.append(f"size={ds.RasterXSize}x{ds.RasterYSize}")
    if dtype != "Int16":
        errors.append(f"dtype={dtype}")
    if nodata != NODATA_INT16:
        errors.append(f"NoData={nodata}")
    if not is_epsg4326(ds):
        errors.append("CRS ni EPSG:4326")

    expected_gt = (west, DEG_PER_PIXEL, 0.0, north, 0.0, -DEG_PER_PIXEL)
    for got, wanted, label in zip(
        gt, expected_gt, ("west", "pixelX", "rotX", "north", "rotY", "pixelY")
    ):
        if abs(got - wanted) > 2e-11:
            errors.append(f"{label}={got:.15g}, pricakovano {wanted:.15g}")

    expected_bytes = PIXELS_PER_DEGREE * PIXELS_PER_DEGREE * 2
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        errors.append(f"BIL bytes={actual_bytes:,}, pricakovano {expected_bytes:,}")

    ds = None
    if errors:
        raise RuntimeError(f"QA FAIL {path}: " + "; ".join(errors))

    return {
        "tile": tile_name,
        "arcsec": ARCSEC,
        "pixels": PIXELS_PER_DEGREE,
        "bytes": actual_bytes,
        "qa": "PASS",
    }


def prepare_chunk_worker(
    chunk: Chunk,
    tile_name: str,
    temp_dir: Path,
    service_url: str,
    timeout: int,
    retries: int,
) -> tuple[Chunk, Path, int]:
    """Download and validate one chunk in a worker thread. No BIL writes here."""
    chunk_path = temp_dir / f"{tile_name}_{chunk.key}.tif"
    url = build_export_url(service_url, chunk)
    last_exc: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            if chunk_path.exists():
                validate_chunk(chunk_path, chunk)
            else:
                part = chunk_path.with_suffix(chunk_path.suffix + ".part")
                try:
                    part.unlink()
                except FileNotFoundError:
                    pass

                req = Request(url, headers={"User-Agent": "RadioMobile-DMR1/1.1-parallel"})
                with urlopen(req, timeout=timeout) as response:
                    status = getattr(response, "status", 200)
                    if status != 200:
                        raise RuntimeError(f"HTTP {status}")
                    length_header = response.headers.get("Content-Length")
                    total_bytes = int(length_header) if length_header else None
                    downloaded = 0
                    with part.open("wb") as f:
                        while True:
                            block = response.read(4 * 1024 * 1024)
                            if not block:
                                break
                            f.write(block)
                            downloaded += len(block)
                    if downloaded < 1024:
                        raise RuntimeError(f"Premajhen odgovor ({downloaded} B)")
                    if total_bytes is not None and downloaded != total_bytes:
                        raise RuntimeError(
                            f"Nepopoln download: {downloaded} / {total_bytes} B"
                        )
                part.replace(chunk_path)
                validate_chunk(chunk_path, chunk)
                return chunk, chunk_path, downloaded

            # Existing valid temp chunk.
            return chunk, chunk_path, chunk_path.stat().st_size

        except Exception as exc:
            last_exc = exc
            for p in (chunk_path, chunk_path.with_suffix(chunk_path.suffix + ".part")):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            if attempt < retries:
                wait = min(60, 5 * (2 ** (attempt - 1)))
                time.sleep(wait)

    raise RuntimeError(
        f"{tile_name} {chunk.key}: download/QA ni uspel po {retries} poskusih: {last_exc}"
    )


def parallel_progress_line(
    tile_name: str,
    completed_count: int,
    total_chunks: int,
    overall_done_before_tile: int,
    overall_total_chunks: int,
    global_start: float,
    workers: int,
) -> str:
    tile_fraction = completed_count / max(1, total_chunks)
    overall_fraction = (overall_done_before_tile + completed_count) / max(1, overall_total_chunks)
    elapsed = max(0.001, time.perf_counter() - global_start)
    eta = elapsed * (1.0 - overall_fraction) / overall_fraction if overall_fraction > 0 else None
    return (
        f"{tile_name} | completed {completed_count:03d}/{total_chunks:03d} | "
        f"tile={100.0 * tile_fraction:6.2f}% | "
        f"overall={100.0 * overall_fraction:6.2f}% | "
        f"workers={workers} | ETA={fmt_duration(eta)}"
    )


def build_one_tile(
    tile_name: str,
    output_root: Path,
    service_url: str,
    cols: int,
    rows: int,
    timeout: int,
    retries: int,
    keep_temp: bool,
    overwrite: bool,
    overall_done_before_tile: int,
    overall_total_chunks: int,
    global_start: float,
    download_workers: int,
) -> Path:
    radio_dir = output_root / "RADIO_MOBILE" / RESOLUTION_LABEL
    temp_dir = output_root / "TEMP" / tile_name
    bil_path = radio_dir / f"{tile_name}.bil"
    state_path = radio_dir / f"{tile_name}.progress.json"
    done_path = radio_dir / f"{tile_name}.done"

    radio_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    if overwrite:
        remove_related_bil_files(bil_path)
        for p in (state_path, done_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    if done_path.exists() and bil_path.exists() and not overwrite:
        result = validate_bil(bil_path, tile_name)
        print(
            f"{tile_name}: ze dokoncan, QA={result['qa']} "
            f"({result['bytes']/1024/1024/1024:.3f} GiB)"
        )
        return bil_path

    chunks = chunk_plan(tile_name, cols, rows)
    completed = load_state(state_path, tile_name, cols, rows)
    if completed and not bil_path.exists():
        raise RuntimeError(
            f"Checkpoint obstaja, BIL pa manjka: {state_path}. "
            "Izbrisi checkpoint ali uporabi --overwrite."
        )

    # A checkpoint with zero completed chunks is still a valid resumable state
    # (e.g. the very first HTTP request failed after the BIL was created).
    out_ds, created = create_or_open_output(
        bil_path, tile_name, resume_allowed=state_path.exists()
    )
    if created:
        save_state(state_path, tile_name, cols, rows, completed)

    total = len(chunks)
    print(
        f"\n=== {tile_name} ===\n"
        f"Grid: {PIXELS_PER_DEGREE} x {PIXELS_PER_DEGREE} | exact {ARCSEC:.12g}\"\n"
        f"Chunks: {cols} x {rows} = {total}; "
        f"chunk={chunks[0].width}x{chunks[0].height} px\n"
        f"Resume: {len(completed)}/{total} chunkov ze koncanih"
    )

    remaining = [chunk for chunk in chunks if chunk.key not in completed]
    completed_count = len(completed)

    if completed_count:
        print(
            parallel_progress_line(
                tile_name, completed_count, total,
                overall_done_before_tile, overall_total_chunks,
                global_start, download_workers,
            ),
            flush=True,
        )

    if remaining:
        print(
            f"Parallel download/QA: {download_workers} workerjev; "
            "BIL zapis ostane enoniten (varno za GDAL/EHdr)."
        )

        with ThreadPoolExecutor(max_workers=download_workers) as pool:
            future_map = {
                pool.submit(
                    prepare_chunk_worker,
                    chunk, tile_name, temp_dir, service_url, timeout, retries,
                ): chunk
                for chunk in remaining
            }

            try:
                for future in as_completed(future_map):
                    chunk = future_map[future]
                    try:
                        _chunk, chunk_path, _downloaded = future.result()
                        # Deliberately serialize writes to the single EHdr dataset.
                        write_chunk_to_bil(chunk_path, out_ds, chunk)
                    except Exception:
                        for f in future_map:
                            f.cancel()
                        raise

                    completed.add(chunk.key)
                    completed_count += 1
                    save_state(state_path, tile_name, cols, rows, completed)

                    if not keep_temp:
                        try:
                            chunk_path.unlink()
                        except FileNotFoundError:
                            pass

                    print(
                        parallel_progress_line(
                            tile_name, completed_count, total,
                            overall_done_before_tile, overall_total_chunks,
                            global_start, download_workers,
                        ),
                        flush=True,
                    )
            except Exception:
                out_ds.FlushCache()
                out_ds = None
                raise

    print()
    out_ds.FlushCache()
    out_ds = None

    qa = validate_bil(bil_path, tile_name)
    state_path.unlink(missing_ok=True)
    done_path.write_text(
        json.dumps(qa, indent=2),
        encoding="utf-8",
    )
    print(
        f"{tile_name}: QA PASS | {qa['pixels']}x{qa['pixels']} | "
        f"{qa['bytes']/1024/1024/1024:.3f} GiB"
    )
    return bil_path


def build_vrt(output_root: Path, selected_tiles: tuple[str, ...]) -> Path | None:
    folder = output_root / "RADIO_MOBILE" / RESOLUTION_LABEL
    paths = [folder / f"{tile}.bil" for tile in selected_tiles]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None
    vrt_path = folder / f"Slovenija_DMR1_{RESOLUTION_LABEL}_TILED.vrt"
    options = gdal.BuildVRTOptions(
        srcNodata=NODATA_INT16,
        VRTNodata=NODATA_INT16,
        resolution="highest",
    )
    vrt = gdal.BuildVRT(str(vrt_path), [str(p) for p in paths], options=options)
    if vrt is None:
        raise RuntimeError("BuildVRT ni uspel")
    vrt.FlushCache()
    vrt = None
    print(f"VRT: {vrt_path}")
    return vrt_path


def bilinear_reference_values(ref_band, ref_gt, xs: np.ndarray, y: float) -> np.ndarray:
    # Pixel-center coordinates in reference raster.
    px = (xs - ref_gt[0]) / ref_gt[1] - 0.5
    py = (y - ref_gt[3]) / ref_gt[5] - 0.5
    c0 = np.floor(px).astype(np.int64)
    r0 = int(math.floor(py))
    fx = px - c0
    fy = py - r0

    out = np.full(xs.shape, np.nan, dtype=np.float64)
    if r0 < 0 or r0 + 1 >= ref_band.YSize:
        return out

    valid_cols = (c0 >= 0) & (c0 + 1 < ref_band.XSize)
    if not valid_cols.any():
        return out

    valid_idx = np.where(valid_cols)[0]
    cc = c0[valid_cols]
    minc = int(cc.min())
    maxc = int(cc.max()) + 1
    width = maxc - minc + 1

    row0 = ref_band.ReadAsArray(minc, r0, width, 1)
    row1 = ref_band.ReadAsArray(minc, r0 + 1, width, 1)
    if row0 is None or row1 is None:
        return out
    row0 = np.asarray(row0[0], dtype=np.float64)
    row1 = np.asarray(row1[0], dtype=np.float64)

    i0 = cc - minc
    v00 = row0[i0]
    v10 = row0[i0 + 1]
    v01 = row1[i0]
    v11 = row1[i0 + 1]
    local_fx = fx[valid_cols]

    top = v00 * (1.0 - local_fx) + v10 * local_fx
    bottom = v01 * (1.0 - local_fx) + v11 * local_fx
    vals = top * (1.0 - fy) + bottom * fy

    nd = ref_band.GetNoDataValue()
    invalid = ~np.isfinite(vals)
    if nd is not None:
        invalid |= (
            np.isclose(v00, nd) | np.isclose(v10, nd) |
            np.isclose(v01, nd) | np.isclose(v11, nd)
        )
    vals[invalid] = np.nan
    out[valid_idx] = vals
    return out


def compare_to_reference(
    bil_path: Path,
    reference_path: Path,
    tile_name: str,
    target_samples: int,
) -> dict:
    new_ds = gdal.Open(str(bil_path), gdal.GA_ReadOnly)
    ref_ds = gdal.Open(str(reference_path), gdal.GA_ReadOnly)
    if new_ds is None or ref_ds is None:
        raise RuntimeError("Primerjalni GDAL open ni uspel")

    new_band = new_ds.GetRasterBand(1)
    ref_band = ref_ds.GetRasterBand(1)
    new_nd = new_band.GetNoDataValue()
    new_gt = new_ds.GetGeoTransform()
    ref_gt = ref_ds.GetGeoTransform()

    side = max(16, int(round(math.sqrt(target_samples))))
    rows = np.linspace(0, new_ds.RasterYSize - 1, side + 2, dtype=np.int64)[1:-1]
    cols = np.linspace(0, new_ds.RasterXSize - 1, side + 2, dtype=np.int64)[1:-1]
    xs = new_gt[0] + (cols.astype(np.float64) + 0.5) * new_gt[1]

    diff_parts: list[np.ndarray] = []
    for row in rows:
        row_arr = new_band.ReadAsArray(0, int(row), new_ds.RasterXSize, 1)
        if row_arr is None:
            continue
        nv = np.asarray(row_arr[0][cols], dtype=np.float64)
        y = new_gt[3] + (float(row) + 0.5) * new_gt[5]
        rv = bilinear_reference_values(ref_band, ref_gt, xs, y)

        valid = np.isfinite(nv) & np.isfinite(rv)
        if new_nd is not None:
            valid &= ~np.isclose(nv, new_nd)
        if valid.any():
            diff_parts.append(nv[valid] - rv[valid])

    new_ds = None
    ref_ds = None

    if not diff_parts:
        return {
            "tile": tile_name,
            "samples": 0,
            "status": "NO_VALID_OVERLAP",
        }

    d = np.concatenate(diff_parts)
    ad = np.abs(d)
    result = {
        "tile": tile_name,
        "samples": int(d.size),
        "status": "OK",
        "bias_m": float(d.mean()),
        "mae_m": float(ad.mean()),
        "rmse_m": float(np.sqrt(np.mean(d * d))),
        "median_abs_m": float(np.median(ad)),
        "p90_abs_m": float(np.percentile(ad, 90)),
        "p95_abs_m": float(np.percentile(ad, 95)),
        "p99_abs_m": float(np.percentile(ad, 99)),
        "max_abs_m": float(ad.max()),
        "within_1m_pct": float((ad <= 1.0).mean() * 100.0),
        "within_2m_pct": float((ad <= 2.0).mean() * 100.0),
        "within_5m_pct": float((ad <= 5.0).mean() * 100.0),
        "over_10m_pct": float((ad > 10.0).mean() * 100.0),
    }
    return result


def write_compare_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields = [
        "tile", "samples", "status", "bias_m", "mae_m", "rmse_m",
        "median_abs_m", "p90_abs_m", "p95_abs_m", "p99_abs_m",
        "max_abs_m", "within_1m_pct", "within_2m_pct", "within_5m_pct",
        "over_10m_pct",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"Primerjalni CSV: {path}")


def resolve_tiles(args) -> tuple[str, ...]:
    if args.all_tiles:
        if args.tile:
            raise ValueError("Uporabi --all-tiles ALI --tile, ne obojega.")
        return SLOVENIA_TILES
    if not args.tile:
        # Deliberately safe default for the first production test.
        return ("N45E014",)
    tiles = tuple(dict.fromkeys(t.upper() for t in args.tile))
    unknown = [t for t in tiles if t not in SLOVENIA_TILES]
    if unknown:
        raise ValueError(f"Nepodprti tile-i: {unknown}")
    return tiles



# ---------------------------------------------------------------------------
# Nastavljivi razrez brez prevzorčenja
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SplitPiece:
    src_tile: str
    row: int
    col: int
    src_xoff: int
    src_yoff: int
    width: int
    height: int
    west: float
    south: float
    east: float
    north: float
    out_dir: Path
    out_bil: Path

    @property
    def stem(self) -> str:
        return self.out_bil.stem


def split_output_root(output_root: Path, split_grid: int) -> Path:
    return output_root / "RADIO_MOBILE" / f"{RESOLUTION_LABEL}_SPLIT_{split_grid}x{split_grid}"


def make_split_piece(tile: str, row: int, col: int, output_root: Path, split_grid: int) -> SplitPiece:
    if PIXELS_PER_DEGREE % split_grid != 0:
        raise ValueError(
            f"Mreža {PIXELS_PER_DEGREE} px/° ni deljiva s --split-grid {split_grid}. "
            "Izberi drug razrez ali drugo ločljivost."
        )
    part_n = PIXELS_PER_DEGREE // split_grid
    part_deg = 1.0 / split_grid
    west0, south0, east0, north0 = tile_bounds(tile)
    west = west0 + col * part_deg
    east = west + part_deg
    north = north0 - row * part_deg
    south = north - part_deg
    stem = f"{tile}_q{row:02d}{col:02d}"
    out_dir = split_output_root(output_root, split_grid) / tile
    out_bil = out_dir / f"{stem}.bil"
    return SplitPiece(
        tile, row, col, col * part_n, row * part_n, part_n, part_n,
        west, south, east, north, out_dir, out_bil,
    )


def make_split_pieces(selected_tiles: tuple[str, ...], output_root: Path, split_grid: int) -> list[SplitPiece]:
    return [
        make_split_piece(tile, row, col, output_root, split_grid)
        for tile in selected_tiles
        for row in range(split_grid)
        for col in range(split_grid)
    ]


def check_source_tile_for_split(path: Path, tile: str) -> None:
    validate_bil(path, tile)


def split_piece(src_path: Path, piece: SplitPiece, overwrite: bool) -> None:
    piece.out_dir.mkdir(parents=True, exist_ok=True)
    if piece.out_bil.exists() and not overwrite:
        return
    opts = gdal.TranslateOptions(
        format="EHdr",
        srcWin=(piece.src_xoff, piece.src_yoff, piece.width, piece.height),
        outputType=gdal.GDT_Int16,
        noData=NODATA_INT16,
    )
    out = gdal.Translate(str(piece.out_bil), str(src_path), options=opts)
    if out is None:
        raise RuntimeError(f"gdal.Translate ni uspel: {piece.out_bil}")
    out.FlushCache()
    out = None


def validate_split_piece(piece: SplitPiece) -> dict:
    ds = gdal.Open(str(piece.out_bil), gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"QA: ne morem odpreti {piece.out_bil}")
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    errors: list[str] = []
    if ds.RasterXSize != piece.width or ds.RasterYSize != piece.height:
        errors.append(f"size={ds.RasterXSize}x{ds.RasterYSize}")
    if gdal.GetDataTypeName(band.DataType) != "Int16":
        errors.append(f"dtype={gdal.GetDataTypeName(band.DataType)}")
    if band.GetNoDataValue() != NODATA_INT16:
        errors.append(f"NoData={band.GetNoDataValue()}")
    checks = (
        (gt[0], piece.west, "west"), (gt[3], piece.north, "north"),
        (gt[1], DEG_PER_PIXEL, "pxX"), (gt[5], -DEG_PER_PIXEL, "pxY"),
        (gt[2], 0.0, "rotX"), (gt[4], 0.0, "rotY"),
    )
    for got, exp, label in checks:
        if abs(got - exp) > 1e-11:
            errors.append(f"{label}={got}, pričakovano={exp}")
    expected_bytes = piece.width * piece.height * 2
    actual_bytes = piece.out_bil.stat().st_size
    if actual_bytes != expected_bytes:
        errors.append(f"bytes={actual_bytes}, pričakovano={expected_bytes}")
    ds = None
    if errors:
        raise RuntimeError(f"QA FAIL {piece.out_bil}: " + "; ".join(errors))
    return {
        "piece": piece.stem,
        "source_tile": piece.src_tile,
        "row": piece.row,
        "col": piece.col,
        "west": piece.west,
        "south": piece.south,
        "east": piece.east,
        "north": piece.north,
        "pixels_x": piece.width,
        "pixels_y": piece.height,
        "arcsec": ARCSEC,
        "bytes": actual_bytes,
        "structural_qa": "PASS",
    }


def build_split_vrt(output_root: Path, pieces: list[SplitPiece], split_grid: int) -> Path:
    root = split_output_root(output_root, split_grid)
    vrt_path = root / f"Slovenija_DMR1_{RESOLUTION_LABEL}_SPLIT.vrt"
    inputs = [str(p.out_bil) for p in pieces if p.out_bil.exists()]
    if not inputs:
        raise FileNotFoundError("Ni razrezanih BIL datotek za VRT.")
    opts = gdal.BuildVRTOptions(
        resolution="highest", srcNodata=NODATA_INT16, VRTNodata=NODATA_INT16
    )
    vrt = gdal.BuildVRT(str(vrt_path), inputs, options=opts)
    if vrt is None:
        raise RuntimeError("BuildVRT razrezanih BIL ni uspel.")
    vrt.FlushCache()
    vrt = None
    return vrt_path


def check_split_seams(pieces: list[SplitPiece], split_grid: int) -> list[dict]:
    rows: list[dict] = []
    by_tile: dict[str, list[SplitPiece]] = {}
    for p in pieces:
        by_tile.setdefault(p.src_tile, []).append(p)
    for tile, tile_pieces in by_tile.items():
        lookup = {(p.row, p.col): p for p in tile_pieces}
        for r in range(split_grid):
            for c in range(split_grid - 1):
                a, b = lookup[(r, c)], lookup[(r, c + 1)]
                gap = b.west - a.east
                rows.append({
                    "tile": tile, "type": "vertical", "a": a.stem, "b": b.stem,
                    "gap_deg": gap, "status": "PASS" if abs(gap) < 1e-12 else "FAIL",
                })
        for r in range(split_grid - 1):
            for c in range(split_grid):
                a, b = lookup[(r, c)], lookup[(r + 1, c)]
                gap = a.south - b.north
                rows.append({
                    "tile": tile, "type": "horizontal", "a": a.stem, "b": b.stem,
                    "gap_deg": gap, "status": "PASS" if abs(gap) < 1e-12 else "FAIL",
                })
    bad = [r for r in rows if r["status"] != "PASS"]
    if bad:
        raise RuntimeError(f"QA robov FAIL: {len(bad)} robov ni poravnanih.")
    return rows


def exact_split_compare(
    source_root: Path,
    pieces: list[SplitPiece],
    samples_per_piece: int,
) -> list[dict]:
    results: list[dict] = []
    side = max(3, int(round(math.sqrt(samples_per_piece))))
    by_tile: dict[str, list[SplitPiece]] = {}
    for p in pieces:
        by_tile.setdefault(p.src_tile, []).append(p)
    for tile, tile_pieces in by_tile.items():
        src_ds = gdal.Open(str(source_root / f"{tile}.bil"), gdal.GA_ReadOnly)
        if src_ds is None:
            raise RuntimeError(f"Ne morem odpreti izvornega BIL: {tile}")
        src_band = src_ds.GetRasterBand(1)
        for piece in tile_pieces:
            dst_ds = gdal.Open(str(piece.out_bil), gdal.GA_ReadOnly)
            dst_band = dst_ds.GetRasterBand(1)
            rows_idx = np.linspace(0, piece.height - 1, side + 2, dtype=np.int64)[1:-1]
            cols_idx = np.linspace(0, piece.width - 1, side + 2, dtype=np.int64)[1:-1]
            compared = mismatches = max_abs = 0
            for rr in rows_idx:
                src_row = src_band.ReadAsArray(
                    piece.src_xoff, piece.src_yoff + int(rr), piece.width, 1
                )[0]
                dst_row = dst_band.ReadAsArray(0, int(rr), piece.width, 1)[0]
                d = src_row[cols_idx].astype(np.int32) - dst_row[cols_idx].astype(np.int32)
                compared += d.size
                mismatches += int(np.count_nonzero(d))
                if d.size:
                    max_abs = max(max_abs, int(np.max(np.abs(d))))
            dst_ds = None
            status = "PASS" if mismatches == 0 and max_abs == 0 else "FAIL"
            results.append({
                "piece": piece.stem, "source_tile": tile, "samples": compared,
                "mismatches": mismatches, "max_abs_difference_m": max_abs,
                "status": status,
            })
            if status != "PASS":
                raise RuntimeError(
                    f"Brezizgubni QA FAIL {piece.stem}: mismatch={mismatches}, max_abs={max_abs}"
                )
        src_ds = None
    return results


def write_generic_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)


def run_split_pipeline(
    output_root: Path,
    selected_tiles: tuple[str, ...],
    split_grid: int,
    overwrite: bool,
    samples_per_piece: int,
    qa_only: bool = False,
) -> None:
    if split_grid < 1:
        raise ValueError("--split-grid mora biti >= 1")
    if PIXELS_PER_DEGREE % split_grid != 0:
        raise ValueError(
            f"{PIXELS_PER_DEGREE} px/° ni deljivo s split-grid={split_grid}."
        )
    source_root = output_root / "RADIO_MOBILE" / RESOLUTION_LABEL
    for tile in selected_tiles:
        src = source_root / f"{tile}.bil"
        if not src.exists():
            raise FileNotFoundError(src)
        check_source_tile_for_split(src, tile)

    pieces = make_split_pieces(selected_tiles, output_root, split_grid)
    if not qa_only:
        print("\n=== RAZREZ BIL ===")
        total = len(pieces)
        start = time.perf_counter()
        for i, piece in enumerate(pieces, 1):
            src_path = source_root / f"{piece.src_tile}.bil"
            split_piece(src_path, piece, overwrite)
            validate_split_piece(piece)
            elapsed = time.perf_counter() - start
            rate = i / elapsed if elapsed > 0 else 0.0
            eta = (total - i) / rate if rate > 0 else float("nan")
            print(
                f"\r[{i:3d}/{total}] {100*i/total:6.2f}% | {piece.stem:18s} | ETA={fmt_duration(eta)}",
                end="", flush=True,
            )
        print()

    print("\n=== QA RAZREZA ===")
    structural_rows = [validate_split_piece(p) for p in pieces]
    seam_rows = check_split_seams(pieces, split_grid)
    vrt_path = build_split_vrt(output_root, pieces, split_grid)
    compare_rows = exact_split_compare(source_root, pieces, samples_per_piece)
    mismatch_total = sum(r["mismatches"] for r in compare_rows)
    max_abs = max((r["max_abs_difference_m"] for r in compare_rows), default=0)
    root = split_output_root(output_root, split_grid)
    write_generic_csv(root / "split_structural_qa.csv", structural_rows)
    write_generic_csv(root / "split_seam_qa.csv", seam_rows)
    write_generic_csv(root / "split_lossless_pixel_qa.csv", compare_rows)
    print(f"Strukturni QA: PASS ({len(structural_rows)} kosov)")
    print(f"QA robov: PASS ({len(seam_rows)} preverjanj)")
    print(f"Brezizgubna primerjava: PASS | mismatches={mismatch_total} | max_abs={max_abs} m")
    print(f"VRT: {vrt_path}")


# ---------------------------------------------------------------------------
# Nastavitev ločljivosti in uporabniški vmesnik
# ---------------------------------------------------------------------------

def make_resolution_label(arcsec: float) -> str:
    inv = 1.0 / arcsec if arcsec > 0 else 0.0
    if inv >= 1 and abs(inv - round(inv)) < 1e-10:
        return f"1over{int(round(inv))}"
    s = f"{arcsec:.12g}".replace(".", "p")
    return f"{s}arcsec"


def configure_resolution(args) -> tuple[float, float]:
    global PIXELS_PER_DEGREE, ARCSEC, DEG_PER_PIXEL, RESOLUTION_LABEL
    global INTERPOLATION_ENUM, STRIPE_ROWS

    requested = float(args.arcseconds)
    if not math.isfinite(requested) or requested <= 0:
        raise ValueError("--arcseconds mora biti pozitivno končno število.")
    if requested < RM_FINEST_ARCSEC - 1e-12 and not args.allow_finer_than_rm:
        raise ValueError(
            f"Za Radio Mobile Deluxe je najgostejša podprta nastavitev 1/9\" "
            f"({RM_FINEST_ARCSEC:.12g}\"). Za gostejši izhod uporabi "
            "--allow-finer-than-rm; tak izhod ni namenjen neposredni uporabi v RM."
        )

    pixels = int(round(3600.0 / requested))
    if pixels < 1:
        raise ValueError("Izbrana ločljivost je prevelika.")
    actual = 3600.0 / pixels
    if args.require_exact_arcseconds and abs(actual - requested) > 1e-12:
        raise ValueError(
            f"Zahtevana ločljivost {requested:.15g}\" ne razdeli 1° na celo število celic; "
            f"najbližja je {actual:.15g}\" ({pixels} px/°)."
        )

    PIXELS_PER_DEGREE = pixels
    ARCSEC = actual
    DEG_PER_PIXEL = 1.0 / pixels
    RESOLUTION_LABEL = args.resolution_label or make_resolution_label(actual)
    STRIPE_ROWS = args.stripe_rows
    INTERPOLATION_ENUM = {
        "bilinear": "RSP_BilinearInterpolation",
        "nearest": "RSP_NearestNeighbor",
        "cubic": "RSP_CubicConvolution",
        "majority": "RSP_Majority",
    }[args.interpolation]
    return requested, actual


def resolve_tiles(args) -> tuple[str, ...]:
    if args.all_tiles:
        if args.tile:
            raise ValueError("Uporabi --all-tiles ALI --tile, ne obojega.")
        return SLOVENIA_TILES
    if not args.tile:
        return ("N45E014",)
    tiles = tuple(dict.fromkeys(t.upper() for t in args.tile))
    unknown = [t for t in tiles if t not in SLOVENIA_TILES]
    if unknown:
        raise ValueError(f"Nepodprte ploščice: {unknown}")
    return tiles


def parse_args():
    p = argparse.ArgumentParser(
        description="GURS DMR1 -> nastavljiv BIL + razrez + QA za Radio Mobile"
    )
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--service-url", default=SERVICE_URL)
    p.add_argument("--tile", action="append", default=None, help="npr. N45E014; argument lahko ponoviš")
    p.add_argument("--all-tiles", action="store_true", help="vseh 8 slovenskih 1° ploščic")
    p.add_argument(
        "--stage",
        choices=("build", "qa", "compare", "split", "split-qa", "all"),
        default="all",
        help="all izvede build, qa, opcijsko compare, split in split-qa",
    )

    # Ločljivost
    p.add_argument("--arcseconds", type=float, default=DEFAULT_ARCSEC,
                   help="zahtevana ločljivost v ločnih sekundah; privzeto 1/9")
    p.add_argument("--require-exact-arcseconds", action="store_true",
                   help="zavrni nastavitev, ki 1° ne razdeli na celo število celic")
    p.add_argument("--allow-finer-than-rm", action="store_true",
                   help="dovoli gostejši izhod od 1/9\" za druge namene")
    p.add_argument("--resolution-label", default=None,
                   help="ročno ime mape ločljivosti; sicer se izračuna samodejno")
    p.add_argument("--interpolation", choices=("bilinear", "nearest", "cubic", "majority"),
                   default="bilinear", help="način prevzorčenja ImageServerja")

    # Prenos in sestava
    p.add_argument("--chunk-cols", type=int, default=DEFAULT_CHUNK_COLS)
    p.add_argument("--chunk-rows", type=int, default=DEFAULT_CHUNK_ROWS)
    p.add_argument("--download-workers", type=int, default=8)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--retries", type=int, default=5)
    p.add_argument("--stripe-rows", type=int, default=256,
                   help="vrstice naenkrat pri zapisovanju Float32 -> Int16")
    p.add_argument("--keep-temp", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-vrt", action="store_true", help="ne izdelaj skupnega VRT")

    # Primerjava
    p.add_argument("--reference-vrt", type=Path, default=DEFAULT_REFERENCE)
    p.add_argument("--compare-samples", type=int, default=50000)
    p.add_argument("--no-compare", action="store_true",
                   help="pri --stage all preskoči primerjavo z referenco")

    # Razrez
    p.add_argument("--split-grid", type=int, default=4,
                   help="N pomeni N x N kosov na 1° ploščico; privzeto 4")
    p.add_argument("--split-samples", type=int, default=4096,
                   help="vzorci na kos za preverjanje brezizgubnosti")
    p.add_argument("--no-split", action="store_true",
                   help="pri --stage all preskoči razrez")
    return p.parse_args()


def main() -> int:
    gdal.UseExceptions()
    args = parse_args()
    requested_arcsec, actual_arcsec = configure_resolution(args)
    selected_tiles = resolve_tiles(args)
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if args.chunk_cols < 1 or args.chunk_rows < 1:
        raise ValueError("--chunk-cols/--chunk-rows morata biti >= 1")
    if args.retries < 1 or args.download_workers < 1 or args.stripe_rows < 1:
        raise ValueError("retries, download-workers in stripe-rows morajo biti >= 1")
    if args.compare_samples < 16 or args.split_samples < 16:
        raise ValueError("primerjalno število vzorcev mora biti >= 16")

    chunks_per_tile = args.chunk_cols * args.chunk_rows
    overall_total_chunks = chunks_per_tile * len(selected_tiles)
    one_bil_bytes = PIXELS_PER_DEGREE * PIXELS_PER_DEGREE * 2

    print(f"GDAL: {gdal.VersionInfo('--version')}")
    print(f"Vir: {args.service_url}")
    print(f"Izhod: {output_root}")
    print(f"Zahtevana ločljivost: {requested_arcsec:.12g}\"")
    print(f"Dejanska mrežna ločljivost: {actual_arcsec:.12g}\" | {PIXELS_PER_DEGREE} px/°")
    print(f"Oznaka: {RESOLUTION_LABEL}")
    print(f"Interpolacija: {args.interpolation}")
    print(f"BIL/ploščico: {one_bil_bytes/1024/1024/1024:.3f} GiB")
    print(f"Zahteve: {args.chunk_cols} x {args.chunk_rows} = {chunks_per_tile}/ploščico")
    print(f"Ploščice: {', '.join(selected_tiles)}")
    print(f"Vzporedni prenosi: {args.download_workers}")
    if actual_arcsec < RM_FINEST_ARCSEC - 1e-12:
        print("OPOZORILO: izhod je gostejši od 1/9\" in ni namenjen neposredni uporabi v Radio Mobile Deluxe.")

    global_start = time.perf_counter()
    source_folder = output_root / "RADIO_MOBILE" / RESOLUTION_LABEL

    if args.stage in ("build", "all"):
        for tile_index, tile_name in enumerate(selected_tiles):
            build_one_tile(
                tile_name=tile_name,
                output_root=output_root,
                service_url=args.service_url,
                cols=args.chunk_cols,
                rows=args.chunk_rows,
                timeout=args.timeout,
                retries=args.retries,
                keep_temp=args.keep_temp,
                overwrite=args.overwrite,
                overall_done_before_tile=tile_index * chunks_per_tile,
                overall_total_chunks=overall_total_chunks,
                global_start=global_start,
                download_workers=args.download_workers,
            )
        if not args.no_vrt:
            build_vrt(output_root, selected_tiles)

    if args.stage in ("qa", "all"):
        for tile in selected_tiles:
            qa = validate_bil(source_folder / f"{tile}.bil", tile)
            print(
                f"QA {tile}: PASS | {qa['pixels']}x{qa['pixels']} | "
                f"{qa['bytes']/1024/1024/1024:.3f} GiB"
            )
        if not args.no_vrt:
            build_vrt(output_root, selected_tiles)

    if args.stage == "compare" or (args.stage == "all" and not args.no_compare):
        ref = args.reference_vrt.resolve()
        if not ref.exists():
            print(f"Primerjava preskočena: referenca ne obstaja: {ref}")
        else:
            print(f"\nPrimerjava DMR1 {ARCSEC:.12g}\" z referenco:\n  {ref}")
            compare_rows: list[dict] = []
            for tile in selected_tiles:
                result = compare_to_reference(
                    source_folder / f"{tile}.bil", ref, tile, args.compare_samples
                )
                compare_rows.append(result)
                if result.get("status") == "OK":
                    print(
                        f"  {tile}: n={result['samples']:,}; MAE={result['mae_m']:.3f} m; "
                        f"RMSE={result['rmse_m']:.3f} m; P95={result['p95_abs_m']:.3f} m; "
                        f"bias={result['bias_m']:+.3f} m"
                    )
                else:
                    print(f"  {tile}: {result.get('status')}")
            write_compare_csv(
                output_root / "RADIO_MOBILE" / f"dmr1_{RESOLUTION_LABEL}_comparison.csv",
                compare_rows,
            )

    if args.stage == "split":
        run_split_pipeline(
            output_root, selected_tiles, args.split_grid, args.overwrite,
            args.split_samples, qa_only=False,
        )
    elif args.stage == "split-qa":
        run_split_pipeline(
            output_root, selected_tiles, args.split_grid, args.overwrite,
            args.split_samples, qa_only=True,
        )
    elif args.stage == "all" and not args.no_split:
        run_split_pipeline(
            output_root, selected_tiles, args.split_grid, args.overwrite,
            args.split_samples, qa_only=False,
        )

    print(f"\nKONČANO. Čas: {fmt_duration(time.perf_counter() - global_start)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nPrekinjeno. Kontrolna točka prenosa ostane; ob ponovnem zagonu se nadaljuje.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nNAPAKA: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
