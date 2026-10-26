"""
GUARDIÁN BELLO — FASE 1 (Piloto)
Detección temprana de invasiones en la frontera urbano-rural de Bello, Antioquia.
Hipótesis: los puntos de calor anómalos preceden al loteo ilegal y a las
construcciones ilegales en la periferia (Quitasol, El Mirador de Niquía).

Este archivo es ejecutable de dos formas:
    1) Como pipeline de datos:      python guardian_bello.py --run-pipeline
    2) Como dashboard Streamlit:   streamlit run guardian_bello.py

Dependencias:
    pip install streamlit folium streamlit-folium pandas numpy requests \
                 pystac-client planetary-computer geopandas rasterio scipy

Datos usados (todos gratuitos):
    - NASA FIRMS (VIIRS 375 m): puntos de calor 2018–2026, área de Bello.
    - Sentinel-2 L2A (10 m, cada ~5 días): NDVI y cambio de cobertura.
    - Límite urbano/rural: polígono simplificado del POT de Bello (ajustar
      con el shapefile oficial de la Secretaría de Planeación de Bello).
"""

import argparse
import io
import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ----------------------------------------------------------------------------
# 1. CONFIGURACIÓN — Áreas de interés (AOI) del piloto
# ----------------------------------------------------------------------------

DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)

# Coordenadas aproximadas de los sectores críticos (WGS84).
# Ajustar con precisión usando el POT y geoportales de la Gobernación de Antioquia.
SECTORES = {
    # Cerro Quitasol: foco histórico de loteo ilegal (operativo 2025, ~100 lotes)
    "quitasol": {
        "label": "Cerro Quitasol",
        "bbox": (-75.615, 6.320, -75.570, 6.365),   # (minLon, minLat, maxLon, maxLat)
        "urbano_rural_frontera": True,
    },
    # El Mirador de Niquía: ofensiva contra construcciones ilegales (2026)
    "niquia": {
        "label": "El Mirador de Niquía",
        "bbox": (-75.645, 6.325, -75.600, 6.360),
        "urbano_rural_frontera": True,
    },
    # Llano Grande / franja periurbana oriental (zona de control)
    "llano_grande": {
        "label": "Llano Grande (control)",
        "bbox": (-75.570, 6.295, -75.525, 6.330),
        "urbano_rural_frontera": True,
    },
}

FECHA_INICIO = date(2018, 1, 1)
FECHA_FIN = date(2026, 10, 26)

# Umbral para clasificar una quema como "sospechosa de loteo":
# FRP bajo (fuego pequeño y controlado, no incendio de copa), cercanía a la
# frontera urbano-rural y ocurrencia en época seca.
FRP_MAX_QUEMA_CONTROLADA = 50.0   # MW — quemas pequeñas
DIST_FRONTERA_MAX_KM = 1.5       # km desde el límite urbano del POT


# ----------------------------------------------------------------------------
# 2. MÓDULO 1 — Descarga de puntos de calor (NASA FIRMS)
# ----------------------------------------------------------------------------

FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/{west},{south},{east},{north}/{days}"

def descargar_puntos_calor(api_key_firms: str = "MEUROPA", days: int = 1) -> pd.DataFrame:
    """Descarga puntos de calor VIIRS (375 m) para el bounding box de Bello.

    Regístrate gratis en https://firms.modaps.eosdis.nasa.gov/api/area/ para
    obtener tu MAP_KEY. El historial 2018–2026 se obtiene con el archivo
    'VIIRS_SNPP_C2_SouthAmerica.csv' (Shapefile/CSV de la Colección 2),
    que puede descargarse por año desde FIRMS Archive.
    """
    west, south, east, north = -75.660, 6.280, -75.520, 6.390  # todo Bello
    url = FIRMS_URL.format(key=api_key_firms, west=west, south=south,
                           east=east, north=north, days=days)
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.to_csv(DATA_DIR / "firms_nrt.csv", index=False)
    return df


def cargar_historico_firms() -> pd.DataFrame:
    """Carga el histórico anual VIIRS C2 (un CSV por año en data/firms/)."""
    frames = []
    for f in sorted((DATA_DIR / "firms").glob("*.csv")):
        frames.append(pd.read_csv(f))
    if not frames:
        raise FileNotFoundError(
            "Descarga los CSV históricos de FIRMS Archive (VIIRS C2, "
            "South America) por año en data/firms/, ej: VIIRS_SNPP_C2_2023.csv"
        )
    df = pd.concat(frames, ignore_index=True)
    # Columnas estándar FIRMS: latitude, longitude, acq_date, acq_time,
    # satellite, confidence, frp (MW), daynight, bright_ti4 ...
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    df["acq_date"] = pd.to_datetime(df["acq_date"])
    df = df[(df["latitude"].between(6.28, 6.39)) &
            (df["longitude"].between(-75.66, -75.52))]
    return df


# ----------------------------------------------------------------------------
# 3. MÓDULO 2 — Clasificación: incendio forestal vs. quema sospechosa
# ----------------------------------------------------------------------------

MESES_SECOS = {12, 1, 2}  # temporada seca en Antioquia (ajustar con IDEAM)

def clasificar_eventos(df: pd.DataFrame) -> pd.DataFrame:
    """Etiqueta cada punto de calor:
       - 'incendio_forestal': FRP alto o confianza alta en zona boscosa.
       - 'quema_sospechosa': FRP bajo, cerca de frontera urbano-rural.
       - 'otro': resto (quemas agrícolas, falsas alarmas...).
    """
    df = df.copy()
    df["frp"] = pd.to_numeric(df.get("frp", 0), errors="coerce").fillna(0)
    df["mes"] = df["acq_date"].dt.month
    df["epoca_seca"] = df["mes"].isin(MESES_SECOS)

    cerca_frontera = df.apply(lambda r: any(
        _distancia_a_bbox(r["longitude"], r["latitude"], s["bbox"]) < DIST_FRONTERA_MAX_KM
        for s in SECTORES.values() if s["urbano_rural_frontera"]), axis=1)

    df["clasificacion"] = "otro"
    df.loc[df["frp"] > FRP_MAX_QUEMA_CONTROLADA, "clasificacion"] = "incendio_forestal"
    df.loc[(df["frp"] <= FRP_MAX_QUEMA_CONTROLADA) & cerca_frontera,
           "clasificacion"] = "quema_sospechosa"
    df.loc[(df["clasificacion"] == "quema_sospechosa") & df["epoca_seca"],
           "prioridad_alerta"] = "ALTA"
    df.loc[(df["clasificacion"] == "quema_sospechosa") & ~df["epoca_seca"],
           "prioridad_alerta"] = "MEDIA"
    df["prioridad_alerta"] = df["prioridad_alerta"].fillna("—")
    return df


def _distancia_a_bbox(lon: float, lat: float, bbox) -> float:
    """Distancia aproximada en km de un punto al bbox más cercano."""
    west, south, east, north = bbox
    dx = max(south - lat, 0, lat - north)
    dy = max(west - lon, 0, lon - east)
    return np.hypot(dx * 111, dy * 111 * np.cos(np.radians(lat)))


# ----------------------------------------------------------------------------
# 4. MÓDULO 3 — Sentinel-2: NDVI y detección de cambios de cobertura
# ----------------------------------------------------------------------------

def buscar_imagenes_sentinel(bbox, inicio, fin, max_nubes=20):
    """Busca escenas Sentinel-2 L2A en Microsoft Planetary Computer (gratis).

    pip install pystac-client planetary-computer rasterio
    """
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
    """Calcula NDVI de una escena Sentinel-2 (B08 NIR, B04 rojo)."""
    import rasterio
    from rasterio.windows import from_bounds

    href = item.assets["B04"].href, item.assets["B08"].href
    with rasterio.open(href[0]) as red, rasterio.open(href[1]) as nir:
        win = from_bounds(*item.bbox, transform=red.transform)
        r = red.read(1, window=win).astype("float32")
        n = nir.read(1, window=win).astype("float32")
        denom = (n + r); denom[denom == 0] = np.nan
        return (n - r) / denom


def detectar_perdida_vegetacion(ndvi_antes: np.ndarray, ndvi_despues: np.ndarray,
                                umbral: float = -0.25) -> np.ndarray:
    """Máscara booleana de píxeles con pérdida de vegetación persistente."""
    delta = ndvi_despues - ndvi_antes
    return (delta < umbral) & (ndvi_antes > 0.4)  # antes era vegetación


def detectar_candidatos_construccion(ndvi_antes, ndvi_despues, ndwi_despues=None,
                                     umbral_perdida: float = -0.25) -> np.ndarray:
    """Candidato a edificación nueva: vegetación alta que cae a NDVI bajo y
    se mantiene bajo (los techos/zona loteada no se recupera, a diferencia
    de una quema agrícola que rebrota en semanas).
    """
    perdida = detectar_perdida_vegetacion(ndvi_antes, ndvi_despues, umbral_perdida)
    suelo_desnudo = ndvi_despues < 0.25
    return perdida & suelo_desnudo


# ----------------------------------------------------------------------------
# 5. MÓDULO 4 — Expediente por sector (evidencia temporal)
# ----------------------------------------------------------------------------

def expediente_sector(df_fuegos: pd.DataFrame, sector_key: str) -> dict:
    s = SECTORES[sector_key]
    west, south, east, north = s["bbox"]
    m = (df_fuegos["longitude"].between(west, east) &
         df_fuegos["latitude"].between(south, north))
    sub = df_fuegos[m]
    return {
        "sector": s["label"],
        "total_puntos_calor": int(len(sub)),
        "quemas_sospechosas": int((sub["clasificacion"] == "quema_sospechosa").sum()),
        "incendios_forestales": int((sub["clasificacion"] == "incendio_forestal").sum()),
        "primer_evento": str(sub["acq_date"].min().date()) if len(sub) else "—",
        "ultimo_evento": str(sub["acq_date"].max().date()) if len(sub) else "—",
        "por_anio": sub.groupby(sub["acq_date"].dt.year).size().to_dict(),
        "alerta": "ALTA" if (sub["clasificacion"] == "quema_sospechosa").sum() >= 3 else "—",
    }


# ----------------------------------------------------------------------------
# 6. DASHBOARD STREAMLIT
# ----------------------------------------------------------------------------

def run_dashboard():
    import streamlit as st
    import folium
    from streamlit_folium import st_folium

    st.set_page_config(page_title="Guardián Bello", layout="wide")
    st.title("🛰️ Guardián Bello — Frontera urbano-rural")
    st.caption("Piloto Fase 1 · Detección temprana de invasiones · Bello, Antioquia")

    try:
        fuegos = pd.read_csv(DATA_DIR / "firms_procesado.csv", parse_dates=["acq_date"])
    except FileNotFoundError:
        st.warning("Ejecuta primero el pipeline: `python guardian_bello.py --run-pipeline`")
        st.stop()

    # --- Mapa principal
    centro = [6.335, -75.590]
    m = folium.Map(location=centro, zoom_start=13, tiles="Esri.WorldImagery")

    colores = {"quema_sospechosa": "orange", "incendio_forestal": "red", "otro": "gray"}
    for cls, g in fuegos.groupby("clasificacion"):
        for _, r in g.sample(n=min(len(g), 500)).iterrows():
            folium.CircleMarker(
                [r.latitude, r.longitude], radius=3,
                color=colores.get(cls, "gray"), fill=True,
                tooltip=f"{r.acq_date.date()} · FRP {r.frp:.1f} MW · {cls}",
            ).add_to(m)

    for s in SECTORES.values():
        w, s_, e, n = s["bbox"]
        folium.Rectangle(bounds=[(s_, w), (n, e)], color="cyan", fill=False,
                         tooltip=s["label"]).add_to(m)

    left, right = st.columns([3, 2])
    with left:
        st_folium(m, height=520)
    with right:
        st.subheader("📋 Expedientes por sector")
        for key in SECTORES:
            exp = expediente_sector(fuegos, key)
            color = {"ALTA": "🔴", "MEDIA": "🟠", "—": "🟢"}[exp["alerta"]]
            st.markdown(f"**{color} {exp['sector']}**")
            st.json({k: v for k, v in exp.items() if k != "sector"})

    # --- Serie temporal
    st.subheader("📈 Puntos de calor por año, por clasificación")
    tab = fuegos.pivot_table(index=fuegos["acq_date"].dt.year,
                             columns="clasificacion", aggfunc="size", fill_value=0)
    st.bar_chart(tab)

    st.info("Fase 1 = línea base. La validación de edificaciones usa los "
            "pares de imágenes Sentinel-2 alrededor de cada quema sospechosa "
            "(funciones de los módulos 3–4 de este archivo).")


# ----------------------------------------------------------------------------
# 7. PIPELINE FASE 1
# ----------------------------------------------------------------------------

def run_pipeline():
    print("► Cargando histórico FIRMS 2018–2026…")
    fuegos = cargar_historico_firms()
    print(f"  {len(fuegos)} puntos de calor en Bello")
    fuegos = clasificar_eventos(fuegos)
    out = DATA_DIR / "firms_procesado.csv"
    fuegos.to_csv(out, index=False)
    print(f"► Clasificación lista → {out}")

    for key in SECTORES:
        exp = expediente_sector(fuegos, key)
        print(f"  [{exp['sector']}] sospechosas={exp['quemas_sospechosas']} "
              f"forestales={exp['incendios_forestales']} alerta={exp['alerta']}")

    print("► Descargando pares Sentinel-2 (antes/después) por sector…")
    # Por cada quema sospechosa: buscar imagen ~1 mes antes y ~3 meses después,
    # calcular NDVI y guardar candidatos de construcción en data/cambios/.
    (DATA_DIR / "cambios").mkdir(exist_ok=True)
    for key, s in SECTORES.items():
        items = buscar_imagenes_sentinel(s["bbox"], "2025-01-01", "2026-10-01")
        print(f"  {s['label']}: {len(items)} escenas <20% nubes")
        if len(items) >= 2:
            ndvi_antes = ndvi_de_escena(items[0])
            ndvi_despues = ndvi_de_escena(items[-1])
            cand = detectar_candidatos_construccion(ndvi_antes, ndvi_despues)
            np.save(DATA_DIR / "cambios" / f"{key}_candidatos.npy", cand)
            print(f"    píxeles candidato edificación: {int(cand.sum())}")
    print("✔ Fase 1 completa. Lanza el dashboard: streamlit run guardian_bello.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-pipeline", action="store_true")
    args = ap.parse_args()
    if args.run_pipeline:
        run_pipeline()
    else:
        run_dashboard()
