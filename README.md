# Visualizador de terremotos del IGN

Genera un vídeo MP4 cuadrado y cronológico a partir de un catálogo de terremotos descargado del [Instituto Geográfico Nacional](https://www.ign.es/).

El resultado está pensado para redes sociales: mapa fijo en escala de grises, burbujas cuyo tamaño y color representan la magnitud, contador de eventos, fecha, atribución de datos y marca de agua.

## Características

- Salida MP4 H.264 en formato cuadrado.
- Resolución predeterminada de 1080×1080, 30 FPS y 30 segundos.
- Mapa de OpenStreetMap descargado y almacenado en caché local.
- Fondo en escala de grises para resaltar los terremotos.
- Tamaño y color de cada burbuja asociados a su magnitud.
- Animación cronológica acumulativa con pulso para el evento más reciente.
- Textos y fechas en español de España.
- Lectura de CSV en UTF-8, Windows-1252 y Latin-1.
- Opciones para limitar eventos o comenzar en el primer terremoto que supere una magnitud.

## Requisitos

- Python 3.10 o posterior.
- Conexión a Internet en la primera ejecución para descargar los mosaicos del mapa.

FFmpeg se instala mediante `imageio-ffmpeg`; no es necesario instalarlo por separado.

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

En Linux o macOS, activa el entorno con:

```bash
source .venv/bin/activate
```

## Uso

Generación estándar:

```powershell
python render_earthquakes.py catalogo.csv -o terremotos.mp4
```

Para omitir el tramo inicial anterior al primer terremoto de magnitud 3:

```powershell
python render_earthquakes.py catalogo.csv -o terremotos.mp4 --start-magnitude 3
```

Prueba rápida con los 10 primeros eventos seleccionados:

```powershell
python render_earthquakes.py catalogo.csv -o prueba.mp4 `
  --duration 10 --fps 15 --size 540 `
  --start-magnitude 3 --max-events 10
```

### Opciones principales

| Opción | Valor predeterminado | Descripción |
|---|---:|---|
| `-o`, `--output` | `earthquake_animation.mp4` | Archivo MP4 de salida. |
| `--duration` | `30` | Duración del vídeo en segundos. |
| `--fps` | `30` | Fotogramas por segundo. |
| `--size` | `1080` | Anchura y altura del vídeo cuadrado. |
| `--cache-dir` | `.map_cache` | Directorio de caché de los mosaicos. |
| `--map-style` | `grayscale` | Estilo `grayscale` o `color`. |
| `--max-events` | Sin límite | Usa únicamente los primeros N eventos seleccionados. |
| `--start-magnitude` | Desactivado | Omite el tramo anterior al primer evento que alcance la magnitud indicada. |

Consulta todas las opciones con:

```powershell
python render_earthquakes.py --help
```

## Formato del CSV

El archivo debe estar delimitado por punto y coma, coma o tabulador y contener estas columnas obligatorias:

- `Fecha`, en formato `DD/MM/AAAA`.
- `Hora`, en formato `HH:MM:SS`.
- `Latitud`.
- `Longitud`.
- `Mag.`.

También se reconocen `Evento`, `Prof. (Km)`, `Inten.`, `Tipo Mag.` y `Localización`. Los espacios sobrantes de encabezados y valores se eliminan automáticamente. Las filas inválidas se descartan con un aviso.

## Archivos generados

Los vídeos, imágenes de comprobación, mosaicos descargados y catálogos CSV no se incluyen en Git. Cada usuario aporta su propio catálogo del IGN.

## Atribución

- Datos sísmicos: Instituto Geográfico Nacional (IGN).
- Mapa: © colaboradores de OpenStreetMap.

El vídeo muestra la atribución abreviada `Datos: IGN | Mapa: OSM`.
