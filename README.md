# 🛰️ Guardián Bello — Fase 1 (Piloto)

Detección temprana de invasiones en la frontera urbano-rural de **Bello, Antioquia**.

**Hipótesis**: los puntos de calor anómalos preceden al loteo ilegal y a las
construcciones ilegales en la periferia (Quitasol, El Mirador de Niquía).

## 📦 Instalación

### 1. Clonar el repositorio
```bash
git clone https://github.com/tu-usuario/guardian-bello.git
cd guardian-bello
```

### 2. Crear entorno virtual
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. (Solo Linux) Dependencias del sistema
```bash
sudo apt-get install -y gdal-bin libgdal-dev libspatialindex-dev proj-bin
```

##  Uso

### Ejecutar el pipeline de datos
```bash
python pipeline.py --run-pipeline
```

### Descargar puntos de calor recientes (FIRMS NRT)
```bash
python pipeline.py --download-firms --days 7
```

### Lanzar el dashboard Streamlit
```bash
streamlit run app.py
```

## 🌐 Despliegue en Streamlit Cloud

1. Sube el repositorio a GitHub.
2. En [share.streamlit.io](https://share.streamlit.io), conecta el repositorio.
3. Configura:
   - **Main file path**: `app.py`
   - **Python version**: `3.11` o `3.12`
4. Streamlit Cloud detectará automáticamente `requirements.txt` y `packages.txt`.
5. Haz clic en **Deploy**.

### ⚠️ Si falla el despliegue
- Ve a **Manage app** → **Reboot app** para forzar una nueva compilación.
- Verifica que `folium==0.14.0` y `streamlit-folium==0.16.0` estén en `requirements.txt`.

##  Datos usados

| Fuente | Resolución | Periodo | Uso |
|--------|-----------|---------|-----|
| NASA FIRMS (VIIRS 375m) | 375 m | 2018–2026 | Puntos de calor |
| Sentinel-2 L2A | 10 m | Cada ~5 días | NDVI y cambio de cobertura |
| POT Bello (simplificado) | Vectorial | — | Límite urbano/rural |

## 📁 Estructura
