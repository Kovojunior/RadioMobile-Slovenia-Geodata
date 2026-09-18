#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Slovenia Radio Mobile Deluxe LCV V1.0 - samostojni gradnik razredov 00..14.

Izdaja V1.0 znotraj Slovenije ponovno izračuna vse razrede 00..14 iz izvornih podatkov.
Ne potrebuje predhodno izdelane klasifikacije z urbanima razredoma 13/14.

Vhodni viri
-----------
* MKGP RABA:
    osnovna naravna semantika, voda in gola tla
* ZGS sestoji:
    strukturni gozdni razredi 01..05
* GURS Pokritost tal:
    natančna geometrija posebnih negozdnih površin
* DRSV Hidrografija, ploskovne površinske vode:
    dodatna natančna geometrija stalnih vodnih površin
* GURS stavbe:
    samostojen izračun Urban LO 13 in Urban HI 14
* Maska Slovenije:
    binarni geografski raster slovenskega ozemlja; pripravljalna skripta ga izdela
    iz uradnih slovenskih podatkov
* Originalni Radio Mobile LCV (tipično starejši 3"/1201x1201 ali 1"):
    ohrani tujino in služi kot nadomestni vir samo tam, kjer novi slovenski
    viri nimajo dovolj podatkov. Morebitna stara 13/14 se znotraj Slovenije
    odstranita in nato izračunata na novo.

Ključna pravila V1.0
------------------
* Končni raster: 1" (3601 x 3601 na 1-stopinjski tile).
* Naravni poligoni: privzeto 8x vzorčenje = 0,125" = 64 podvzorcev/celico.
* Privzeti najmanjši površinski pragovi: 00 Water = 75 %, 12 Bare Ground = 75 %,
  razredi 01..11 = 25 %. Pragove je mogoče spreminjati globalno ali po razredu.
* 4210 Trstičje je združeno z razredom 08.
* Razred 10 = travinje in druga nizka odprta vegetacija.
* Razred 11 = njive in poljščine. RF-profil 00..14 se zapiše v landheight.dat.
* Urban Corridor se ne uporablja.
* 13/14 se izračunata iz GURS stavb s preverjeno urbano logiko:
    skupna analiza 0,25", notranja rasterizacija stavb 0,125",
    fizični krožni radij 45 m, utežen krožni filter.
* Urban LO/HI sta zadnja stopnja in zato ne moreta biti povožena z naravnimi
  razredi. Voda 00 je pred urbanizacijo zaščitena, enako kot v preverjenem
  urbanem modelu.

Priporočeni produkcijski zagon
------------------------------
Privzeto se vsi vhodni in izhodni podatki iščejo v podmapi ``workspace``
ob tej skripti. Pripravljalna skripta to strukturo izdela samodejno.

python build_lcv_slovenia_v1_0.py ^
  --supersample 8 --urban-radius 45 --urban-supersample 8 --workers 1 --rebuild-cache

Za drugo delovno mapo lahko okoljska spremenljivka RMSLO_WORKSPACE kaže na
poljubno lokacijo; posamezne poti je še vedno mogoče prepisati z argumenti CLI.

Izbirno za pregled v QGIS:
  --preview / --no-preview   končni GeoTIFF + nacionalni PREVIEW VRT
  --qa / --no-qa            QA GeoTIFF-i + nacionalna QA VRT-ja
Obe skupini sta privzeto izklopljeni.

Opomba o RAM
------------
Urbana stopnja uporablja skupno 0,25" analizo za cel 1-stopinjski tile.
Zato je privzeto en tile-worker. Več workerjev je dovoljeno, vendar lahko
zahteva zelo veliko pomnilnika.
"""

from __future__ import annotations

import argparse
import csv
import gc
import math
import os
import re
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from functools import partial
from multiprocessing import freeze_support
from pathlib import Path

import numpy as np
from osgeo import gdal

print = partial(print, flush=True)
gdal.UseExceptions()

PROGRAM_NAME = "Slovenia Radio Mobile Deluxe LCV"
PROGRAM_VERSION = "1.0"
PROGRAM_TAG = "V1_0"

# =============================================================================
# POTI / PRIVZETE VREDNOSTI
# =============================================================================

# Privzeto je projekt popolnoma prenosljiv: vsi večji podatki živijo v
# podmapi ``workspace`` ob skripti. Setup lahko lokacijo spremeni prek
# okoljske spremenljivke RMSLO_WORKSPACE, ne da bi bilo treba spreminjati kodo.
PROJECT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = Path(
    os.environ.get("RMSLO_WORKSPACE", str(PROJECT_DIR / "workspace"))
).expanduser().resolve()
GEODATA_ROOT = WORKSPACE_ROOT / "Geodata"
ROOT = GEODATA_ROOT / "Landcover"

DEFAULT_GURS = ROOT / "DTM" / "DTM_SLO_POKRITOST_TAL_LC_POKRITOSTTAL_P_poligon.shp"
DEFAULT_BUILDINGS = (
    ROOT / "Buildings" / "STAVBE" / "DTM_SLO_ZGRADBE_BU_STAVBE_P_20260822"
    / "DTM_SLO_ZGRADBE_BU_STAVBE_P_poligon.shp"
)
DEFAULT_RABA = GEODATA_ROOT / "RABA"
DEFAULT_ZGS = GEODATA_ROOT / "ZGS" / "SESTOJI_WFS" / "sestoji_slovenija.gpkg"
DEFAULT_HYDRO = GEODATA_ROOT / "Hidrografija" / "HIDRO5_OBM_PV.shp"
DEFAULT_MASK = (
    GEODATA_ROOT / "DMV5" / "OUTPUT" / "RADIO_MOBILE" / "0p25"
    / "Slovenija_DMV_0p25_TILED.vrt"
)
DEFAULT_ORIGINAL = ROOT / "Original"
DEFAULT_OUTPUT = ROOT / "GURS" / "V1_0"

LOCKED_ZGS_P70_LZHA = 383.16062176165804

# =============================================================================
# MREŽA / TILE-I
# =============================================================================

SLOVENIA_TILES = {
    "N45E013", "N45E014", "N45E015", "N45E016",
    "N46E013", "N46E014", "N46E015", "N46E016",
}
TILE_RE = re.compile(r"^([NS])(\d{2})([EW])(\d{3})\.lcv$", re.IGNORECASE)

FINAL_ARCSEC = 1.0
FINAL_N = 3601
DEFAULT_SUPERSAMPLE = 8
DEFAULT_BLOCK_SIZE = 512
DEFAULT_OTHER_THRESHOLD = 0.25
DEFAULT_WATER_THRESHOLD = 0.75
DEFAULT_BARE_THRESHOLD = 0.75
NATURAL_CLASS_IDS = tuple(range(13))

# Privzeti najmanjši delež končne 1" celice, potreben za posamezen
# naravni razred. 00 Water in 12 Bare Ground sta namerno strožja,
# ker imata v Radio Mobile 0 m / 0 % clutter. Ostali razredi ohranijo
# dosedanji prag 25 %. Vse je mogoče prepisati prek CLI.
DEFAULT_CLASS_THRESHOLDS = {
    0: DEFAULT_WATER_THRESHOLD,
    **{cls: DEFAULT_OTHER_THRESHOLD for cls in range(1, 12)},
    12: DEFAULT_BARE_THRESHOLD,
}
NODATA_CLASS = 255

TIE_ORDER_LOW_TO_HIGH = [10, 11, 9, 6, 8, 7, 2, 1, 3, 4, 5, 12, 0]

# =============================================================================
# URBANI MODEL 13/14 - zaklenjena preverjena logika
# =============================================================================

DEFAULT_URBAN_RADIUS_M = 45.0
DEFAULT_URBAN_LO_DENSITY = 0.0725
DEFAULT_TALL_DENSITY = 0.1375
DEFAULT_TALL_HEIGHT_M = 17.0
DEFAULT_DENSE_HI_DENSITY = 0.27
DEFAULT_DENSE_HI_HEIGHT_M = 11.0

DEFAULT_KERNEL_MODE = "weighted"
DEFAULT_KERNEL_WORKERS = 1
DEFAULT_KERNEL_ROW_BLOCK = 256
DEFAULT_KERNEL_QUADRATURE = 64

DEFAULT_URBAN_ANALYSIS_ARCSEC = 0.25
DEFAULT_URBAN_SUPERSAMPLE = 8

# Pri privzetem 1" izhodu pomeni 8x notranji raster stavb 0,125".
# Skupna analiza ostane 0,25", zato je faktor rasterizacije stavb znotraj
# skupne mreže 2x. V1.0 to vrednost izračuna iz --urban-analysis-arcsec in
# --urban-supersample, tako da sta oba parametra ročno nastavljiva.

# =============================================================================
# RAZREDI V1.0
# =============================================================================

# RF profil V1.0. Te vrednosti se zapišejo tudi v landheight.dat.
# Pozor: to so Radio Mobile RF clutter parametri in niso enaki pragovom
# površinske klasifikacije 25/75 % zgoraj.
CLASS_META = {
    0:  ("Water",                                      0.0,   0.0, "fixed"),
    1:  ("F1 Young closed / low dense forest",         8.0, 115.0, "V1.0 RF profile"),
    2:  ("F2 Low-medium open forest",                 12.0,  65.0, "V1.0 RF profile"),
    3:  ("F3 Medium closed forest",                   19.0, 130.0, "V1.0 RF profile"),
    4:  ("F4 Tall open / regeneration mosaic forest",24.0, 105.0, "V1.0 RF profile"),
    5:  ("F5 Tall dense / multilayer forest",         30.0, 140.0, "V1.0 RF profile"),
    6:  ("Open woody succession / young woodland",     4.0,  45.0, "V1.0 RF profile"),
    7:  ("Sparse taller tree cover / orchards",        6.0,  35.0, "V1.0 RF profile"),
    8:  ("Dense shrubland / dwarf pine / reedbed",     3.0,  65.0, "V1.0 RF profile"),
    9:  ("Structured perennial crops",                 3.0,  40.0, "V1.0 RF profile"),
    10: ("Grassland / low open vegetation",            2.0,  15.0, "V1.0 RF profile"),
    11: ("Arable land / field crops",                  2.0,  20.0, "V1.0 RF profile"),
    12: ("Bare Ground",                                0.0,   0.0, "fixed"),
    13: ("Urban and Built-up LO",                     10.0, 150.0, "V1.0 RF profile"),
    14: ("Urban and Built-up HI",                     20.0, 175.0, "V1.0 RF profile"),
}

# Posebne vrednosti QA rasterja. 0..100 ostane rezervirano za odstotek
# dominantnosti naravne klasifikacije.
QA_FALLBACK = 253
QA_URBAN = 254
QA_OUTSIDE = 255

PRIOR_ZGS_DISTRIBUTION = {
    1: {"stands": 25345, "area_ha": 32195.13, "area_pct": 2.7343508180, "lzha_mean": 8.63711375},
    2: {"stands": 43912, "area_ha": 121858.70, "area_pct": 10.3495291377, "lzha_mean": 121.33334756},
    3: {"stands": 80695, "area_ha": 188773.73, "area_pct": 16.0326609349, "lzha_mean": 245.07961992},
    4: {"stands": "", "area_ha": 433463.13, "area_pct": 36.8142717266, "lzha_mean": ""},
    5: {"stands": 95238, "area_ha": 401141.62, "area_pct": 34.0691873828, "lzha_mean": 418.99693181},
}

GURS_TO_RM = {
    11: 12, 12: 12, 13: 12, 14: 12,
    21: 3,
    22: 7, 23: 9, 24: 7, 25: 9,
    26: 8, 27: 8,
    31: 0, 32: 0,
    33: 12,
}
GURS_SPECIFIC_TO_RM = {k: v for k, v in GURS_TO_RM.items() if k != 21}

RABA_TO_RM = {
    1100: 11,
    1130: 10, 1131: 10,
    1160: 9, 1161: 9,
    1180: 11, 1181: 11,
    1211: 9, 1212: 9,
    1221: 7, 1222: 7, 1230: 7, 1240: 7,
    1300: 10, 1320: 10, 1321: 10, 1330: 10,
    1410: 6, 1420: 6, 1500: 6,
    1600: 10, 1610: 10,
    1800: 7,
    2000: 3,
    4100: 10,
    4210: 8,
    4220: 10,
    5000: 10,
    6000: 12,
    7000: 0,
}
RABA_PHYSICAL_TO_RM = {6000: 12, 7000: 0}

DEFAULT_HYDRO_SYMBOLS = ("11,02", "31,01", "15,02", "33", "34", "36", "37")

LEGACY_BASE_REMAP = {
    0: 0,
    1: 3, 2: 3, 3: 3, 4: 3, 5: 3,
    6: 6, 7: 7, 8: 8, 9: 6, 10: 10, 11: 11, 12: 12,
}


def safe_float(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else float("nan")
    except Exception:
        return float("nan")




def safe_int(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(float(v))
    except Exception:
        return None




def weighted_quantile(values, weights, q):
    v = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    mask = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[mask], w[mask]
    if v.size == 0:
        return float("nan")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cw = np.cumsum(w)
    idx = np.searchsorted(cw, q * cw[-1], side="left")
    return float(v[min(int(idx), len(v) - 1)])




def parse_tile_name(path: Path):
    m = TILE_RE.match(path.name)
    if not m:
        raise ValueError(f"Neveljavno LCV ime: {path.name}")
    ns, lat, ew, lon = m.groups()
    lat = int(lat)
    lon = int(lon)
    if ns.upper() == "S":
        lat = -lat
    if ew.upper() == "W":
        lon = -lon
    return lat, lon




def tile_geotransform(lat: int, lon: int, arcsec: float = FINAL_ARCSEC):
    step = arcsec / 3600.0
    return (
        lon - step / 2.0,
        step,
        0.0,
        lat + 1.0 + step / 2.0,
        0.0,
        -step,
    )




def create_mem_raster(width, height, geotransform, dtype=gdal.GDT_Byte, fill=255):
    ds = gdal.GetDriverByName("MEM").Create("", width, height, 1, dtype)
    ds.SetProjection("EPSG:4326")
    ds.SetGeoTransform(geotransform)
    ds.GetRasterBand(1).Fill(fill)
    return ds




def _vector_fields(layer):
    d = layer.GetLayerDefn()
    return [d.GetFieldDefn(i).GetName() for i in range(d.GetFieldCount())]




def discover_vector_layer(source: Path, wanted_fields: tuple[str, ...]):
    """Find best vector dataset/layer containing all wanted fields."""
    source = source.resolve()
    if source.is_file():
        candidates = [source]
    elif source.is_dir():
        exts = {".shp", ".gpkg", ".sqlite", ".db", ".fgb", ".geojson"}
        candidates = [
            p for p in source.rglob("*")
            if p.is_file() and p.suffix.lower() in exts
        ]
    else:
        raise FileNotFoundError(source)

    best = None
    wanted = tuple(x.lower() for x in wanted_fields)
    for p in candidates:
        try:
            ds = gdal.OpenEx(str(p), gdal.OF_VECTOR | gdal.OF_READONLY)
            if ds is None:
                continue
            for i in range(ds.GetLayerCount()):
                layer = ds.GetLayer(i)
                if layer is None:
                    continue
                fields = _vector_fields(layer)
                fmap = {f.lower(): f for f in fields}
                if not all(f in fmap for f in wanted):
                    continue
                try:
                    count = int(layer.GetFeatureCount())
                except Exception:
                    count = 0
                size = p.stat().st_size if p.exists() else 0
                key = (count, size)
                if best is None or key > best[0]:
                    best = (key, p, layer.GetName(), fmap)
            ds = None
        except Exception:
            continue

    if best is None:
        raise RuntimeError(
            f"V {source} ne najdem vector layerja s polji: "
            + ", ".join(wanted_fields)
        )
    return best[1], best[2], best[3]




def sql_ident(name: str):
    return '"' + str(name).replace('"', '""') + '"'




def write_csv(path: Path, rows):
    rows = list(rows)
    if not rows:
        return
    fields = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)




def classify_zgs_values(rfaza, sklep, lzha, national_p70):
    """Reference Python implementation; SQL preparation below mirrors this."""
    if rfaza == 1:
        return 1 if sklep in (1, 2) else 2
    if rfaza in (8, 9, 10):
        return 2
    if rfaza == 2:
        return 3 if sklep in (1, 2) else 2
    if rfaza == 3:
        return 5 if sklep in (1, 2) else 4
    if rfaza == 4:
        return 4
    if rfaza in (5, 6, 11):
        return 5
    if rfaza == 7:
        if sklep in (1, 2):
            return 5
        if math.isfinite(lzha) and lzha >= national_p70:
            return 5
        return 4
    if math.isfinite(lzha):
        if lzha >= national_p70:
            return 5
        if lzha >= 200:
            return 4
    return 2




def calculate_zgs_p70(source: Path):
    src, layer_name, fmap = discover_vector_layer(
        source, ("povrsina", "lzsku", "rfaza", "sklep")
    )
    ds = gdal.OpenEx(str(src), gdal.OF_VECTOR | gdal.OF_READONLY)
    layer = ds.GetLayerByName(layer_name)
    area_field = fmap["povrsina"]
    lz_field = fmap["lzsku"]

    values = []
    weights = []
    layer.ResetReading()
    for feat in layer:
        area = safe_float(feat.GetField(area_field))
        lz = safe_float(feat.GetField(lz_field))
        if math.isfinite(area) and area > 0 and math.isfinite(lz) and lz >= 0:
            values.append(lz / area)
            weights.append(area)
    ds = None

    p70 = weighted_quantile(values, weights, 0.70)
    if not math.isfinite(p70):
        raise RuntimeError("ZGS P70 LZ/ha izracun ni uspel.")
    return p70




def prepare_zgs(source: Path, destination: Path, p70: float):
    print("Pripravljam ZGS sestoje -> RF razredi 01..05 ...")
    src, layer_name, fmap = discover_vector_layer(
        source, ("rfaza", "sklep", "povrsina", "lzsku")
    )
    rf = sql_ident(fmap["rfaza"])
    sk = sql_ident(fmap["sklep"])
    area = sql_ident(fmap["povrsina"])
    lz = sql_ident(fmap["lzsku"])

    lzha = (
        f"CASE WHEN CAST({area} AS REAL) > 0 "
        f"THEN CAST({lz} AS REAL) / CAST({area} AS REAL) ELSE NULL END"
    )

    cls = f"""
        CASE
            WHEN CAST({rf} AS INTEGER) = 1
                AND CAST({sk} AS INTEGER) IN (1,2) THEN 1
            WHEN CAST({rf} AS INTEGER) = 1 THEN 2

            WHEN CAST({rf} AS INTEGER) IN (8,9,10) THEN 2

            WHEN CAST({rf} AS INTEGER) = 2
                AND CAST({sk} AS INTEGER) IN (1,2) THEN 3
            WHEN CAST({rf} AS INTEGER) = 2 THEN 2

            WHEN CAST({rf} AS INTEGER) = 3
                AND CAST({sk} AS INTEGER) IN (1,2) THEN 5
            WHEN CAST({rf} AS INTEGER) = 3 THEN 4

            WHEN CAST({rf} AS INTEGER) = 4 THEN 4
            WHEN CAST({rf} AS INTEGER) IN (5,6,11) THEN 5

            WHEN CAST({rf} AS INTEGER) = 7
                AND CAST({sk} AS INTEGER) IN (1,2) THEN 5
            WHEN CAST({rf} AS INTEGER) = 7
                AND ({lzha}) >= {p70:.12f} THEN 5
            WHEN CAST({rf} AS INTEGER) = 7 THEN 4

            WHEN ({lzha}) >= {p70:.12f} THEN 5
            WHEN ({lzha}) >= 200.0 THEN 4
            ELSE 2
        END
    """

    sql = f'''
        SELECT
            *,
            CAST(({cls}) AS INTEGER) AS RM_CLASS,
            CAST(({lzha}) AS REAL) AS LZ_HA
        FROM {sql_ident(layer_name)}
    '''

    if destination.exists():
        destination.unlink()
    options = gdal.VectorTranslateOptions(
        format="GPKG",
        dstSRS="EPSG:4326",
        # ZGS WFS arrives as OGR MultiSurface.  PROMOTE_TO_MULTI keeps that
        # container type; for reliable polygon rasterization force a linear
        # representation.  GDAL converts MULTISURFACE -> MULTIPOLYGON.
        geometryType="CONVERT_TO_LINEAR",
        SQLStatement=sql,
        SQLDialect="SQLITE",
        layerName="zgs_forest",
        layerCreationOptions=["SPATIAL_INDEX=YES"],
    )
    result = gdal.VectorTranslate(str(destination), str(src), options=options)
    if result is None:
        raise RuntimeError("Priprava ZGS layerja ni uspela.")
    result = None
    print(f"  OK | P70 LZ/ha = {p70:.6f} m3/ha")




def _prepare_mapped_layer(
    source: Path,
    destination: Path,
    *,
    source_field_lower: str,
    mapping: dict[int, int],
    out_layer_name: str,
):
    src, layer_name, fmap = discover_vector_layer(source, (source_field_lower,))
    field = fmap[source_field_lower]
    qf = sql_ident(field)
    cases = [
        f"WHEN CAST({qf} AS INTEGER) = {code} THEN {rm}"
        for code, rm in sorted(mapping.items())
    ]
    code_list = ",".join(str(code) for code in sorted(mapping))
    sql = f'''
        SELECT
            *,
            CASE {' '.join(cases)} ELSE 255 END AS RM_CLASS
        FROM {sql_ident(layer_name)}
        WHERE CAST({qf} AS INTEGER) IN ({code_list})
    '''
    if destination.exists():
        destination.unlink()
    options = gdal.VectorTranslateOptions(
        format="GPKG",
        dstSRS="EPSG:4326",
        geometryType="PROMOTE_TO_MULTI",
        SQLStatement=sql,
        SQLDialect="SQLITE",
        layerName=out_layer_name,
        layerCreationOptions=["SPATIAL_INDEX=YES"],
    )
    result = gdal.VectorTranslate(str(destination), str(src), options=options)
    if result is None:
        raise RuntimeError(f"Priprava {out_layer_name} ni uspela.")
    result = None




def rasterize_slovenia_mask(mask_dem: Path, lat: int, lon: int):
    src = gdal.Open(str(mask_dem))
    if src is None:
        raise RuntimeError(f"Ne morem odpreti maske Slovenije: {mask_dem}")
    band = src.GetRasterBand(1)
    src_nodata = band.GetNoDataValue()
    if src_nodata is None:
        raise RuntimeError(
            "Raster maske Slovenije nima NoData; potrebujem vir z NoData zunaj Slovenije."
        )

    gt = tile_geotransform(lat, lon, FINAL_ARCSEC)
    west = gt[0]
    north = gt[3]
    east = west + FINAL_N * gt[1]
    south = north + FINAL_N * gt[5]
    dst_nodata = -32768.0

    warped = gdal.Warp(
        "",
        src,
        options=gdal.WarpOptions(
            format="MEM",
            dstSRS="EPSG:4326",
            outputBounds=(west, south, east, north),
            width=FINAL_N,
            height=FINAL_N,
            resampleAlg=gdal.GRA_NearestNeighbour,
            srcNodata=src_nodata,
            dstNodata=dst_nodata,
            outputType=gdal.GDT_Float32,
            multithread=False,
            warpOptions=["NUM_THREADS=1"],
        ),
    )
    if warped is None:
        raise RuntimeError("Izdelava maske Slovenije ni uspela.")
    data = warped.GetRasterBand(1).ReadAsArray()
    valid = np.isfinite(data) & (data != dst_nodata)
    warped = None
    src = None
    return valid




def save_geotiff(array, path: Path, lat: int, lon: int, nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    dtype = gdal.GDT_Byte if array.dtype == np.uint8 else gdal.GDT_Float32
    ds = gdal.GetDriverByName("GTiff").Create(
        str(path),
        array.shape[1],
        array.shape[0],
        1,
        dtype,
        options=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
    )
    ds.SetProjection("EPSG:4326")
    ds.SetGeoTransform(tile_geotransform(lat, lon, FINAL_ARCSEC))
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(array)
    band.FlushCache()
    ds.FlushCache()
    ds = None




def _rasterize_into(
    ds,
    gpkg: Path,
    layer_name: str,
    *,
    where: str | None = None,
):
    options = gdal.RasterizeOptions(
        layers=[layer_name],
        attribute="RM_CLASS",
        where=where,
        allTouched=False,
    )
    result = gdal.Rasterize(ds, str(gpkg), options=options)
    if result is None:
        raise RuntimeError(f"Rasterizacija {layer_name} ni uspela.")





# =============================================================================
# PRIPRAVA VEKTORSKIH VIROV
# =============================================================================

def prepare_raba(source: Path, destination: Path):
    print("Pripravljam MKGP RABA - polna V1.0 semantika ...")
    _prepare_mapped_layer(
        source, destination,
        source_field_lower="raba_id",
        mapping=RABA_TO_RM,
        out_layer_name="raba",
    )
    print("  OK")


def prepare_raba_physical(source: Path, destination: Path):
    print("Pripravljam MKGP RABA - voda in gola tla ...")
    _prepare_mapped_layer(
        source, destination,
        source_field_lower="raba_id",
        mapping=RABA_PHYSICAL_TO_RM,
        out_layer_name="raba_physical",
    )
    print("  OK")


def prepare_gurs(source: Path, destination: Path):
    print("Pripravljam GURS Pokritost tal - posebne negozdne površine ...")
    _prepare_mapped_layer(
        source, destination,
        source_field_lower="vrsta_pok",
        mapping=GURS_SPECIFIC_TO_RM,
        out_layer_name="gurs_specific",
    )
    print("  OK")


def prepare_hydro(source: Path, destination: Path, symbols: tuple[str, ...]):
    # Pripravi samo izbrane ploskovne vodne tipe DRSV kot RM_CLASS=00.
    print("Pripravljam DRSV Hidrografijo - izbrane stalne vodne površine ...")
    src, layer_name, fmap = discover_vector_layer(source, ("simbol",))
    fs = sql_ident(fmap["simbol"])
    quoted = ",".join("'" + s.replace("'", "''") + "'" for s in symbols)
    sql = f"""
        SELECT
            *,
            0 AS RM_CLASS
        FROM {sql_ident(layer_name)}
        WHERE CAST({fs} AS TEXT) IN ({quoted})
    """
    if destination.exists():
        destination.unlink()
    options = gdal.VectorTranslateOptions(
        format="GPKG",
        dstSRS="EPSG:4326",
        dim="XY",
        geometryType="CONVERT_TO_LINEAR",
        SQLStatement=sql,
        SQLDialect="SQLITE",
        layerName="hydro_water",
        layerCreationOptions=["SPATIAL_INDEX=YES"],
    )
    result = gdal.VectorTranslate(str(destination), str(src), options=options)
    if result is None:
        raise RuntimeError("Priprava DRSV Hidrografije ni uspela.")
    result = None
    print("  OK | SIMBOL: " + ", ".join(symbols))


def load_original_lcv(path: Path, *, row_block: int = 512):
    """Naloži izvorni Radio Mobile LCV in ga po potrebi poravna na 1" mrežo.

    Standardne starejše Radio Mobile ploščice so pogosto 1201x1201 (3"), medtem
    ko je izhod V1.0 3601x3601 (1"). Izvorni LCV je v V1.0 samo osnovni vir za
    tujino in za redke vrzeli v slovenskih izvornih podatkih; 13/14 se znotraj
    Slovenije odstranita in ponovno izračunata iz GURS stavb. Zato je varen
    prevzorčenje po najbližjem sosedu na isto vozliščno 1-stopinjsko mrežo.
    """
    size = path.stat().st_size
    n = math.isqrt(size)
    if n * n != size:
        raise ValueError(
            f"{path.name}: LCV mora biti kvadratna Byte mreža; datoteka ima {size} B."
        )
    if n < 2:
        raise ValueError(f"{path.name}: neveljavna LCV mreža {n}x{n}.")

    src = np.fromfile(path, dtype=np.uint8).reshape(n, n)
    if n == FINAL_N:
        return src

    src_arcsec = 3600.0 / (n - 1)
    print(
        f"  {path.name}: izvorni RM LCV {n}x{n} (~{src_arcsec:g}\") "
        f"-> nearest resampling na {FINAL_N}x{FINAL_N} (1\")"
    )

    # Prvi in zadnji vzorec ostaneta točno na robovih 1-stopinjskega tile-a.
    # Blockwise izvedba omeji začasni RAM pri več vzporednih tile procesih.
    idx = np.rint(
        np.linspace(0, n - 1, FINAL_N, dtype=np.float64)
    ).astype(np.int32)
    out = np.empty((FINAL_N, FINAL_N), dtype=np.uint8)
    for y0 in range(0, FINAL_N, row_block):
        y1 = min(FINAL_N, y0 + row_block)
        rows = idx[y0:y1]
        out[y0:y1, :] = src[np.ix_(rows, idx)]
    return out

def remap_original_fallback(block: np.ndarray):
    out = block.copy()
    for old, new in LEGACY_BASE_REMAP.items():
        out[block == old] = new
    return out



# =============================================================================
# URBANI MODEL 13/14 IZ GURS STAVB
# =============================================================================


class ProgressLine:
    """Low-noise progress reporter.

    With inplace=True a single terminal line is rewritten using carriage
    return. With inplace=False (parallel tile mode), coarse milestone lines
    are printed so multiple worker processes do not constantly overwrite one
    another. This affects only terminal output, never calculation order.
    """

    def __init__(
        self,
        label: str,
        total: int | None = None,
        *,
        inplace: bool = True,
        min_interval: float = 0.35,
        coarse_percent: float = 10.0,
    ):
        self.label = label
        self.total = total
        self.inplace = inplace
        self.min_interval = min_interval
        self.coarse_percent = coarse_percent
        self._last_time = 0.0
        self._last_len = 0
        self._last_coarse = -1
        self._finished = False

    def _message(self, done: int | None = None, fraction: float | None = None):
        if fraction is None and done is not None and self.total:
            fraction = done / self.total
        if fraction is not None:
            fraction = max(0.0, min(1.0, float(fraction)))
            pct = 100.0 * fraction
            if done is not None and self.total:
                return f"{self.label}: {pct:6.2f} % [{done:,}/{self.total:,}]"
            return f"{self.label}: {pct:6.2f} %"
        if done is not None:
            return f"{self.label}: {done:,}"
        return self.label

    def update(
        self,
        done: int | None = None,
        *,
        fraction: float | None = None,
        force: bool = False,
    ):
        if self._finished:
            return
        now = time.monotonic()
        if not force and now - self._last_time < self.min_interval:
            return
        self._last_time = now
        msg = self._message(done, fraction)

        if self.inplace:
            padded = msg.ljust(self._last_len)
            print("\r" + padded, end="", flush=True)
            self._last_len = max(self._last_len, len(msg))
            return

        # Parallel tile mode: only coarse progress milestones become lines.
        frac = fraction
        if frac is None and done is not None and self.total:
            frac = done / self.total
        if frac is None:
            if force:
                print(msg)
            return
        bucket = int((100.0 * frac) // self.coarse_percent)
        if force or bucket > self._last_coarse:
            self._last_coarse = bucket
            print(msg)

    def finish(self, done: int | None = None):
        if self._finished:
            return
        if done is None and self.total is not None:
            done = self.total
        msg = self._message(done, 1.0)
        if self.inplace:
            print("\r" + msg.ljust(self._last_len))
        else:
            print(msg)
        self._finished = True




def grid_parameters(requested_arcsec: float):
    if requested_arcsec <= 0:
        raise ValueError("Arcseconds mora biti > 0.")

    intervals = round(3600.0 / requested_arcsec)
    if intervals < 1:
        raise ValueError("Neveljavna locljivost.")

    actual_arcsec = 3600.0 / intervals
    n = intervals + 1
    return n, actual_arcsec




def estimate_pixel_metres(lat_deg: float, arcsec: float):
    # Dober lokalni približek za Slovenijo.
    north_south = arcsec * 30.87
    east_west = (
        arcsec
        * 30.87
        * math.cos(math.radians(lat_deg))
    )
    return east_west, north_south




def prepare_buildings(source: Path, destination: Path):
    print()
    print("Pripravljam GURS stavbe ...")

    # STAN_KONST 2 = v gradnji, 3 = uporabno.
    # Footprint is retained even when VISINA is missing.
    sql = f'''
        SELECT
            BUI_DTM_ID,
            CASE
                WHEN VISINA > 0 AND VISINA < 300
                THEN VISINA
                ELSE 0
            END AS RM_HEIGHT,
            VIS_STATUS,
            STAN_KONST,
            HZ_REF_GEO,
            REF_GEOM,
            geometry
        FROM "{source.stem}"
        WHERE STAN_KONST IN (2, 3)
    '''

    options = gdal.VectorTranslateOptions(
        format="GPKG",
        dstSRS="EPSG:4326",
        geometryType="PROMOTE_TO_MULTI",
        SQLStatement=sql,
        SQLDialect="SQLITE",
        layerName="buildings",
        layerCreationOptions=["SPATIAL_INDEX=YES"],
    )

    result = gdal.VectorTranslate(
        str(destination), str(source), options=options
    )
    if result is None:
        raise RuntimeError("Priprava stavb ni uspela.")

    result = None
    print("  OK")




def extract_tile_buildings(
    source_gpkg: Path,
    destination_gpkg: Path,
    west: float,
    south: float,
    east: float,
    north: float,
):
    """Make a small indexed GPKG for one tile + RF halo."""

    options = gdal.VectorTranslateOptions(
        format="GPKG",
        layers=["buildings"],
        layerName="buildings",
        spatFilter=(west, south, east, north),
        layerCreationOptions=["SPATIAL_INDEX=YES"],
    )

    result = gdal.VectorTranslate(
        str(destination_gpkg), str(source_gpkg), options=options
    )
    if result is None:
        raise RuntimeError("Izrez stavb za tile ni uspel.")

    result = None




def _block_geotransform(
    ext_west: float,
    ext_north: float,
    output_step_deg: float,
    block_x: int,
    block_y: int,
    factor: int,
):
    high_step = output_step_deg / factor
    return (
        ext_west + block_x * output_step_deg,
        high_step,
        0.0,
        ext_north - block_y * output_step_deg,
        0.0,
        -high_step,
    )




def _downsample_mean(arr: np.ndarray, out_h: int, out_w: int, factor: int):
    return (
        arr.reshape(out_h, factor, out_w, factor)
        .mean(axis=(1, 3), dtype=np.float32)
        .astype(np.float32, copy=False)
    )




def rasterize_buildings_blockwise(
    buildings_gpkg: Path,
    lat: int,
    lon: int,
    target_n: int,
    target_arcsec: float,
    factor: int,
    radius_m: float,
    block_size: int,
    temp_dir: Path,
    *,
    progress_inplace: bool = True,
):
    """
    Returns three EXTENDED output-grid arrays:

      footprint_fraction
          Fraction 0..1 of each target pixel covered by a building.

      height_area
          Average of building height over the complete target pixel.
          Non-building and invalid-height subpixels contribute 0.

      valid_height_fraction
          Fraction 0..1 of target pixel covered by a building that has a
          valid RM_HEIGHT. This prevents missing heights from pulling the
          mean building height downward.

    The internal raster is factor times finer in each axis, but is created
    block-by-block; no giant full 0.125" raster is kept in RAM.
    """

    centre_lat = lat + 0.5
    px_x_m, px_y_m = estimate_pixel_metres(centre_lat, target_arcsec)

    halo_x = math.ceil(radius_m / px_x_m) + 2
    halo_y = math.ceil(radius_m / px_y_m) + 2

    ext_w = target_n + 2 * halo_x
    ext_h = target_n + 2 * halo_y

    step = target_arcsec / 3600.0

    base_gt = tile_geotransform(lat, lon, target_arcsec)
    ext_west = base_gt[0] - halo_x * step
    ext_north = base_gt[3] + halo_y * step
    ext_east = ext_west + ext_w * step
    ext_south = ext_north - ext_h * step

    print(
        f"    Building supersampling: {factor}x "
        f"({target_arcsec / factor:.9f}\" interno)"
    )
    print(
        f"    Halo: {halo_x} px E/W, {halo_y} px N/S "
        f"(radius {radius_m:g} m)"
    )

    tile_buildings = temp_dir / "buildings_tile.gpkg"
    if tile_buildings.exists():
        tile_buildings.unlink()

    extract_tile_buildings(
        buildings_gpkg,
        tile_buildings,
        ext_west,
        ext_south,
        ext_east,
        ext_north,
    )

    footprint = np.zeros((ext_h, ext_w), dtype=np.float32)
    height_area = np.zeros((ext_h, ext_w), dtype=np.float32)
    valid_height_fraction = np.zeros((ext_h, ext_w), dtype=np.float32)

    total_blocks = math.ceil(ext_h / block_size) * math.ceil(ext_w / block_size)
    block_no = 0
    build_progress = ProgressLine(
        "      GURS stavbe",
        total_blocks,
        inplace=progress_inplace,
        min_interval=0.20,
    )

    for by in range(0, ext_h, block_size):
        bh = min(block_size, ext_h - by)

        for bx in range(0, ext_w, block_size):
            bw = min(block_size, ext_w - bx)
            block_no += 1

            build_progress.update(block_no)

            high_w = bw * factor
            high_h = bh * factor

            gt = _block_geotransform(
                ext_west,
                ext_north,
                step,
                bx,
                by,
                factor,
            )

            # High-res building occupancy mask.
            mask_ds = create_mem_raster(
                high_w,
                high_h,
                gt,
                gdal.GDT_Byte,
                0,
            )

            gdal.Rasterize(
                mask_ds,
                str(tile_buildings),
                options=gdal.RasterizeOptions(
                    layers=["buildings"],
                    burnValues=[1],
                    # Pixel-centre rule. Using ALL_TOUCHED causes a positive
                    # footprint bias that falls as supersampling increases;
                    # centre-based burning converges much better.
                    allTouched=False,
                ),
            )

            high_mask = mask_ds.GetRasterBand(1).ReadAsArray()
            mask_ds = None

            footprint_block = _downsample_mean(
                high_mask, bh, bw, factor
            )
            footprint[by:by + bh, bx:bx + bw] = footprint_block

            # High-res height raster. 0 means outside a building or invalid
            # height. Float32 warning for decimal values is harmless.
            height_ds = create_mem_raster(
                high_w,
                high_h,
                gt,
                gdal.GDT_Float32,
                0,
            )

            gdal.Rasterize(
                height_ds,
                str(tile_buildings),
                options=gdal.RasterizeOptions(
                    layers=["buildings"],
                    attribute="RM_HEIGHT",
                    allTouched=False,
                ),
            )

            high_height = (
                height_ds.GetRasterBand(1)
                .ReadAsArray()
                .astype(np.float32, copy=False)
            )
            height_ds = None

            high_valid = (high_height > 0).astype(np.float32)

            height_area[by:by + bh, bx:bx + bw] = _downsample_mean(
                high_height, bh, bw, factor
            )
            valid_height_fraction[by:by + bh, bx:bx + bw] = _downsample_mean(
                high_valid, bh, bw, factor
            )

            del high_mask, high_height, high_valid, footprint_block

    build_progress.finish(total_blocks)

    return (
        footprint,
        height_area,
        valid_height_fraction,
        halo_x,
        halo_y,
        px_x_m,
        px_y_m,
    )




def _circle_cell_fraction(
    dx: int,
    dy: int,
    px_x_m: float,
    px_y_m: float,
    radius_m: float,
    nodes: np.ndarray,
    node_weights: np.ndarray,
):
    """Fraction of one rectangular output cell covered by a physical circle.

    dx/dy are non-negative integer cell offsets from the kernel centre. Fully
    inside/outside cells are handled analytically. Only boundary cells use
    deterministic Gauss-Legendre integration in Y. Kernel construction is tiny
    compared with the raster convolution, so a high quadrature order is cheap.
    """
    cx = dx * px_x_m
    cy = dy * px_y_m
    x0 = cx - px_x_m / 2.0
    x1 = cx + px_x_m / 2.0
    y0 = cy - px_y_m / 2.0
    y1 = cy + px_y_m / 2.0
    r2 = radius_m * radius_m

    # Minimum distance from origin to the rectangle.
    near_x = 0.0 if x0 <= 0.0 <= x1 else min(abs(x0), abs(x1))
    near_y = 0.0 if y0 <= 0.0 <= y1 else min(abs(y0), abs(y1))
    if near_x * near_x + near_y * near_y >= r2:
        return 0.0

    # Maximum distance from origin to any rectangle corner.
    far_x = max(abs(x0), abs(x1))
    far_y = max(abs(y0), abs(y1))
    if far_x * far_x + far_y * far_y <= r2:
        return 1.0

    # Integrate horizontal overlap width through the cell's Y extent.
    half_y = (y1 - y0) / 2.0
    mid_y = (y1 + y0) / 2.0
    ys = mid_y + half_y * nodes
    half_width = np.sqrt(np.clip(r2 - ys * ys, 0.0, None))
    widths = np.maximum(
        0.0,
        np.minimum(x1, half_width) - np.maximum(x0, -half_width),
    )
    area = half_y * float(np.dot(node_weights, widths))
    fraction = area / (px_x_m * px_y_m)
    return float(min(1.0, max(0.0, fraction)))




def build_circle_kernel_spec(
    px_x_m: float,
    px_y_m: float,
    radius_m: float,
    *,
    mode: str,
    quadrature_order: int = DEFAULT_KERNEL_QUADRATURE,
):
    """Build one deterministic physical-circle kernel description.

    binary   = exact V3 centre-in-circle rule.
    weighted = fractional area overlap of each output cell with the circle.

    Weighted rows are stored as a full central horizontal box plus only the
    fractional boundary cells. This keeps the convolution close to the speed
    of the old prefix-sum implementation instead of doing a dense 2-D kernel.
    """
    if radius_m <= 0:
        raise ValueError("--urban-radius mora biti > 0")
    if mode not in {"binary", "weighted"}:
        raise ValueError(f"Neznan kernel mode: {mode}")

    circle_area = math.pi * radius_m * radius_m
    pixel_area = px_x_m * px_y_m

    if mode == "binary":
        max_dy = int(math.floor(radius_m / px_y_m))
        signed_rows = []
        kernel_cells = 0
        for dy in range(-max_dy, max_dy + 1):
            y_m = dy * px_y_m
            remaining = max(0.0, radius_m * radius_m - y_m * y_m)
            max_dx = int(math.floor(math.sqrt(remaining) / px_x_m))
            signed_rows.append(
                {
                    "dy": dy,
                    "full_dx": max_dx,
                    "partials": (),
                }
            )
            kernel_cells += 2 * max_dx + 1

        effective_area = kernel_cells * pixel_area
        return {
            "mode": "binary",
            "signed_rows": signed_rows,
            "normalizer": float(kernel_cells),
            "support_cells": int(kernel_cells),
            "equivalent_cells": float(kernel_cells),
            "effective_area_m2": float(effective_area),
            "circle_area_m2": float(circle_area),
            "area_error_pct": float(100.0 * (effective_area / circle_area - 1.0)),
            "quadrature_order": 0,
        }

    if quadrature_order < 8:
        raise ValueError("--kernel-quadrature naj bo vsaj 8")

    nodes, node_weights = np.polynomial.legendre.leggauss(quadrature_order)

    # Include every cell whose rectangle can intersect the circle, not only
    # cells whose centre lies inside it.
    max_dy = int(math.ceil(radius_m / px_y_m + 0.5)) + 1
    max_dx_limit = int(math.ceil(radius_m / px_x_m + 0.5)) + 1

    abs_rows = {}
    support_cells = 0
    total_weight = 0.0

    for dy in range(0, max_dy + 1):
        weights = []
        for dx in range(0, max_dx_limit + 1):
            w = _circle_cell_fraction(
                dx,
                dy,
                px_x_m,
                px_y_m,
                radius_m,
                nodes,
                node_weights,
            )
            weights.append(w)

        while weights and weights[-1] <= 1e-12:
            weights.pop()
        if not weights:
            continue

        # Fully covered cells always form a central run. They are summed with
        # one prefix-sum box. Only the circle-boundary cells need multipliers.
        full_dx = -1
        for dx, w in enumerate(weights):
            if w == 1.0 and dx == full_dx + 1:
                full_dx = dx
            else:
                break

        partials = tuple(
            (dx, float(w))
            for dx, w in enumerate(weights)
            if dx > full_dx and w > 1e-12
        )
        abs_rows[dy] = {
            "full_dx": full_dx,
            "partials": partials,
        }

        row_support = 0
        row_weight = 0.0
        for dx, w in enumerate(weights):
            multiplicity = 1 if dx == 0 else 2
            row_support += multiplicity
            row_weight += multiplicity * w

        y_mult = 1 if dy == 0 else 2
        support_cells += y_mult * row_support
        total_weight += y_mult * row_weight

    signed_rows = []
    for dy in sorted(abs_rows):
        spec = abs_rows[dy]
        if dy == 0:
            signed_rows.append(
                {
                    "dy": 0,
                    "full_dx": spec["full_dx"],
                    "partials": spec["partials"],
                }
            )
        else:
            signed_rows.append(
                {
                    "dy": -dy,
                    "full_dx": spec["full_dx"],
                    "partials": spec["partials"],
                }
            )
            signed_rows.append(
                {
                    "dy": dy,
                    "full_dx": spec["full_dx"],
                    "partials": spec["partials"],
                }
            )

    signed_rows.sort(key=lambda row: row["dy"])
    effective_area = total_weight * pixel_area

    return {
        "mode": "weighted",
        "signed_rows": signed_rows,
        "normalizer": float(total_weight),
        "support_cells": int(support_cells),
        "equivalent_cells": float(total_weight),
        "effective_area_m2": float(effective_area),
        "circle_area_m2": float(circle_area),
        "area_error_pct": float(100.0 * (effective_area / circle_area - 1.0)),
        "quadrature_order": int(quadrature_order),
    }




def _prefix_box_sum(rows: np.ndarray, halo_x: int, target_n: int, dx: int):
    """Horizontal [-dx,+dx] sum using the same Float32 prefix rule as V3."""
    csum = np.cumsum(rows, axis=1, dtype=np.float32)
    right_start = halo_x + dx
    right = csum[:, right_start:right_start + target_n]
    left_index = halo_x - dx - 1
    if left_index >= 0:
        left = csum[:, left_index:left_index + target_n]
        return right - left
    return right




def _circle_sum_block(
    src_ext: np.ndarray,
    target_n: int,
    halo_x: int,
    halo_y: int,
    kernel_spec: dict,
    y0: int,
    h: int,
):
    """Compute one independent output row block.

    A block reads halo data but writes no neighbouring output block. The
    arithmetic inside each block is fixed, so running blocks on multiple
    threads changes scheduling only, not the numerical order within a pixel.
    """
    acc = np.zeros((h, target_n), dtype=np.float32)
    weighted = kernel_spec["mode"] == "weighted"

    for row_spec in kernel_spec["signed_rows"]:
        dy = row_spec["dy"]
        full_dx = row_spec["full_dx"]
        src_y0 = halo_y + y0 + dy
        rows = src_ext[src_y0:src_y0 + h, :]

        if full_dx >= 0:
            acc += _prefix_box_sum(rows, halo_x, target_n, full_dx)

        if weighted:
            for dx, weight in row_spec["partials"]:
                w = np.float32(weight)
                if dx == 0:
                    start = halo_x
                    acc += rows[:, start:start + target_n] * w
                else:
                    pos = halo_x + dx
                    neg = halo_x - dx
                    acc += rows[:, pos:pos + target_n] * w
                    acc += rows[:, neg:neg + target_n] * w

    return acc




def circular_sum_core(
    src_ext: np.ndarray,
    target_n: int,
    halo_x: int,
    halo_y: int,
    kernel_spec: dict,
    *,
    row_block: int = DEFAULT_KERNEL_ROW_BLOCK,
    workers: int = 1,
    progress_label: str = "      kernel",
    progress_inplace: bool = True,
):
    """Blockwise circular sum with optional deterministic thread parallelism."""
    if row_block < 32:
        raise ValueError("kernel row block naj bo vsaj 32")
    if workers < 1:
        raise ValueError("kernel workers mora biti >= 1")

    result = np.zeros((target_n, target_n), dtype=np.float32)
    blocks = [
        (y0, min(row_block, target_n - y0))
        for y0 in range(0, target_n, row_block)
    ]
    progress = ProgressLine(
        progress_label,
        len(blocks),
        inplace=progress_inplace,
        min_interval=0.25,
    )

    if workers == 1 or len(blocks) == 1:
        for block_no, (y0, h) in enumerate(blocks, start=1):
            result[y0:y0 + h, :] = _circle_sum_block(
                src_ext,
                target_n,
                halo_x,
                halo_y,
                kernel_spec,
                y0,
                h,
            )
            progress.update(block_no)
    else:
        # Threads intentionally share read-only src_ext and write disjoint
        # result slices. Processes would copy/serialize multi-GB arrays on
        # Windows. Thread scheduling cannot alter a block's arithmetic order.
        with ThreadPoolExecutor(max_workers=min(workers, len(blocks))) as executor:
            futures = {
                executor.submit(
                    _circle_sum_block,
                    src_ext,
                    target_n,
                    halo_x,
                    halo_y,
                    kernel_spec,
                    y0,
                    h,
                ): (y0, h)
                for y0, h in blocks
            }
            completed = 0
            for future in as_completed(futures):
                y0, h = futures[future]
                result[y0:y0 + h, :] = future.result()
                completed += 1
                progress.update(completed)

    progress.finish(len(blocks))
    return result




def _urban_sample_factor(target_arcsec: float, analysis_arcsec: float) -> int:
    ratio = target_arcsec / analysis_arcsec
    factor = int(round(ratio))
    if factor < 1 or not math.isclose(
        ratio, factor, rel_tol=0.0, abs_tol=1e-12
    ):
        raise RuntimeError(
            f"Urbana analiza zahteva, da je končna ločljivost cel večkratnik "
            f"{analysis_arcsec}\"; dobil {target_arcsec}\"."
        )
    return factor




def _sample_urban_analysis_grid(
    array: np.ndarray,
    target_n: int,
    target_arcsec: float,
    analysis_arcsec: float,
):
    factor = _urban_sample_factor(target_arcsec, analysis_arcsec)
    sampled = array[::factor, ::factor]
    if sampled.shape != (target_n, target_n):
        raise RuntimeError(
            "Neujemanje poravnave urbane analizne mreže: "
            f"common {array.shape}, factor {factor} -> {sampled.shape}, "
            f"pricakovano {(target_n, target_n)}"
        )
    return sampled, factor




def build_urban_analysis(
    buildings_gpkg: Path,
    lat: int,
    lon: int,
    radius_m: float,
    block_size: int,
    temp_dir: Path,
    *,
    analysis_arcsec_requested: float,
    urban_supersample: int,
    kernel_mode: str,
    kernel_workers: int,
    kernel_row_block: int,
    kernel_quadrature: int,
    progress_inplace: bool,
    keep_footprint: bool,
):
    """Build one common urban-analysis field for a complete 1-degree tile.

    --urban-analysis-arcsec določi mrežo, na kateri se izračunata gostota in
    reprezentativna višina. --urban-supersample je faktor glede na končni 1"
    LCV; npr. 8 pomeni notranjo rasterizacijo stavb pri 0,125".

    Faktor rasterizacije znotraj analizne mreže se izračuna tako, da je notranja
    ločljivost vedno FINAL_ARCSEC / urban_supersample.
    """
    analysis_n, analysis_arcsec = grid_parameters(analysis_arcsec_requested)
    internal_arcsec = FINAL_ARCSEC / float(urban_supersample)
    building_factor_f = analysis_arcsec / internal_arcsec
    building_factor = int(round(building_factor_f))
    if building_factor < 1 or not math.isclose(
        building_factor_f, building_factor, rel_tol=0.0, abs_tol=1e-10
    ):
        raise ValueError(
            "Nezdružljiva --urban-analysis-arcsec in --urban-supersample: "
            f"analysis={analysis_arcsec:.9f}\", interno={internal_arcsec:.9f}\". "
            "Analysis grid mora biti celoštevilski večkratnik notranjega rasterja."
        )

    print()
    print("    [URBANA ANALIZA]")
    print(
        f"      analysis grid: {analysis_arcsec:.9f}\" "
        f"({analysis_n} x {analysis_n})"
    )
    print(
        f"      buildings interno: {internal_arcsec:.9f}\" "
        f"({building_factor}x znotraj analysis grida; {urban_supersample}x glede na 1\")"
    )
    print(f"      physical radius: {radius_m:g} m")

    (
        footprint_ext,
        height_area_ext,
        valid_height_ext,
        halo_x,
        halo_y,
        px_x_m,
        px_y_m,
    ) = rasterize_buildings_blockwise(
        buildings_gpkg,
        lat,
        lon,
        analysis_n,
        analysis_arcsec,
        building_factor,
        radius_m,
        block_size,
        temp_dir,
        progress_inplace=progress_inplace,
    )

    footprint_core_view = footprint_ext[
        halo_y:halo_y + analysis_n,
        halo_x:halo_x + analysis_n,
    ]
    equivalent_building_pixels = float(
        footprint_core_view.sum(dtype=np.float64)
    )
    touched = int((footprint_core_view > 0).sum())
    mean_coverage = 100.0 * equivalent_building_pixels / (analysis_n * analysis_n)

    print(f"      analiznih celic z delom stavbe: {touched:,}")
    print(
        f"      analizni ekvivalent polno pozidanih {analysis_arcsec:.6f}\" celic: "
        f"{equivalent_building_pixels:,.0f}"
    )
    print(f"      povprečna pokritost stavb v analizni mreži: {mean_coverage:.3f} %")

    footprint_core = (
        footprint_core_view.copy() if keep_footprint else None
    )

    kernel_spec = build_circle_kernel_spec(
        px_x_m,
        px_y_m,
        radius_m,
        mode=kernel_mode,
        quadrature_order=kernel_quadrature,
    )

    print(
        f"      kernel: {kernel_spec['mode']} | support="
        f"{kernel_spec['support_cells']:,} | ekvivalent="
        f"{kernel_spec['equivalent_cells']:.6f} celic analysis grida"
    )
    print(
        f"      kernel area: {kernel_spec['effective_area_m2']:,.3f} m2; "
        f"ideal={kernel_spec['circle_area_m2']:,.3f} m2; "
        f"error={kernel_spec['area_error_pct']:+.6f} %"
    )

    footprint_sum = circular_sum_core(
        footprint_ext,
        analysis_n,
        halo_x,
        halo_y,
        kernel_spec,
        row_block=kernel_row_block,
        workers=kernel_workers,
        progress_label=f"      urbana gostota {analysis_arcsec:.6f}\"",
        progress_inplace=progress_inplace,
    )
    density = footprint_sum / np.float32(kernel_spec["normalizer"])
    np.clip(density, 0.0, 1.0, out=density)
    del footprint_ext, footprint_sum, footprint_core_view
    gc.collect()

    height_numerator = circular_sum_core(
        height_area_ext,
        analysis_n,
        halo_x,
        halo_y,
        kernel_spec,
        row_block=kernel_row_block,
        workers=kernel_workers,
        progress_label=f"      urbana višina števec {analysis_arcsec:.6f}\"",
        progress_inplace=progress_inplace,
    )
    height_denominator = circular_sum_core(
        valid_height_ext,
        analysis_n,
        halo_x,
        halo_y,
        kernel_spec,
        row_block=kernel_row_block,
        workers=kernel_workers,
        progress_label=f"      urbana višina imenovalec {analysis_arcsec:.6f}\"",
        progress_inplace=progress_inplace,
    )
    del height_area_ext, valid_height_ext
    gc.collect()

    mean_height = np.zeros((analysis_n, analysis_n), dtype=np.float32)
    valid = height_denominator > 1e-6
    mean_height[valid] = (
        height_numerator[valid] / height_denominator[valid]
    )
    del valid, height_numerator, height_denominator
    gc.collect()

    stats = {
        "analysis_arcsec": float(analysis_arcsec),
        "analysis_grid_n": int(analysis_n),
        "building_internal_arcsec": float(internal_arcsec),
        "building_factor": int(building_factor),
        "urban_supersample": int(urban_supersample),
        "building_touched_cells": int(touched),
        "building_equivalent_cells": float(equivalent_building_pixels),
        "building_mean_coverage_pct": float(mean_coverage),
        "kernel_mode": kernel_spec["mode"],
        "kernel_support_cells": int(kernel_spec["support_cells"]),
        "kernel_weight": float(kernel_spec["normalizer"]),
        "kernel_effective_area_m2": float(kernel_spec["effective_area_m2"]),
        "kernel_circle_area_m2": float(kernel_spec["circle_area_m2"]),
        "kernel_area_error_pct": float(kernel_spec["area_error_pct"]),
        "kernel_quadrature": int(kernel_spec["quadrature_order"]),
        "kernel_workers": int(kernel_workers),
        "px_x_m": float(px_x_m),
        "px_y_m": float(px_y_m),
    }
    return density, mean_height, footprint_core, stats




def classify_urban_from_analysis(
    base: np.ndarray,
    slovenia_mask: np.ndarray,
    density_common: np.ndarray,
    mean_height_common: np.ndarray,
    target_n: int,
    target_arcsec: float,
    analysis_arcsec: float,
    lo_density: float,
    tall_density: float,
    tall_height_m: float,
    dense_hi_density: float,
    dense_hi_height_m: float,
):
    density, sample_factor = _sample_urban_analysis_grid(
        density_common, target_n, target_arcsec, analysis_arcsec
    )
    mean_height, sample_factor_h = _sample_urban_analysis_grid(
        mean_height_common, target_n, target_arcsec, analysis_arcsec
    )
    if sample_factor != sample_factor_h:
        raise RuntimeError("Neujemanje faktorja vzorčenja urbane gostote in višine")

    # Threshold logic is intentionally the same as V3/V4.
    urban_lo = density >= lo_density
    hi_tall = (
        (density >= tall_density)
        & (mean_height >= tall_height_m)
    )
    hi_dense = (
        (density >= dense_hi_density)
        & (mean_height >= dense_hi_height_m)
    )
    urban_hi = hi_tall | hi_dense
    urban_lo &= ~urban_hi

    # Urbani razredi iz GURS stavb se določajo samo znotraj Slovenije.
    urban_lo &= slovenia_mask
    urban_hi &= slovenia_mask

    # Water is protected exactly as before.
    urban_lo &= base != 0
    urban_hi &= base != 0

    out = base.copy()
    out[urban_lo] = 13
    out[urban_hi] = 14

    stats = {
        "analysis_sample_factor": int(sample_factor),
        "urban_lo": int(urban_lo.sum()),
        "urban_hi": int(urban_hi.sum()),
        # Preserve V3/V4 semantics: these are raw threshold-condition counts
        # pred omejitvijo na Slovenijo in zaščito vode.
        "hi_tall": int(hi_tall.sum()),
        "hi_dense": int(hi_dense.sum()),
    }
    return out, density, mean_height, stats




def _neighbor_count(mask: np.ndarray):
    """8-neighbour count for a boolean 2D array."""
    count = np.zeros(mask.shape, dtype=np.uint8)

    count[1:, :] += mask[:-1, :]
    count[:-1, :] += mask[1:, :]
    count[:, 1:] += mask[:, :-1]
    count[:, :-1] += mask[:, 1:]

    count[1:, 1:] += mask[:-1, :-1]
    count[1:, :-1] += mask[:-1, 1:]
    count[:-1, 1:] += mask[1:, :-1]
    count[:-1, :-1] += mask[1:, 1:]

    return count




def remove_legacy_urban_masked(
    src: np.ndarray,
    slovenia_mask: np.ndarray,
    *,
    progress_inplace: bool = True,
):
    """Remove legacy RM Urban 13/14 only inside Slovenia.

    The iterative neighbour-fill logic is unchanged from V3. Only live status
    reporting was added. It is deliberately not parallelised inside one tile:
    each iteration depends on the fully completed previous iteration, and a
    memory-heavy class-parallel implementation would bring little benefit.
    """
    out = np.asarray(src, dtype=np.uint8).copy()

    legacy_any = (out == 13) | (out == 14)
    todo = legacy_any & slovenia_mask

    inside_count = int(todo.sum())
    outside_preserved = int((legacy_any & ~slovenia_mask).sum())

    if inside_count == 0:
        return out, 0, 0, outside_preserved

    iteration = 0
    remaining_count = inside_count
    last_len = 0

    while todo.any():
        iteration += 1

        best_count = np.zeros(out.shape, dtype=np.uint8)
        best_class = np.zeros(out.shape, dtype=np.uint8)

        # Natural/non-urban classes 1..12. Water is intentionally excluded
        # to avoid coastal water bleeding into former settlements.
        for cls in range(1, 13):
            cls_mask = (out == cls)
            counts = _neighbor_count(cls_mask)

            better = todo & (counts > best_count)
            best_count[better] = counts[better]
            best_class[better] = cls

        fillable = todo & (best_count > 0)
        num = int(fillable.sum())

        if num == 0:
            # Zelo redek nadomestni primer; omejen je samo na Slovenijo.
            out[todo] = 10
            remaining_count = 0
            break

        out[fillable] = best_class[fillable]
        todo[fillable] = False
        remaining_count -= num

        msg = (
            f"      legacy fill: iteracija {iteration}, "
            f"preostane {remaining_count:,}/{inside_count:,}"
        )
        if progress_inplace:
            print("\r" + msg.ljust(last_len), end="", flush=True)
            last_len = max(last_len, len(msg))
        else:
            print(msg)

        if iteration > 1000:
            raise RuntimeError(
                "Legacy urban fill ni konvergiral po 1000 iteracijah."
            )

    if progress_inplace and last_len:
        msg = f"      legacy fill: KONCANO po {iteration} iteracijah"
        print("\r" + msg.ljust(last_len))

    remaining = int(
        (((out == 13) | (out == 14)) & slovenia_mask).sum()
    )

    return out, inside_count, remaining, outside_preserved





# =============================================================================
# NARAVNA KLASIFIKACIJA: PODVZORCI -> 1" CELICA
# =============================================================================

def compose_source_block(
    *,
    raba_gpkg: Path,
    raba_physical_gpkg: Path,
    zgs_gpkg: Path,
    gurs_gpkg: Path,
    hydro_gpkg: Path,
    lat: int,
    lon: int,
    x0: int,
    y0: int,
    width: int,
    height: int,
    factor: int,
):
    step = FINAL_ARCSEC / 3600.0
    high_step = step / factor
    tile_gt = tile_geotransform(lat, lon, FINAL_ARCSEC)
    west = tile_gt[0] + x0 * step
    north = tile_gt[3] - y0 * step

    ds = create_mem_raster(
        width * factor,
        height * factor,
        (west, high_step, 0.0, north, 0.0, -high_step),
        gdal.GDT_Byte,
        NODATA_CLASS,
    )
    ds.GetRasterBand(1).SetNoDataValue(NODATA_CLASS)

    _rasterize_into(ds, raba_gpkg, "raba")
    _rasterize_into(ds, zgs_gpkg, "zgs_forest")
    _rasterize_into(ds, raba_physical_gpkg, "raba_physical")
    _rasterize_into(ds, gurs_gpkg, "gurs_specific")
    _rasterize_into(ds, hydro_gpkg, "hydro_water")

    arr = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    return arr


def aggregate_block_v1_0(
    high: np.ndarray,
    out_h: int,
    out_w: int,
    factor: int,
    class_thresholds: dict[int, float],
):
    """
    Izbere največji površinski razred med kandidati, ki dosežejo svoj prag.

    Privzeto:
      00 Water       >= 75 %
      01..11          >= 25 %
      12 Bare Ground >= 75 %

    Prag je vedno delež CELOTNE končne 1" celice (factor*factor podvzorcev),
    enako kot v preverjeni produkcijski logiki. Če noben razred ne doseže svojega praga,
    se uporabi izhodiščni/fallback LCV.
    """
    view = high.reshape(out_h, factor, out_w, factor)
    total_samples = factor * factor

    nodata_count = (view == NODATA_CLASS).sum(axis=(1, 3), dtype=np.uint16)
    valid_count = total_samples - nodata_count

    counts = {}
    present = set(int(x) for x in np.unique(high) if int(x) != NODATA_CLASS)
    for cls in present:
        if 0 <= cls <= 12:
            counts[cls] = (view == cls).sum(axis=(1, 3), dtype=np.uint16)

    best_count = np.zeros((out_h, out_w), dtype=np.uint16)
    best_class = np.full((out_h, out_w), NODATA_CLASS, dtype=np.uint8)

    # TIE_ORDER_LOW_TO_HIGH pomeni, da kasnejši razred ob popolnem izenačenju
    # prepiše prejšnjega (>=), kar ohrani dosedanjo deterministično logiko.
    for cls in TIE_ORDER_LOW_TO_HIGH:
        if cls not in counts:
            continue
        threshold = float(class_thresholds[cls])
        count = counts[cls]
        fraction = count.astype(np.float32) / float(total_samples)
        eligible = fraction >= threshold
        replace = eligible & (count >= best_count)
        best_count[replace] = count[replace]
        best_class[replace] = cls

    dominant_fraction = best_count.astype(np.float32) / float(total_samples)
    source_fraction = valid_count.astype(np.float32) / float(total_samples)

    zeros = np.zeros((out_h, out_w), dtype=np.uint16)
    water_fraction = counts.get(0, zeros).astype(np.float32) / float(total_samples)
    bare_fraction = counts.get(12, zeros).astype(np.float32) / float(total_samples)
    return best_class, dominant_fraction, source_fraction, water_fraction, bare_fraction



# =============================================================================
# TILE: VSE 00..14
# =============================================================================

def process_tile(
    original_path: Path,
    *,
    output_dir: Path,
    raba_gpkg: Path,
    raba_physical_gpkg: Path,
    zgs_gpkg: Path,
    gurs_gpkg: Path,
    hydro_gpkg: Path,
    buildings_gpkg: Path,
    mask_dem: Path,
    factor: int,
    block_size: int,
    class_thresholds: dict[int, float],
    urban_radius: float,
    urban_analysis_arcsec: float,
    urban_supersample: int,
    urban_lo: float,
    tall_density: float,
    tall_height: float,
    dense_hi_density: float,
    dense_hi_height: float,
    kernel_mode: str,
    kernel_workers: int,
    kernel_row_block: int,
    kernel_quadrature: int,
    write_geotiff: bool,
    write_confidence: bool,
    progress_inplace: bool = True,
):
    t0 = time.perf_counter()
    lat, lon = parse_tile_name(original_path)
    tile = original_path.stem.upper()

    print("\n" + "=" * 84)
    print(
        f"TILE {original_path.name} | V1.0 | 1\\\" final | "
        f"{factor}x naravno vzorčenje = {1/factor:.3f}\\\""
    )
    print("=" * 84)

    original = load_original_lcv(original_path)
    slovenia = rasterize_slovenia_mask(mask_dem, lat, lon)

    if not slovenia.any():
        output_dir.mkdir(parents=True, exist_ok=True)
        out_lcv = output_dir / original_path.name
        original.tofile(out_lcv)
        if write_geotiff:
            save_geotiff(
                original,
                output_dir / "PREVIEW" / f"{tile}_V1_0_FINAL_1arcsec.tif",
                lat, lon, nodata=None,
            )
        if write_confidence:
            outside = np.full((FINAL_N, FINAL_N), QA_OUTSIDE, dtype=np.uint8)
            save_geotiff(
                outside,
                output_dir / "QA" / f"{tile}_V1_0_NATURAL_DOMINANT_FRACTION_PCT.tif",
                lat, lon, nodata=QA_OUTSIDE,
            )
            save_geotiff(
                outside,
                output_dir / "QA" / f"{tile}_V1_0_QA_DOMINANCE_STATUS.tif",
                lat, lon, nodata=QA_OUTSIDE,
            )
            del outside
        qa = {
            "tile": tile,
            "slovenia_pixels": 0,
            "qa_raster_natural_0_100": 0,
            "qa_raster_fallback_253": 0,
            "qa_raster_urban_254": 0,
            "qa": "PASS",
            "elapsed_min": (time.perf_counter() - t0) / 60.0,
        }
        for cls in range(15):
            qa[f"final_class_{cls:02d}"] = 0
        print("  PASS | tile nima slovenskih celic; original ohranjen.")
        return qa

    cleaned, legacy_urban, legacy_remaining, legacy_outside = remove_legacy_urban_masked(
        original, slovenia, progress_inplace=progress_inplace
    )
    if legacy_remaining:
        raise RuntimeError(
            f"QA FAIL {tile}: po odstranitvi starega urbana ostane "
            f"{legacy_remaining} celic 13/14 znotraj Slovenije."
        )

    fallback_base = remap_original_fallback(cleaned)
    final = original.copy()
    final[slovenia] = fallback_base[slovenia]

    confidence = (
        np.zeros((FINAL_N, FINAL_N), dtype=np.uint8)
        if write_confidence else None
    )
    qa_status = (
        np.full((FINAL_N, FINAL_N), QA_OUTSIDE, dtype=np.uint8)
        if write_confidence else None
    )

    qa = {
        "tile": tile,
        "supersample_factor": factor,
        "internal_arcsec": FINAL_ARCSEC / factor,
        "subpixels_per_final_cell": factor * factor,
        "block_size": block_size,
        "other_threshold_default": class_thresholds[1],
        "water_threshold": class_thresholds[0],
        "bare_threshold": class_thresholds[12],
        "class_thresholds": ";".join(
            f"{cls:02d}={class_thresholds[cls]:.6f}" for cls in NATURAL_CLASS_IDS
        ),
        "slovenia_pixels": int(slovenia.sum()),
        "legacy_urban_removed": legacy_urban,
        "legacy_urban_outside_preserved": legacy_outside,
        "source_classified_cells": 0,
        "source_low_support_cells": 0,
        "fallback_cells_inside_slovenia": 0,
        "water_eligible_cells": 0,
        "water_rejected_below_threshold": 0,
        "bare_eligible_cells": 0,
        "bare_rejected_below_threshold": 0,
        "mean_dominant_fraction_pct": 0.0,
        "mean_source_fraction_pct": 0.0,
        "qa_raster_natural_0_100": 0,
        "qa_raster_fallback_253": 0,
        "qa_raster_urban_254": 0,
    }

    sum_dom = 0.0
    sum_src = 0.0
    sum_considered = 0

    blocks_y = math.ceil(FINAL_N / block_size)
    blocks_x = math.ceil(FINAL_N / block_size)
    total_blocks = blocks_y * blocks_x
    block_no = 0

    for y0 in range(0, FINAL_N, block_size):
        h = min(block_size, FINAL_N - y0)
        for x0 in range(0, FINAL_N, block_size):
            w = min(block_size, FINAL_N - x0)
            block_no += 1

            if progress_inplace:
                print(
                    f"\r  naravna agregacija {block_no:3d}/{total_blocks:3d} "
                    f"({100.0*block_no/total_blocks:6.2f}%)",
                    end="",
                )
            elif block_no in (1, total_blocks) or block_no % max(1, total_blocks // 4) == 0:
                print(f"  [{tile}] naravna agregacija {100.0*block_no/total_blocks:5.1f}%")

            high = compose_source_block(
                raba_gpkg=raba_gpkg,
                raba_physical_gpkg=raba_physical_gpkg,
                zgs_gpkg=zgs_gpkg,
                gurs_gpkg=gurs_gpkg,
                hydro_gpkg=hydro_gpkg,
                lat=lat, lon=lon,
                x0=x0, y0=y0, width=w, height=h, factor=factor,
            )
            cls, dom_fraction, src_fraction, water_fraction, bare_fraction = aggregate_block_v1_0(
                high, h, w, factor, class_thresholds
            )
            del high

            s_mask = slovenia[y0:y0+h, x0:x0+w]

            # aggregate_block_v1_0 je že preveril prag izbranega razreda.
            apply = s_mask & (cls != NODATA_CLASS)
            low_support = s_mask & (src_fraction > 0) & ~apply
            fallback = s_mask & ~apply

            out_block = final[y0:y0+h, x0:x0+w]
            out_block[apply] = cls[apply]

            qa["source_classified_cells"] += int(apply.sum())
            qa["source_low_support_cells"] += int(low_support.sum())
            qa["fallback_cells_inside_slovenia"] += int(fallback.sum())

            water_present = s_mask & (water_fraction > 0)
            bare_present = s_mask & (bare_fraction > 0)
            qa["water_eligible_cells"] += int(
                (water_present & (water_fraction >= class_thresholds[0])).sum()
            )
            qa["water_rejected_below_threshold"] += int(
                (water_present & (water_fraction < class_thresholds[0])).sum()
            )
            qa["bare_eligible_cells"] += int(
                (bare_present & (bare_fraction >= class_thresholds[12])).sum()
            )
            qa["bare_rejected_below_threshold"] += int(
                (bare_present & (bare_fraction < class_thresholds[12])).sum()
            )

            n_considered = int(s_mask.sum())
            if n_considered:
                sum_dom += float(dom_fraction[s_mask].sum(dtype=np.float64))
                sum_src += float(src_fraction[s_mask].sum(dtype=np.float64))
                sum_considered += n_considered

            if confidence is not None:
                dom_pct = np.clip(
                    np.rint(dom_fraction * 100.0), 0, 100
                ).astype(np.uint8)

                # Stari/raw confidence raster ostane na voljo za analizo same
                # naravne agregacije, tudi kadar razred na koncu pade v fallback.
                conf_block = confidence[y0:y0+h, x0:x0+w]
                conf_block[s_mask] = dom_pct[s_mask]
                conf_block[~s_mask] = QA_OUTSIDE

                # Novi QA raster je semantično jasen:
                # 0..100 = source-based naravna dominantnost,
                # 253 = fallback, 254 se doda po urbani klasifikaciji,
                # 255 = zunaj Slovenije.
                qa_block = qa_status[y0:y0+h, x0:x0+w]
                qa_block[apply] = dom_pct[apply]
                qa_block[fallback] = QA_FALLBACK
                qa_block[~s_mask] = QA_OUTSIDE
                del dom_pct

            del cls, dom_fraction, src_fraction, water_fraction, bare_fraction

    if progress_inplace:
        print()

    if sum_considered:
        qa["mean_dominant_fraction_pct"] = 100.0 * sum_dom / sum_considered
        qa["mean_source_fraction_pct"] = 100.0 * sum_src / sum_considered

    # Shranimo naravno stanje, da lahko strogo preverimo, da urban ne povozi vode.
    natural_before_urban = final.copy()

    print("  Urbana klasifikacija 13/14 iz GURS stavb ...")
    with tempfile.TemporaryDirectory(prefix=f"lcv_v1_0_{tile}_") as td:
        density_common, mean_height_common, footprint_common, urban_common_stats = (
            build_urban_analysis(
                buildings_gpkg,
                lat,
                lon,
                urban_radius,
                block_size,
                Path(td),
                analysis_arcsec_requested=urban_analysis_arcsec,
                urban_supersample=urban_supersample,
                kernel_mode=kernel_mode,
                kernel_workers=kernel_workers,
                kernel_row_block=kernel_row_block,
                kernel_quadrature=kernel_quadrature,
                progress_inplace=progress_inplace,
                keep_footprint=False,
            )
        )

        final, density_sample, height_sample, urban_stats = classify_urban_from_analysis(
            final,
            slovenia,
            density_common,
            mean_height_common,
            FINAL_N,
            FINAL_ARCSEC,
            urban_common_stats["analysis_arcsec"],
            urban_lo,
            tall_density,
            tall_height,
            dense_hi_density,
            dense_hi_height,
        )

        del density_common, mean_height_common, density_sample, height_sample, footprint_common
        gc.collect()

    qa.update({
        "urban_analysis_arcsec": urban_common_stats["analysis_arcsec"],
        "urban_building_internal_arcsec": urban_common_stats["building_internal_arcsec"],
        "urban_radius_m": urban_radius,
        "urban_analysis_arcsec_requested": urban_analysis_arcsec,
        "urban_supersample": urban_supersample,
        "urban_building_factor_within_analysis": urban_common_stats["building_factor"],
        "urban_lo_density": urban_lo,
        "urban_tall_density": tall_density,
        "urban_tall_height_m": tall_height,
        "urban_dense_hi_density": dense_hi_density,
        "urban_dense_hi_height_m": dense_hi_height,
        "urban_lo": urban_stats["urban_lo"],
        "urban_hi": urban_stats["urban_hi"],
        "urban_hi_tall_condition": urban_stats["hi_tall"],
        "urban_hi_dense_condition": urban_stats["hi_dense"],
        "urban_kernel_mode": urban_common_stats["kernel_mode"],
        "urban_kernel_area_error_pct": urban_common_stats["kernel_area_error_pct"],
    })

    outside_changed = int((final[~slovenia] != original[~slovenia]).sum())
    qa["outside_slovenia_changed"] = outside_changed
    if outside_changed:
        raise RuntimeError(f"QA FAIL {tile}: tujina changed={outside_changed}")

    invalid = int(((final > 14) & slovenia).sum())
    qa["invalid_class_cells"] = invalid
    if invalid:
        raise RuntimeError(f"QA FAIL {tile}: {invalid} celic ima razred izven 00..14")

    if qa_status is not None:
        urban_mask_qa = slovenia & ((final == 13) | (final == 14))
        qa_status[urban_mask_qa] = QA_URBAN
        del urban_mask_qa

        qa["qa_raster_natural_0_100"] = int(
            (slovenia & (qa_status <= 100)).sum()
        )
        qa["qa_raster_fallback_253"] = int(
            (slovenia & (qa_status == QA_FALLBACK)).sum()
        )
        qa["qa_raster_urban_254"] = int(
            (slovenia & (qa_status == QA_URBAN)).sum()
        )
        qa_status_total = (
            qa["qa_raster_natural_0_100"]
            + qa["qa_raster_fallback_253"]
            + qa["qa_raster_urban_254"]
        )
        if qa_status_total != qa["slovenia_pixels"]:
            raise RuntimeError(
                f"QA FAIL {tile}: QA dominance/status raster pokriva "
                f"{qa_status_total:,}/{qa['slovenia_pixels']:,} slovenskih celic."
            )

    water_overwritten = int(
        ((natural_before_urban == 0) & slovenia & (final != 0)).sum()
    )
    qa["water_overwritten_by_urban"] = water_overwritten
    if water_overwritten:
        raise RuntimeError(
            f"QA FAIL {tile}: urban je povozil {water_overwritten} vodnih celic."
        )

    for c in range(15):
        qa[f"final_class_{c:02d}"] = int(((final == c) & slovenia).sum())

    structural_non3 = sum(qa[f"final_class_{c:02d}"] for c in (1, 2, 4, 5))
    if qa["slovenia_pixels"] > 100000 and qa["final_class_03"] > 10000 and structural_non3 == 0:
        raise RuntimeError(
            f"QA FAIL {tile}: ZGS forest collapse; 01/02/04/05 so vsi nič."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_lcv = output_dir / original_path.name
    final.tofile(out_lcv)
    if out_lcv.stat().st_size != FINAL_N * FINAL_N:
        raise RuntimeError(f"Napačna velikost izhodnega LCV: {out_lcv}")

    if write_geotiff:
        save_geotiff(
            final,
            output_dir / "PREVIEW" / f"{tile}_V1_0_FINAL_1arcsec.tif",
            lat, lon, nodata=None,
        )
    if confidence is not None:
        save_geotiff(
            confidence,
            output_dir / "QA" / f"{tile}_V1_0_NATURAL_DOMINANT_FRACTION_PCT.tif",
            lat, lon, nodata=QA_OUTSIDE,
        )
        save_geotiff(
            qa_status,
            output_dir / "QA" / f"{tile}_V1_0_QA_DOMINANCE_STATUS.tif",
            lat, lon, nodata=QA_OUTSIDE,
        )

    qa["elapsed_min"] = (time.perf_counter() - t0) / 60.0
    qa["qa"] = "PASS"

    print(
        f"  PASS | natural={qa['source_classified_cells']:,}; "
        f"fallback={qa['fallback_cells_inside_slovenia']:,}; "
        f"urban13={qa['urban_lo']:,}; urban14={qa['urban_hi']:,}; "
        f"mean dominant={qa['mean_dominant_fraction_pct']:.2f}%"
    )
    print(f"  output: {out_lcv}")

    del original, cleaned, fallback_base, natural_before_urban, final, slovenia
    if confidence is not None:
        del confidence, qa_status
    gc.collect()
    return qa



# =============================================================================
# DOKUMENTACIJA / PARALELIZACIJA / CLI
# =============================================================================

def _fmt_landheight_number(value: float) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def write_landheight_dat(output_dir: Path):
    """Zapiše 15 standardnih Radio Mobile height,density parov (00..14).

    Ne dodajamo opcijske 16. vrstice za alternativni attenuation model,
    zato ostane izbran običajni Radio Mobile način obravnave clutterja.
    """
    lines = []
    for cls in range(15):
        _name, height, density, _status = CLASS_META[cls]
        if height is None or density is None:
            raise RuntimeError(
                f"RF profil ni popoln: razred {cls:02d} nima height/density."
            )
        lines.append(
            f"{_fmt_landheight_number(height)},{_fmt_landheight_number(density)}"
        )
    path = output_dir / "landheight.dat"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def build_vrt_file(destination: Path, sources: list[Path], *, nodata=None):
    """Zgradi QGIS-prijazen VRT iz končnih tile GeoTIFF-ov."""
    sources = [Path(x) for x in sources if Path(x).exists()]
    if not sources:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    kwargs = {}
    if nodata is not None:
        kwargs["srcNodata"] = nodata
        kwargs["VRTNodata"] = nodata
    opts = gdal.BuildVRTOptions(**kwargs)
    ds = gdal.BuildVRT(str(destination), [str(x) for x in sources], options=opts)
    if ds is None:
        raise RuntimeError(f"VRT izdelava ni uspela: {destination}")
    ds.FlushCache()
    ds = None
    return destination


def write_class_documentation(
    output_dir: Path,
    p70: float,
    factor: int,
    class_thresholds: dict[int, float],
    hydro_symbols: tuple[str, ...],
    urban_params: dict,
):
    rows = []
    for cls in range(15):
        name, h, d, status = CLASS_META[cls]
        rows.append({
            "class": f"{cls:02d}",
            "name": name,
            "RM_height_m": "" if h is None else h,
            "RM_density_pct": "" if d is None else d,
            "min_area_fraction": (
                class_thresholds[cls] if cls in NATURAL_CLASS_IDS else ""
            ),
            "min_area_pct": (
                100.0 * class_thresholds[cls] if cls in NATURAL_CLASS_IDS else ""
            ),
            "calibration_status": status,
        })
    write_csv(output_dir / "LCV_CLASS_DEFINITIONS_V1_0.csv", rows)

    write_csv(
        output_dir / "ZGS_PRIOR_NATIONAL_DISTRIBUTION_V1_0.csv",
        [{"class": cls, **s} for cls, s in PRIOR_ZGS_DISTRIBUTION.items()],
    )

    metadata = f"""SLOVENIA RM DELUXE LCV V1.0 - BUILD METADATA

Software version: {PROGRAM_VERSION}
Final resolution: 1.0 arcsec
Natural polygon supersampling: 1/{factor} arcsec = {1/factor:.6f} arcsec
Samples per final 1" cell: {factor*factor}
ZGS P70 LZ/ha: {p70:.15f} m3/ha

Natural-class minimum area thresholds:
00 Water requires >= {100*class_thresholds[0]:.1f}% of the 1" cell.
12 Bare Ground requires >= {100*class_thresholds[12]:.1f}% of the 1" cell.
01..11 default/current thresholds are listed below; CLI overrides are recorded exactly.
{chr(10).join(f"{cls:02d}: {100*class_thresholds[cls]:.3f}%" for cls in NATURAL_CLASS_IDS)}

Natural-source precedence (low -> high):
1. MKGP RABA
2. ZGS structural forest 01..05
3. MKGP water/bare re-assertion
4. GURS specific non-forest Pokritost tal
5. DRSV Hidrografija selected polygonal water types

DRSV SIMBOL included as water:
{", ".join(hydro_symbols)}

V1.0 natural-class rules:
4210 Trsticje -> 08
10 = grassland / low open vegetation
11 = arable land / field crops

RF profile 00..14:
landheight.dat is generated from CLASS_META with the V1.0 RF profile.
00=0/0; 01=8/115; 02=12/65; 03=19/130; 04=24/105; 05=30/140;
06=4/45; 07=6/35; 08=3/65; 09=3/40; 10=2/15; 11=2/20;
12=0/0; 13=10/150; 14=20/175.

Urban 13/14:
Calculated from GURS buildings on every run; no preclassified urban input is required.
Common urban analysis grid: {urban_params['analysis_arcsec']} arcsec
Urban supersampling relative to final 1 arcsec: {urban_params['urban_supersample']}x
Building internal raster: {1.0/urban_params['urban_supersample']:.9f} arcsec
Radius: {urban_params['urban_radius']} m
Urban LO density >= {100*urban_params['urban_lo']:.3f}%
HI tall: density >= {100*urban_params['tall_density']:.3f}% and mean height >= {urban_params['tall_height']} m
HI dense: density >= {100*urban_params['dense_hi_density']:.3f}% and mean height >= {urban_params['dense_hi_height']} m
Kernel: {urban_params['kernel_mode']}

QA dominance/status raster (when --qa is enabled):
0..100 = dominant fraction of source-based natural classification
253 = fallback inside Slovenia
254 = final urban class 13/14; natural dominance not applicable
255 = outside Slovenia / NoData

ZGS:
MultiSurface geometry is linearized before rasterization.
GURS generic forest is excluded from the final high-precedence override.
Species composition does not change the ZGS production class.

Original RM LCV:
Used only to preserve areas outside Slovenia and as a gap fallback; legacy 3" tiles are aligned/resampled to the final 1" grid.
Any legacy 13/14 inside Slovenia is removed before V1.0 classification and recalculated from GURS buildings.
"""
    (output_dir / "BUILD_METADATA_V1_0.txt").write_text(metadata, encoding="utf-8")

    landheight_path = write_landheight_dat(output_dir)

    # Če je iz starega zagona ostal dokument o nedokončani RF kalibraciji,
    # ga odstranimo, da v izhodnem imeniku ne ostane zastarela informacija.
    for old_pending in output_dir.glob("RF_CALIBRATION_PENDING_*.txt"):
        old_pending.unlink()

    return landheight_path


def _worker_init(gdal_cache_mb: int):
    gdal.UseExceptions()
    gdal.SetConfigOption("GDAL_NUM_THREADS", "1")
    try:
        gdal.SetCacheMax(int(gdal_cache_mb) * 1024 * 1024)
    except Exception:
        pass


def _process_tile_task(task: dict):
    return process_tile(**task)


def choose_worker_count(requested: int, n_tiles: int) -> int:
    if n_tiles <= 1:
        return 1
    if requested > 0:
        return max(1, min(requested, n_tiles))
    return 1


def parse_tiles(value: str):
    if value.strip().lower() in {"all", "*"}:
        return sorted(SLOVENIA_TILES)
    out = []
    for x in value.split(","):
        t = x.strip().upper().replace(".LCV", "")
        if t not in SLOVENIA_TILES:
            raise argparse.ArgumentTypeError(f"Neznana ploščica Slovenije: {t}")
        out.append(t)
    return sorted(set(out))


def parse_hydro_symbols(value: str):
    vals = tuple(x.strip() for x in value.split(";") if x.strip())
    if not vals:
        raise argparse.ArgumentTypeError("--hydro-symbols ne sme biti prazen.")
    return vals


def parse_class_threshold_overrides(value: str):
    """Parse npr. '01=0.30;08=0.40;10=0.20'."""
    text = value.strip()
    if not text:
        return {}
    out: dict[int, float] = {}
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                "--class-thresholds uporabi obliko RAZRED=DELEZ; npr. 01=0.30;08=0.40"
            )
        cls_text, threshold_text = item.split("=", 1)
        try:
            cls = int(cls_text.strip())
            threshold = float(threshold_text.strip().replace(",", "."))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Neveljavna nastavitev praga: {item!r}"
            ) from exc
        if cls not in NATURAL_CLASS_IDS:
            raise argparse.ArgumentTypeError(
                f"Prag je mogoče nastaviti za naravne razrede 00..12; dobil {cls}."
            )
        if not (0.0 < threshold <= 1.0):
            raise argparse.ArgumentTypeError(
                f"Prag za razred {cls:02d} mora biti v (0,1]; dobil {threshold}."
            )
        out[cls] = threshold
    return out


def resolve_class_thresholds(args) -> dict[int, float]:
    """Sestavi končne pragove 00..12 z jasno prioriteto ročnih nastavitev."""
    legacy_zero = (
        DEFAULT_WATER_THRESHOLD
        if args.zero_clutter_threshold is None
        else float(args.zero_clutter_threshold)
    )
    water = legacy_zero if args.water_threshold is None else float(args.water_threshold)
    bare = legacy_zero if args.bare_threshold is None else float(args.bare_threshold)
    other = float(args.other_threshold)

    thresholds = {cls: other for cls in range(1, 12)}
    thresholds[0] = water
    thresholds[12] = bare

    # Najbolj specifična nastavitev ima zadnjo besedo.
    thresholds.update(args.class_thresholds)
    return thresholds


def main():
    ap = argparse.ArgumentParser(
        description=(
            "LCV V1.0: samostojni izračun slovenskih razredov 00..14 iz "
            "MKGP RABA, ZGS sestojev, GURS Pokritosti tal, DRSV Hidrografije in GURS stavb."
        )
    )

    ap.add_argument(
        "--version", action="version",
        version=f"%(prog)s {PROGRAM_VERSION}",
    )

    ap.add_argument("--original-dir", type=Path, default=DEFAULT_ORIGINAL)
    ap.add_argument("--zgs", type=Path, default=DEFAULT_ZGS)
    ap.add_argument("--raba", type=Path, default=DEFAULT_RABA)
    ap.add_argument("--gurs", type=Path, default=DEFAULT_GURS)
    ap.add_argument("--hydro", type=Path, default=DEFAULT_HYDRO)
    ap.add_argument("--buildings", type=Path, default=DEFAULT_BUILDINGS)
    ap.add_argument(
        "--mask", dest="mask_path", type=Path, default=DEFAULT_MASK,
        help="Binarni raster maske Slovenije; vrednost znotraj države, NoData zunaj.",
    )
    # Skriti alias zaradi združljivosti s starejšimi ukazi.
    ap.add_argument(
        "--mask-dem", dest="mask_path", type=Path, default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    ap.add_argument(
        "--tiles", type=parse_tiles, default=sorted(SLOVENIA_TILES),
        help="all ali seznam z vejicami, npr. N46E014,N46E015",
    )
    ap.add_argument(
        "--supersample", type=int, default=DEFAULT_SUPERSAMPLE, choices=(2,4,8),
        help="Naravno vzorčenje: 2=0,5\", 4=0,25\", 8=0,125\" (privzeto 8).",
    )
    ap.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)
    ap.add_argument(
        "--other-threshold", "--min-dominant-fraction",
        dest="other_threshold", type=float, default=DEFAULT_OTHER_THRESHOLD,
        help=(
            "Privzeti najmanjši površinski delež za razrede 01..11 "
            "(privzeto 0,25). Staro ime --min-dominant-fraction ostaja veljavno."
        ),
    )
    ap.add_argument(
        "--water-threshold", type=float, default=None,
        help="Najmanjši delež za 00 Water (privzeto 0,75).",
    )
    ap.add_argument(
        "--bare-threshold", type=float, default=None,
        help="Najmanjši delež za 12 Bare Ground (privzeto 0,75).",
    )
    ap.add_argument(
        "--zero-clutter-threshold", type=float, default=None,
        help=(
            "Združljivost s starejšimi zagoni: če je podan, nastavi skupni prag za 00 in 12, "
            "razen če je posamezen --water-threshold/--bare-threshold podan posebej."
        ),
    )
    ap.add_argument(
        "--class-thresholds", type=parse_class_threshold_overrides, default={},
        metavar='"01=0.30;08=0.40;10=0.20"',
        help=(
            "Neobvezni pragovi po posameznih naravnih razredih 00..12. "
            "Prepišejo splošne nastavitve. Primer: 01=0.30;08=0.40;12=0.80"
        ),
    )
    ap.add_argument(
        "--hydro-symbols", type=parse_hydro_symbols,
        default=DEFAULT_HYDRO_SYMBOLS,
        help=(
            "DRSV SIMBOL-i, ki se štejejo kot voda, ločeni s podpičjem. "
            "Privzeto: " + ";".join(DEFAULT_HYDRO_SYMBOLS)
        ),
    )

    ap.add_argument("--zgs-p70", default=f"{LOCKED_ZGS_P70_LZHA:.15g}")

    ap.add_argument(
        "--urban-radius", type=float, default=DEFAULT_URBAN_RADIUS_M,
        help="Fizični radij urbanega krožnega filtra v metrih (privzeto 45).",
    )
    ap.add_argument(
        "--urban-analysis-arcsec", type=float, default=DEFAULT_URBAN_ANALYSIS_ARCSEC,
        help="Ločljivost skupne urbane analize v ločnih sekundah (privzeto 0,25).",
    )
    ap.add_argument(
        "--urban-supersample", type=int, default=DEFAULT_URBAN_SUPERSAMPLE,
        help=(
            "Supersampling GURS stavb glede na končni 1\" LCV. "
            "Privzeto 8 = notranji raster 0,125\"."
        ),
    )
    ap.add_argument(
        "--urban-lo", type=float, default=DEFAULT_URBAN_LO_DENSITY,
        help="Najmanjša urbana gostota za razred 13, delež 0..1 (privzeto 0,0725).",
    )
    ap.add_argument(
        "--tall-density", type=float, default=DEFAULT_TALL_DENSITY,
        help="HI tall: najmanjša gostota stavb, delež 0..1 (privzeto 0,1375).",
    )
    ap.add_argument(
        "--tall-height", type=float, default=DEFAULT_TALL_HEIGHT_M,
        help="HI tall: najmanjša povprečna višina stavb v m (privzeto 17).",
    )
    ap.add_argument(
        "--dense-hi-density", type=float, default=DEFAULT_DENSE_HI_DENSITY,
        help="HI dense: najmanjša gostota stavb, delež 0..1 (privzeto 0,27).",
    )
    ap.add_argument(
        "--dense-hi-height", type=float, default=DEFAULT_DENSE_HI_HEIGHT_M,
        help="HI dense: najmanjša povprečna višina stavb v m (privzeto 11).",
    )
    ap.add_argument("--kernel-mode", choices=("weighted","binary"), default=DEFAULT_KERNEL_MODE)
    ap.add_argument("--kernel-workers", type=int, default=DEFAULT_KERNEL_WORKERS)
    ap.add_argument("--kernel-row-block", type=int, default=DEFAULT_KERNEL_ROW_BLOCK)
    ap.add_argument("--kernel-quadrature", type=int, default=DEFAULT_KERNEL_QUADRATURE)

    ap.add_argument(
        "--workers", type=int, default=0,
        help="Vzporedni procesi ploščic. 0=varna samodejna izbira (privzeto 1 zaradi RAM).",
    )
    ap.add_argument("--gdal-cache-mb", type=int, default=192)
    ap.add_argument("--rebuild-cache", action="store_true")

    # Izbirni QGIS/QA izhodi. Privzeto sta oba izklopljena, da produkcijski
    # zagon izdela samo LCV + dokumentacijo + landheight.dat.
    ap.add_argument(
        "--preview",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Vklopi/izklopi QGIS PREVIEW: končni 1\" GeoTIFF-i in "
            "PREVIEW/Slovenia_V1_0_FINAL.vrt. Privzeto izklopljeno."
        ),
    )
    ap.add_argument(
        "--qa",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Vklopi/izklopi QA rasterje: NATURAL_DOMINANT_FRACTION, "
            "QA_DOMINANCE_STATUS in pripadajoča nacionalna VRT-ja. "
            "Privzeto izklopljeno."
        ),
    )

    # Združljivost s starejšimi ukazi. Če sta uporabljena, sta samo aliasa
    # za --preview oziroma --qa. Ker uporabljata isti dest, zadnja navedena
    # možnost na ukazni vrstici odloči tudi pri kombinaciji z --no-preview/--no-qa.
    ap.add_argument(
        "--write-geotiff", dest="preview", action="store_true",
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--write-confidence", dest="qa", action="store_true",
        help=argparse.SUPPRESS,
    )

    args = ap.parse_args()

    print(f"{PROGRAM_NAME} V{PROGRAM_VERSION}")

    if args.block_size < 32:
        raise ValueError("--block-size naj bo vsaj 32.")
    if not (0.0 < args.other_threshold <= 1.0):
        raise ValueError("--other-threshold mora biti v (0,1].")
    for value, name in [
        (args.water_threshold, "--water-threshold"),
        (args.bare_threshold, "--bare-threshold"),
        (args.zero_clutter_threshold, "--zero-clutter-threshold"),
    ]:
        if value is not None and not (0.0 < value <= 1.0):
            raise ValueError(f"{name} mora biti v (0,1].")

    class_thresholds = resolve_class_thresholds(args)
    print(
        "Naravni pragovi 00..12: "
        + ", ".join(
            f"{cls:02d}={100.0*class_thresholds[cls]:g}%"
            for cls in NATURAL_CLASS_IDS
        )
    )
    if args.workers < 0:
        raise ValueError("--workers mora biti >= 0.")
    if args.kernel_workers < 1:
        raise ValueError("--kernel-workers mora biti >= 1.")
    if args.kernel_row_block < 32:
        raise ValueError("--kernel-row-block naj bo >= 32.")
    if args.kernel_quadrature < 8:
        raise ValueError("--kernel-quadrature naj bo >= 8.")
    if args.urban_radius <= 0:
        raise ValueError("--urban-radius mora biti > 0.")
    if args.urban_analysis_arcsec <= 0:
        raise ValueError("--urban-analysis-arcsec mora biti > 0.")
    if args.urban_supersample < 1:
        raise ValueError("--urban-supersample mora biti >= 1.")
    # Preverimo združljivost analizne mreže z notranjo rasterizacijo stavb.
    _analysis_n_check, analysis_arcsec_check = grid_parameters(args.urban_analysis_arcsec)
    internal_arcsec_check = FINAL_ARCSEC / float(args.urban_supersample)
    bf_check = analysis_arcsec_check / internal_arcsec_check
    if bf_check < 1 or not math.isclose(bf_check, round(bf_check), rel_tol=0.0, abs_tol=1e-10):
        raise ValueError(
            "--urban-analysis-arcsec in --urban-supersample nista združljiva: "
            f"analysis={analysis_arcsec_check:.9f}\", interno={internal_arcsec_check:.9f}\". "
            "Razmerje mora biti pozitivno celo število."
        )
    for v,n in [
        (args.urban_lo,"--urban-lo"),
        (args.tall_density,"--tall-density"),
        (args.dense_hi_density,"--dense-hi-density"),
    ]:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"{n} mora biti med 0 in 1.")
    if args.tall_height < 0:
        raise ValueError("--tall-height mora biti >= 0.")
    if args.dense_hi_height < 0:
        raise ValueError("--dense-hi-height mora biti >= 0.")

    original_dir = args.original_dir.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "_CACHE_PREPARED"
    cache.mkdir(parents=True, exist_ok=True)

    required = (
        original_dir, args.zgs, args.raba, args.gurs,
        args.hydro, args.buildings, args.mask_path,
    )
    for p in required:
        if not Path(p).exists():
            raise FileNotFoundError(p)

    if str(args.zgs_p70).strip().lower() == "auto":
        print("Računam nov nacionalni area-weighted ZGS P70 LZ/ha ...")
        p70 = calculate_zgs_p70(args.zgs)
    else:
        p70 = float(args.zgs_p70)
    print(f"ZGS P70 LZ/ha = {p70:.12f} m3/ha")

    raba_gpkg = cache / "raba_v1_0_full.gpkg"
    raba_physical_gpkg = cache / "raba_v1_0_physical.gpkg"
    gurs_gpkg = cache / "gurs_v1_0_specific.gpkg"
    zgs_gpkg = cache / "zgs_forest_v1_0.gpkg"
    hydro_gpkg = cache / "drsv_hydro_v1_0_water.gpkg"
    buildings_gpkg = cache / "gurs_buildings_v1_0.gpkg"

    if args.rebuild_cache or not raba_gpkg.exists():
        prepare_raba(args.raba, raba_gpkg)
    else:
        print(f"MKGP full cache: {raba_gpkg}")

    if args.rebuild_cache or not raba_physical_gpkg.exists():
        prepare_raba_physical(args.raba, raba_physical_gpkg)
    else:
        print(f"MKGP physical cache: {raba_physical_gpkg}")

    if args.rebuild_cache or not gurs_gpkg.exists():
        prepare_gurs(args.gurs, gurs_gpkg)
    else:
        print(f"GURS specific cache: {gurs_gpkg}")

    if args.rebuild_cache or not zgs_gpkg.exists():
        prepare_zgs(args.zgs, zgs_gpkg, p70)
    else:
        print(f"ZGS cache: {zgs_gpkg}")

    if args.rebuild_cache or not hydro_gpkg.exists():
        prepare_hydro(args.hydro, hydro_gpkg, tuple(args.hydro_symbols))
    else:
        print(f"DRSV hydro cache: {hydro_gpkg}")

    if args.rebuild_cache or not buildings_gpkg.exists():
        if buildings_gpkg.exists():
            buildings_gpkg.unlink()
        prepare_buildings(args.buildings, buildings_gpkg)
    else:
        print(f"GURS buildings cache: {buildings_gpkg}")

    urban_params = {
        "urban_radius": args.urban_radius,
        "analysis_arcsec": args.urban_analysis_arcsec,
        "urban_supersample": args.urban_supersample,
        "urban_lo": args.urban_lo,
        "tall_density": args.tall_density,
        "tall_height": args.tall_height,
        "dense_hi_density": args.dense_hi_density,
        "dense_hi_height": args.dense_hi_height,
        "kernel_mode": args.kernel_mode,
    }
    landheight_path = write_class_documentation(
        out, p70, args.supersample, class_thresholds,
        tuple(args.hydro_symbols), urban_params
    )

    tasks = []
    for tile in args.tiles:
        original_path = original_dir / f"{tile}.lcv"
        if not original_path.exists():
            print(f"OPOZORILO: manjka {original_path}; preskakujem.")
            continue
        tasks.append({
            "original_path": original_path,
            "output_dir": out,
            "raba_gpkg": raba_gpkg,
            "raba_physical_gpkg": raba_physical_gpkg,
            "zgs_gpkg": zgs_gpkg,
            "gurs_gpkg": gurs_gpkg,
            "hydro_gpkg": hydro_gpkg,
            "buildings_gpkg": buildings_gpkg,
            "mask_dem": args.mask_path,
            "factor": args.supersample,
            "block_size": args.block_size,
            "class_thresholds": class_thresholds,
            "urban_radius": args.urban_radius,
            "urban_analysis_arcsec": args.urban_analysis_arcsec,
            "urban_supersample": args.urban_supersample,
            "urban_lo": args.urban_lo,
            "tall_density": args.tall_density,
            "tall_height": args.tall_height,
            "dense_hi_density": args.dense_hi_density,
            "dense_hi_height": args.dense_hi_height,
            "kernel_mode": args.kernel_mode,
            "kernel_workers": args.kernel_workers,
            "kernel_row_block": args.kernel_row_block,
            "kernel_quadrature": args.kernel_quadrature,
            "write_geotiff": args.preview,
            "write_confidence": args.qa,
        })

    if not tasks:
        raise RuntimeError("Noben tile ni bil najden.")

    workers = choose_worker_count(args.workers, len(tasks))
    if workers > 1:
        print(
            "OPOZORILO: urbana 0,25\\\" analiza porabi veliko RAM-a; "
            f"zagnanih bo {workers} tile procesov hkrati."
        )
    print(f"Tile paralelizacija: {workers} worker(jev) za {len(tasks)} tile-ov")

    qa_rows = []
    if workers == 1:
        _worker_init(args.gdal_cache_mb)
        for task in tasks:
            task = dict(task)
            task["progress_inplace"] = True
            qa_rows.append(_process_tile_task(task))
    else:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(args.gdal_cache_mb,),
        ) as pool:
            future_to_tile = {}
            for task in tasks:
                task = dict(task)
                task["progress_inplace"] = False
                fut = pool.submit(_process_tile_task, task)
                future_to_tile[fut] = task["original_path"].stem.upper()

            for fut in as_completed(future_to_tile):
                tile = future_to_tile[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    for other in future_to_tile:
                        other.cancel()
                    raise RuntimeError(f"Tile worker FAIL {tile}: {exc}") from exc
                qa_rows.append(row)
                print(f"[MAIN] PASS {tile} ({len(qa_rows)}/{len(tasks)})")

    qa_rows.sort(key=lambda r: r.get("tile",""))
    write_csv(out / "LCV_QA_TILES_V1_0.csv", qa_rows)

    # Nacionalni VRT-ji za takojšen pregled v QGIS.
    # Izdelajo se samo, če je uporabnik izrecno vključil ustrezno skupino.
    if args.preview:
        build_vrt_file(
            out / "PREVIEW" / "Slovenia_V1_0_FINAL.vrt",
            sorted((out / "PREVIEW").glob("*_V1_0_FINAL_1arcsec.tif")),
            nodata=None,
        )
    if args.qa:
        build_vrt_file(
            out / "QA" / "Slovenia_V1_0_DOMINANT_FRACTION.vrt",
            sorted((out / "QA").glob("*_V1_0_NATURAL_DOMINANT_FRACTION_PCT.tif")),
            nodata=QA_OUTSIDE,
        )
        build_vrt_file(
            out / "QA" / "Slovenia_V1_0_QA_DOMINANCE_STATUS.vrt",
            sorted((out / "QA").glob("*_V1_0_QA_DOMINANCE_STATUS.tif")),
            nodata=QA_OUTSIDE,
        )

    print("\nKONČANO")
    print(f"Output: {out}")
    print(f"QA:     {out / 'LCV_QA_TILES_V1_0.csv'}")
    print(f"RF:     {landheight_path}")
    print(f"Preview: {'ON' if args.preview else 'OFF'}")
    print(f"QA:      {'ON' if args.qa else 'OFF'}")
    if args.preview:
        print(f"Preview VRT: {out / 'PREVIEW' / 'Slovenia_V1_0_FINAL.vrt'}")
    if args.qa:
        print(f"QA VRT:      {out / 'QA' / 'Slovenia_V1_0_QA_DOMINANCE_STATUS.vrt'}")


if __name__ == "__main__":
    freeze_support()
    main()
