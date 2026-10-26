"""
GUARDIÁN BELLO — PIPELINE DE DATOS
Descarga, clasificación y análisis de puntos de calor FIRMS + Sentinel-2.

Ejecución:
    python pipeline.py --run-pipeline
    python pipeline.py --download-firms --days 7
"""

import argparse
import io
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "firms").mkdir(exist_ok=True)
(DATA_DIR / "cambios").mkdir(exist_ok=True)

SECTORES = {
    "quitasol": {
        "label": "Cerro Quitasol",
        "bbox": (-75.615, 6.320, -75.570, 6.365),
        "urbano_rural_frontera": True,
    },
    "niquia": {
        "label": "El Mirador de Niquía",
        "bbox": (-75.645, 6.325, -75.600, 6.360),
        "urbano_rural_frontera": True,
    },
    "llano_grande": {
        "label": "Llano Grande (control)",
        "bbox": (-75.570, 6.295, -75.525, 6.330),
        "urbano_rural_frontera": True,
    },
}

FRP_MAX_QUEMA_CONTROLADA = 50.0
DIST_FRONTERA_MAX_KM = 1.5
MESES_SECOS = {12, 1, 2}

FIRMS_URL = (
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
    "{key}/VIIRS_SNPP_NRT/{west},{south},{east},{north}/{days}"
)

# ---------------------------------------------------------------------------
# Módulo 1: Descarga FIRMS
# ---------------------------------------------------------------------------

def descargar_puntos_calor(api_key: str = "MEUROPA", days: int = 7) -> pd.DataFrame:
    """Descarga puntos de calor VIIRS NRT para el bounding box de Bello."""
    west, south, east, north = -75.660, 6.280, -75.520, 6.390
    url = FIRMS_URL.format(
        key=api_key, west=west, south=south,
        east=east, north=north, days=days,
    )
    print(f"  → GET {url}")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    out = DATA_DIR / "firms" / f"firms_nrt_{date.today().isoformat()}.csv"
    df.to_csv(out, index=False)
    print(f"  ✔ Guardado: {out} ({len(df)} registros)")
    return df


def cargar_historico_firms() -> pd.DataFrame:
    """Carga todos los CSV históricos de data/firms/."""
    frames = []
    for f in sorted((DATA_DIR / "firms").glob("*.csv")):
        frames.append(pd.read_csv(f))
    if not frames:
        raise FileNotFoundError(
            "No hay CSVs en data/firms/. Descárgalos primero con:\n"
            "  python pipeline.py --download-firms --days 365"
        )
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip().lower() for c in df.columns]
    df["acq_date"] = pd.to_datetime(df["acq_date"])
    df = df[
        df["latitude"].between(6.28, 6.39) &
        df["longitude"].between(-75.66, -75.52)
    ].reset_index(drop=True)
    return df

# ---------------------------------------------------------------------------
# Módulo 2: Clasificación
# ---------------------------------------------------------------------------

def _distancia_a_bbox(lon: float, lat: float, bbox) -> float:
    west, south, east, north = bbox
    dx = max(south - lat, 0, lat - north)
    dy = max(west - lon, 0, lon - east)
    return float(np.hypot(dx * 111, dy * 111 * np.cos(np.radians(lat))))


def clasificar_eventos(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["frp"] = pd.to_numeric(df.get("frp", 0), errors="coerce").fillna(0)
    df["mes"] = df["acq_date"].dt.month
    df["epoca_seca"] = df["mes"].isin(MESES_SECOS)

    def cerca_frontera(row):
        return any(
            _distancia_a_bbox(row["longitude"], row["latitude"], s["bbox"])
            < DIST_FRONTERA_MAX_KM
            for s in SECTORES.values()
            if s["urbano_rural_frontera"]
        )

    df["cerca_frontera"] = df.apply(cerca_frontera, axis=1)
    df["clasificacion"] = "otro"
    df.loc[df["frp"] > FRP_MAX_QUEMA_CONTROLADA, "clasificacion"] = "incendio_forestal"
    df.loc[
        (df["frp"] <= FRP_MAX_QUEMA_CONTROLADA) & df["cerca_frontera"],
        "clasificacion",
    ] = "quema_sospechosa"

    df["prioridad_alerta"] = "—"
    df.loc[
        (df["clasificacion"] == "quema_sospechosa") & df["epoca_seca"],
        "prioridad_alerta",
    ] = "ALTA"
    df.loc[
        (df["clasificacion"] == "quema_sospechosa") & ~df["epoca_seca"],
        "prioridad_alerta",
    ] = "MEDIA"

    df.drop(columns=["cerca_frontera", "mes", "epoca_seca"], inplace=True)
    return df

# ---------------------------------------------------------------------------
# Módulo 3: Sentinel-2 (NDVI)
# ---------------------------------------------------------------------------

def buscar_imagenes_sentinel(bbox, inicio, fin, max_nubes=20):
    from pystac_client import Client
    import planetary_computer as pc

    cat = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
    )
    search = cat.search(
        collections=["sentinel-2-l2a"],
        bbox=list(bbox),
        datetime=f"{inicio}/{fin}",
        query={"eo:cloud_cover": {"lt": max_nubes}},
    )
    return list(search.items())


def ndvi_de_escena(item):
    import rasterio
    from rasterio.windows import from_bounds

    href_red = item.assets["B04"].href
    href_nir = item.assets["B08"].href

    with rasterio.open(href_red) as red, rasterio.open(href_nir) as nir:
        win = from_bounds(*item.bbox, transform=red.transform)
        r = red.read(1, window=win).astype("float32")
        n = nir.read(1, window=win).astype("float32")
        denom = n + r
        denom[denom == 0] = np.nan
        return (n - r) / denom


def detectar_candidatos_construccion(ndvi_antes, ndvi_despues, umbral=-0.25):
    perdida = (ndvi_despues - ndvi_antes < umbral) & (ndvi_antes > 0.4)
    suelo_desnudo = ndvi_despues < 0.25
    return perdida & suelo_desnudo

# ---------------------------------------------------------------------------
# Módulo 4: Expedientes
# ---------------------------------------------------------------------------

def expediente_sector(df: pd.DataFrame, sector_key: str) -> dict:
    s = SECTORES[sector_key]
    west, south, east, north = s["bbox"]
    m = (df["longitude"].between(west, east) &
         df["latitude"].between(south, north))
    sub = df[m]
    sospechosas = int((sub["clasificacion"] == "quema_sospechosa").sum())
    return {
        "sector": s["label"],
        "total_puntos_calor": int(len(sub)),
        "quemas_sospechosas": sospechosas,
        "incendios_forestales": int((sub["clasificacion"] == "incendio_forestal").sum()),
        "primer_evento": str(sub["acq_date"].min().date()) if len(sub) else "—",
        "ultimo_evento": str(sub["acq_date"].max().date()) if len(sub) else "—",
        "alerta": "ALTA" if sospechosas >= 3 else ("MEDIA" if sospechosas >= 1 else "—"),
    }

# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def run_pipeline():
    print("=" * 60)
    print("GUARDIÁN BELLO — Pipeline Fase 1")
    print("=" * 60)

    print("\n► [1/4] Cargando histórico FIRMS 2018–2026…")
    fuegos = cargar_historico_firms()
    print(f"  {len(fuegos)} puntos de calor en Bello")

    print("\n► [2/4] Clasificando eventos…")
    fuegos = clasificar_eventos(fuegos)
    out = DATA_DIR / "firms_procesado.csv"
    fuegos.to_csv(out, index=False)
    print(f"  ✔ Guardado: {out}")

    print("\n► [3/4] Expedientes por sector:")
    for key in SECTORES:
        exp = expediente_sector(fuegos, key)
        print(
            f"  [{exp['sector']}] "
            f"sospechosas={exp['quemas_sospechosas']} "
            f"forestales={exp['incendios_forestales']} "
            f"alerta={exp['alerta']}"
        )

    print("\n► [4/4] Buscando pares Sentinel-2 por sector…")
    for key, s in SECTORES.items():
        try:
            items = buscar_imagenes_sentinel(s["bbox"], "2025-01-01", "2026-10-01")
            print(f"  {s['label']}: {len(items)} escenas <20% nubes")
            if len(items) >= 2:
                ndvi_antes = ndvi_de_escena(items[0])
                ndvi_despues = ndvi_de_escena(items[-1])
                cand = detectar_candidatos_construccion(ndvi_antes, ndvi_despues)
                np.save(DATA_DIR / "cambios" / f"{key}_candidatos.npy", cand)
                print(f"    píxeles candidato edificación: {int(cand.sum())}")
        except Exception as e:
            print(f"  ⚠ {s['label']}: {e}")

    print("\n" + "=" * 60)
    print("✔ Fase 1 completa.")
    print("▶ Lanza el dashboard: streamlit run app.py")
    print("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Guardián Bello — Pipeline")
    ap.add_argument("--run-pipeline", action="store_true", help="Ejecuta el pipeline completo")
    ap.add_argument("--download-firms", action="store_true", help="Solo descarga FIRMS NRT")
    ap.add_argument("--days", type=int, default=7, help="Días hacia atrás para FIRMS NRT")
    ap.add_argument("--api-key", type=str, default="MEUROPA", help="MAP_KEY de FIRMS")
    args = ap.parse_args()

    if args.download_firms:
        descargar_puntos_calor(api_key=args.api_key, days=args.days)
    elif args.run_pipeline:
        run_pipeline()
    else:
        ap.print_help()
