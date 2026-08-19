# Actividad sísmica en Granada

Visualizador web móvil de los terremotos del catálogo del [Instituto Geográfico Nacional](https://www.ign.es/) y de las fallas cuaternarias de la base QAFI. También incluye un generador de vídeo MP4 para redes sociales.

La web publicada estará disponible en <https://draxus.github.io/ign_earthquake_visualizer/>.

## Web y actualización de datos

El workflow `.github/workflows/deploy-pages.yml` consulta el catálogo oficial del IGN y publica la web en GitHub Pages:

- Se ejecuta al subir cambios a `main`, manualmente y cada 12 horas (00:17 y 12:17 UTC).
- Consulta desde el 14/08/2026 hasta la fecha actual en la zona horaria de Madrid.
- Limita los resultados al área metropolitana de Granada: latitud 36.95–37.35 y longitud -3.90–-3.40.
- Valida las columnas y el contenido antes de publicar.
- Normaliza el resultado como CSV UTF-8 delimitado por punto y coma.
- Genera `data/catalogue.csv` únicamente dentro del artefacto de Pages; no crea commits automáticos.
- Si la descarga o la validación falla, la publicación se detiene y la versión anterior permanece disponible.

La visualización conserva el campo `Tipo Mag.` del IGN. Las magnitudes `mbLg` y `Mw` se muestran como series independientes, con colores, rangos y filtros propios; no se presentan como si pertenecieran a una única escala equivalente.

Cuando el IGN proporciona `Int. max.`, un anillo independiente muestra la intensidad máxima EMS-98. Esta capa puede ocultarse y su leyenda aclara que el anillo identifica el evento, no la extensión geográfica de los efectos.

Para activar el despliegue por primera vez, abre **Settings → Pages** en GitHub y elige **GitHub Actions** como fuente.

### Actualización local

```powershell
python -m pip install -r requirements-update.txt
python scripts/fetch_ign_catalogue.py --output data/catalogue.csv
python -m http.server 8000
```

Después abre <http://localhost:8000>. Para probar una fecha final concreta:

```powershell
python scripts/fetch_ign_catalogue.py --output data/catalogue.csv --end-date 19/08/2026
```

Las pruebas del actualizador se ejecutan con:

```powershell
python -m unittest discover -s tests -v
```

## Generación de vídeo

Requiere Python 3.10 o posterior. Instala sus dependencias y genera el vídeo con:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python render_earthquakes.py catalogo.csv -o terremotos.mp4
```

Consulta todas las opciones con `python render_earthquakes.py --help`.

El CSV debe incluir `Fecha`, `Hora`, `Latitud`, `Longitud` y `Mag.`. Puede estar delimitado por punto y coma, coma o tabulador, y codificado como UTF-8, Windows-1252 o Latin-1.

## Atribución

- Datos sísmicos: Instituto Geográfico Nacional (IGN).
- Fallas: base QAFI del Instituto Geológico y Minero de España (IGME-CSIC).
- Mapa: colaboradores de OpenStreetMap.
