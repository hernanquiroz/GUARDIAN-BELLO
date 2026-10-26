"""
GUARDIÁN BELLO — DASHBOARD STREAMLIT
"""
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from pathlib import Path
from datetime import date
import numpy as np

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

SECTORES = {
    "quitasol": {"label": "Cerro Quitasol", "bbox": (-75.615, 6.320, -75.570, 6.365), "urbano_rural_frontera": True},
    "niquia": {"label": "El Mirador de Niquía", "bbox": (-75.645, 6.325, -75.600, 6.360), "urbano_rural_frontera": True},
    "llano_grande": {"label": "Llano Grande (control)", "bbox": (-75.570, 6.295, -75.525, 6.330), "urbano_rural_frontera": True},
}

@st.cache_data(ttl=3600)
def cargar_datos() -> pd.DataFrame:
    ruta = DATA_DIR / "firms_procesado.csv"
    if not ruta.exists():
        return pd.DataFrame()
    return pd.read_csv(ruta, parse_dates=["acq_date"])

def _distancia_a_bbox(lon: float, lat: float, bbox) -> float:
    west, south, east, north = bbox
    dx = max(south - lat, 0, lat - north)
    dy = max(west - lon, 0, lon - east)
    return float(np.hypot(dx * 111, dy * 111 * np.cos(np.radians(lat))))

def expediente_sector(df: pd.DataFrame, sector_key: str) -> dict:
    s = SECTORES[sector_key]
    west, south, east, north = s["bbox"]
    m = (df["longitude"].between(west, east) & df["latitude"].between(south, north))
    sub = df[m].copy()
    sospechosas = int((sub["clasificacion"] == "quema_sospechosa").sum())
    return {
        "sector": s["label"],
        "total_puntos_calor": int(len(sub)),
        "quemas_sospechosas": sospechosas,
        "incendios_forestales": int((sub["clasificacion"] == "incendio_forestal").sum()),
        "primer_evento": str(sub["acq_date"].min().date()) if len(sub) else "—",
        "ultimo_evento": str(sub["acq_date"].max().date()) if len(sub) else "—",
        "por_anio": sub.groupby(sub["acq_date"].dt.year).size().to_dict(),
        "alerta": "ALTA" if sospechosas >= 3 else ("MEDIA" if sospechosas >= 1 else "—"),
    }

def run_dashboard():
    st.set_page_config(page_title="Guardián Bello", page_icon="🛰️", layout="wide")
    st.title("🛰️ Guardián Bello — Frontera urbano-rural")
    st.caption("Piloto Fase 1 · Detección temprana de invasiones · Bello, Antioquia · " + date.today().isoformat())

    fuegos = cargar_datos()

    if fuegos.empty:
        st.warning("⚠️ No se encontraron datos procesados. Ejecuta primero el pipeline localmente:\n\n```bash\npython pipeline.py --run-pipeline\n```\nLuego sube el archivo `data/firms_procesado.csv` a GitHub.")
        st.stop()

    with st.sidebar:
        st.header("🔍 Filtros")
        clasificaciones = st.multiselect("Clasificación", options=["quema_sospechosa", "incendio_forestal", "otro"], default=["quema_sospechosa", "incendio_forestal"])
        anio_min, anio_max = st.slider("Rango de años", min_value=int(fuegos["acq_date"].dt.year.min()), max_value=int(fuegos["acq_date"].dt.year.max()), value=(int(fuegos["acq_date"].dt.year.min()), int(fuegos["acq_date"].dt.year.max())))
        sector_filtro = st.multiselect("Sectores", options=list(SECTORES.keys()), default=list(SECTORES.keys()))

    fuegos_filtrado = fuegos[fuegos["clasificacion"].isin(clasificaciones) & fuegos["acq_date"].dt.year.between(anio_min, anio_max)].copy()
    st.metric("Total puntos de calor filtrados", len(fuegos_filtrado))

    st.subheader("🗺️ Mapa de puntos de calor")
    centro = [6.335, -75.590]
    m = folium.Map(location=centro, zoom_start=13, tiles="Esri.WorldImagery")
    colores = {"quema_sospechosa": "orange", "incendio_forestal": "red", "otro": "gray"}

    for cls, g in fuegos_filtrado.groupby("clasificacion"):
        muestra = g.sample(n=min(len(g), 500))
        for _, r in muestra.iterrows():
            folium.CircleMarker([r.latitude, r.longitude], radius=4, color=colores.get(cls, "gray"), fill=True, fill_opacity=0.7, tooltip=f"{r.acq_date.date()} · FRP {r.frp:.1f} MW · {cls}").add_to(m)

    for key, s in SECTORES.items():
        if key not in sector_filtro: continue
        w, s_, e, n = s["bbox"]
        folium.Rectangle(bounds=[(s_, w), (n, e)], color="cyan", weight=2, fill=False, tooltip=f"{s['label']}").add_to(m)

    left, right = st.columns([3, 2])
    with left:
        st_folium(m, height=520, use_container_width=True)
    with right:
        st.subheader("📋 Expedientes por sector")
        for key in SECTORES:
            if key not in sector_filtro: continue
            exp = expediente_sector(fuegos, key)
            icono = {"ALTA": "🔴", "MEDIA": "🟠", "—": "🟢"}[exp["alerta"]]
            with st.expander(f"{icono} {exp['sector']}", expanded=True):
                st.json({k: v for k, v in exp.items() if k != "sector"})

    st.subheader("📊 Puntos de calor por año y clasificación")
    tab = fuegos_filtrado.pivot_table(index=fuegos_filtrado["acq_date"].dt.year, columns="clasificacion", aggfunc="size", fill_value=0)
    st.bar_chart(tab)

if __name__ == "__main__":
    run_dashboard()
