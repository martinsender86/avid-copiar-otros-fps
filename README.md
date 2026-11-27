# Copiar Otros FPS

Herramienta para **Avid Media Composer** (Windows y Mac).

## Qué hace

Busca en todos los bins de un proyecto los master clips y subclips cuyo **FPS no es el del proyecto** y los copia a bins nuevos: **un bin por FPS**.

```
Proyecto "MiDoc" a 25 fps con material mezclado  ->
   MiDoc_23.98fps.avb   MiDoc_29.97fps.avb   MiDoc_50fps.avb ...
```

Los bins se crean en una carpeta al lado de la carpeta de proyectos (`.../Avid Projects/MiDoc` → `.../MiDoc_OTROS_FPS`), fuera de "Avid Projects" para que Avid no la tome como un proyecto.

- No toca los bins del proyecto: solo los lee.
- No incluye secuencias, group clips, multicam ni clips de solo audio.
- Ignora la papelera de Avid (Trash) y el Attic.
- Funciona con cualquier proyecto: no tiene rutas fijas.
- Si lo corrés de nuevo, a los bins que ya existen solo les agrega los clips nuevos (antes guarda un backup en `_backup`).

## Requisitos

Python 3 (en Windows, con "Add python.exe to PATH"; en Mac, el instalador "universal2" de python.org). La primera vez instala solo la librería `pyavb` (necesita internet).

## Cómo se usa

- **Windows:** arrastrá la carpeta del proyecto sobre `Copiar_Otros_FPS.bat`.
- **Mac:** doble clic en `Copiar_Otros_FPS.command` y arrastrá la carpeta del proyecto a la ventana.

1. El script averigua el FPS del proyecto y te lo muestra. Si está bien, Enter; si no, escribí el correcto.
2. Muestra qué clips va a copiar, agrupados por FPS, con los formatos de cada grupo (ej. `1920x1080p (120), 3840x2160p (8)`).
3. Escribí `S` y Enter para crear los bins.

Después, para cada bin: creá en Avid un proyecto nuevo con ese FPS y formato, cerralo, copiá el bin a la carpeta del proyecto y volvé a abrirlo. Los clips traen una columna "Formato" con su resolución.

Avanzado: `python copiar_otros_fps.py "carpeta del proyecto" [--fps 25] [--salida CARPETA] [--dry-run] [--si]`

Más detalle en `LEEME.txt`.
