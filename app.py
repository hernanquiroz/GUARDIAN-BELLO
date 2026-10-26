
"""
GUARDIÁN BELLO — DASHBOARD STREAMLIT CON DATOS REALES
Descarga automática de datos de NASA FIRMS
"""
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from pathlib import Path
from datetime import date, timedelta
import numpy as np
import requests
import io

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

SECTORES = {
    "quitasol": {"label": "Cerro Quitasol", "bbox": (-75.615, 6.320, -75.570, 6.365), "urbano_rural_frontera": True},
    "niquia": {"label": "El Mirador de Niquía", "bbox": (-75.645, 6.325, -75.600, 6.360), "urbano_rural_frontera": True},
    "llano_grande": {"label": "Llano Grande (control)", "bbox": (-75.570, 6.295, -75.525, 6.330), "urbano_rural_frontera": True},
}

FRP_MAX_QUEMA_CONTROLADA = 50.0
DIST_FRONTERA_MAX_KM = 1.5
MESES_SECOS = {12, 1, 2}

# Bounding box de Bello, Antioquia
BELLO_BBOX = {
    "west": -75.660,
    "south": 6.280,
    "east": -75.520,
    "north": 6.390
}

@st.cache_data(ttl=3600)
def descargar_firms_reales(days: int = 30) -> pd.DataFrame:
    """Descarga datos reales de NASA FIRMS para Bello."""
    st.info("📡 Descargando datos reales de NASA FIRMS...")
    
    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"MEUROPA/VIIRS_SNPP_NRT/"
        f"{BELLO_BBOX['west']},{BELLO_BBOX['south']},"
        f"{BELLO_BBOX['east']},{BELLO_BBOX['north']}/"
        f"{days}"
    )
    
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        
        df = pd.read_csv(io.StringIO(response.text))
        
        # Normalizar columnas
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Filtrar solo el área de Bello
        if 'latitude' in df.columns and 'longitude' in df.columns:
            df = df[
                df['latitude'].between(6.28, 6.39) &
                df['longitude'].between(-75.66, -75.52)
            ].copy()
        
        # Convertir fecha
        if 'acq_date' in df.columns:
            df['acq_date'] = pd.to_datetime(df['acq_date'])
        
        st.success(f"✅ Descargados {len(df)} puntos de calor reales")
        return df
        
    except Exception as e:
        st.error(f"❌ Error al descargar datos: {e}")
        return pd.DataFrame()

def clasificar_eventos_reales(df: pd.DataFrame) -> pd.DataFrame:
    """Clasifica los puntos de calor descargados."""
    if df.empty:
        return df
    
    df = df.copy()
    
    # FRP
    if 'frp' in df.columns:
        df['frp'] = pd.to_numeric(df['frp'], errors='coerce').fillna(0)
    else:
        df['frp'] = 0
    
    # Mes y época seca
    df['mes'] = df['acq_date'].dt.month
    df['epoca_seca'] = df['mes'].isin(MESES_SECOS)
    
    # Distancia a frontera
    def cerca_frontera(row):
        if 'longitude' not in row or 'latitude' not in row:
            return False
        lon, lat = row['longitude'], row['latitude']
        return any(
            _distancia_a_bbox(lon, lat, s['bbox']) < DIST_FRONTERA_MAX_KM
            for s in SECTORES.values()
            if s['urbano_rural_frontera']
        )
    
    df['cerca_frontera'] = df.apply(cerca_frontera, axis=1)
    
    # Clasificación
    df['clasificacion'] = 'otro'
    df.loc[df['frp'] > FRP_MAX_QUEMA_CONTROLADA, 'clasificacion'] = 'incendio_forestal'
    df.loc[
        (df['frp'] <= FRP_MAX_QUEMA_CONTROLADA) & df['cerca_frontera'],
        'clasificacion'
    ] = 'quema_sospechosa'
    
    # Prioridad
    df['prioridad_alerta'] = '—'
    df.loc[
        (df['clasificacion'] == 'quema_sospechosa') & df['epoca_seca'],
        'prioridad_alerta'
    ] = 'ALTA'
    df.loc[
        (df['clasificacion'] == 'quema_sospechosa') & ~df['epoca_seca'],
        'prioridad_alerta'
    ] = 'MEDIA'
    
    return df

def _distancia_a_bbox(lon: float, lat: float, bbox) -> float:
    west, south, east, north = bbox
    dx = max(south - lat, 0, lat - north)
    dy = max(west - lon, 0, lon - east)
    return float(np.hypot(dx * 111, dy * 111 * np.cos(np.radians(lat))))

def expediente_sector(df: pd.DataFrame, sector_key: str) -> dict:
    s = SECTORES[sector_key]
    west, south, east, north = s['bbox']
    m = (df['longitude'].between(west, east) & df['latitude'].between(south, north))
    sub = df[m].copy()
    sospechosas = int((sub['clasificacion'] == 'quema_sospechosa').sum())
    return {
        'sector': s['label'],
        'total_puntos_calor': int(len(sub)),
        'quemas_sospechosas': sospechosas,
        'incendios_forestales': int((sub['clasificacion'] == 'incendio_forestal').sum()),
        'primer_evento': str(sub['acq_date'].min().date()) if len(sub) else '—',
        'ultimo_evento': str(sub['acq_date'].max().date()) if len(sub) else '—',
        'por_anio': sub.groupby(sub['acq_date'].dt.year).size().to_dict(),
        'alerta': 'ALTA' if sospechosas >= 3 else ('MEDIA' if sospechosas >= 1 else '—'),
    }

def run_dashboard():
    st.set_page_config(page_title="Guardián Bello", page_icon="️", layout="wide")
    st.title("🛰️ Guardián Bello — Frontera urbano-rural")
    st.caption("Piloto Fase 1 · Detección temprana de invasiones · Bello, Antioquia · " + date.today().isoformat())

    # Descargar datos reales
    fuegos_raw = descargar_firms_reales(days=30)
    
    if fuegos_raw.empty:
        st.error("❌ No se pudieron descargar datos reales de NASA FIRMS.")
        st.info("💡 Verifica tu conexión o intenta más tarde.")
        st.stop()
    
    # Clasificar eventos
    fuegos = clasificar_eventos_reales(fuegos_raw)
    
    # Guardar para uso futuro
    fuegos.to_csv(DATA_DIR / "firms_procesado.csv", index=False)

    with st.sidebar:
        st.header("🔍 Filtros")
        clasificaciones = st.multiselect(
            "Clasificación",
            options=["quema_sospechosa", "incendio_forestal", "otro"],
            default=["quema_sospechosa", "incendio_forestal"]
        )
        
        if not fuegos.empty:
            anio_min = int(fuegos['acq_date'].dt.year.min())
            anio_max = int(fuegos['acq_date'].dt.year.max())
            anio_min, anio_max = st.slider(
                "Rango de años",
                min_value=anio_min,
                max_value=anio_max,
                value=(anio_min, anio_max)
            )
        else:
            anio_min, anio_max = 2020, 2026
        
        sector_filtro = st.multiselect(
            "Sectores",
            options=list(SECTORES.keys()),
            default=list(SECTORES.keys())
        )

    # Filtrar datos
    fuegos_filtrado = fuegos[
        fuegos['clasificacion'].isin(clasificaciones)
    ].copy()
    
    st.metric("🔥 Total puntos de calor (últimos 30 días)", len(fuegos_filtrado))

    # Mapa
    st.subheader("🗺️ Mapa de puntos de calor - Bello, Antioquia")
    centro = [6.335, -75.590]
    m = folium.Map(location=centro, zoom_start=13, tiles="Esri.WorldImagery")
    colores = {"quema_sospechosa": "orange", "incendio_forestal": "red", "otro": "gray"}

    for cls, g in fuegos_filtrado.groupby("clasificacion"):
        muestra = g.sample(n=min(len(g), 500))
        for _, r in muestra.iterrows():
            folium.CircleMarker(
                [r.latitude, r.longitude],
                radius=5,
                color=colores.get(cls, "gray"),
                fill=True,
                fill_opacity=0.8,
                tooltip=f"📅 {r.acq_date.date()}<br>🔥 FRP: {r.frp:.1f} MW<br>📊 {cls}<br>️ Prioridad: {r.get('prioridad_alerta', '—')}"
            ).add_to(m)

    # Dibujar sectores
    for key, s in SECTORES.items():
        if key not in sector_filtro:
            continue
        w, s_, e, n = s['bbox']
        folium.Rectangle(
            bounds=[(s_, w), (n, e)],
            color="cyan",
            weight=2,
            fill=True,
            fill_opacity=0.1,
            tooltip=f"{s['label']} ({key})"
        ).add_to(m)

    left, right = st.columns([3, 2])
    with left:
        st_folium(m, height=600, use_container_width=True)
    
    with right:
        st.subheader("📋 Expedientes por sector")
        for key in SECTORES:
            if key not in sector_filtro:
                continue
            exp = expediente_sector(fuegos, key)
            icono = {"ALTA": "🔴", "MEDIA": "🟠", "—": "🟢"}[exp["alerta"]]
            with st.expander(f"{icono} {exp['sector']}", expanded=True):
                st.json({k: v for k, v in exp.items() if k != "sector"})

    # Gráfico temporal
    if not fuegos_filtrado.empty:
        st.subheader("📊 Distribución por clasificación")
        tab = fuegos_filtrado['clasificacion'].value_counts()
        st.bar_chart(tab)
        
        # Tabla de datos
        with st.expander("📄 Ver tabla de datos reales"):
            st.dataframe(
                fuegos_filtrado.sort_values('acq_date', ascending=False),
                use_container_width=True,
                height=400
            )

    st.success("✅ Datos reales de NASA FIRMS (últimos 30 días)")

if __name__ == "__main__":
    run_dashboard()
