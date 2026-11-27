#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copiar_otros_fps.py  —  "Copiar Otros FPS"

Para cualquier proyecto de Avid Media Composer: busca en TODOS sus bins los
master clips y subclips cuyo FPS no coincide con el del proyecto, y los copia
a bins nuevos en una carpeta aparte, UN BIN POR FPS:
    <Proyecto>_23.98fps.avb, <Proyecto>_24fps.avb, <Proyecto>_50fps.avb...

Cada clip copiado lleva una columna "Formato" (ej. "1920x1080p") y la vista
previa resume qué formatos hay en cada FPS, para crear en Avid el proyecto
nuevo con el formato correcto y arrastrarle el bin.

El FPS del proyecto se averigua así (y siempre se puede corregir):
  1. del archivo .avp, si es un tipo de proyecto conocido;
  2. si no, del FPS de las secuencias del proyecto (heredan el del proyecto);
  3. si no hay secuencias, se pregunta.

Los bins del proyecto solo se leen, nunca se modifican.
Si un bin de destino ya existe, solo se le agregan los clips que falten.
No hace falta cerrar Avid, pero conviene que el proyecto esté cerrado.

Uso (Windows: arrastrar la carpeta del proyecto sobre el .bat;
     Mac: doble clic en el .command y arrastrar la carpeta a la ventana):
    python copiar_otros_fps.py [carpeta_del_proyecto] [opciones]
      --fps 25          FPS del proyecto (no lo detecta)
      --salida CARPETA  dónde crear los bins (por defecto, al lado de la
                        carpeta de proyectos: <Proyecto>_OTROS_FPS)
      --dry-run         solo muestra qué haría
      --si              no pregunta nada (para usar en otros scripts)

Funciona en Windows y en Mac (Intel o Apple Silicon). Requiere pyavb.
"""

import os
import re
import sys
import glob
import shlex
import struct
import shutil
import argparse
import datetime
import tempfile

try:
    import avb
except ImportError:
    import subprocess
    print("Instalando pyavb (solo la primera vez)...")
    subprocess.call([sys.executable, "-m", "pip", "install", "pyavb"])
    try:
        import avb
    except ImportError:
        sys.exit("\nNo se pudo instalar pyavb. Probá a mano:  pip install pyavb")


# ----------------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------------
TOLERANCIA = 0.01

# Carpetas del proyecto que no se recorren (la papelera de Avid y el Attic).
CARPETAS_IGNORADAS = ["Trash", "Papelera", "Avid Attic", "Attic"]

# Clips con alguna de estas etiquetas entre corchetes en el nombre se saltean,
# por ej. ["MC", "B"]. Vacío = no se excluye ninguno.
ETIQUETAS_EXCLUIDAS = []

# Columna custom que se agrega a los bins nuevos con el formato de cada clip.
COLUMNA_FORMATO = "Formato"

# Tipos de proyecto del .avp (PRJ_PROJ_TYPE) ya confirmados -> FPS.
# Avid no documenta esta numeración: solo se cargan los verificados con
# proyectos reales. Si el tipo no está acá, el FPS sale de las secuencias.
TIPOS_PROYECTO = {
    # 16: 25.0,   # pendiente de confirmar (1080p/25?)
}
# ----------------------------------------------------------------------------

USER_COLUMN_FORMAT = 2   # texto
USER_COLUMN_TYPE = 40    # columna de usuario

# Etiquetas "de Avid" para los FPS fraccionarios
NOMBRES_FPS = [(23.976, "23.98"), (29.97, "29.97"), (47.952, "47.95"),
               (59.94, "59.94"), (119.88, "119.88")]


def nombre_fps(fps):
    for valor, nombre in NOMBRES_FPS:
        if abs(fps - valor) <= TOLERANCIA:
            return nombre
    if abs(fps - round(fps)) <= TOLERANCIA:
        return "%d" % round(fps)
    return ("%.3f" % fps).rstrip("0").rstrip(".")


def leer_fps_texto(texto):
    """'23.98' -> 23.976, '29.97' -> 29.97, '25' -> 25.0. None si no es válido."""
    try:
        v = float(texto.replace(",", ".").lower().replace("fps", "").strip())
    except ValueError:
        return None
    if v <= 0 or v > 300:
        return None
    for valor, nombre in NOMBRES_FPS:
        if abs(v - valor) <= 0.006 or nombre == ("%g" % v):
            return valor
    return v


def mismo_fps(a, b):
    return abs(a - b) <= TOLERANCIA


# ---------------------------------------------------------------------------
# Proyecto
# ---------------------------------------------------------------------------

def normalizar_proyecto(ruta):
    """Acepta la carpeta del proyecto o el .avp. Devuelve (carpeta, avp|None)."""
    ruta = os.path.abspath(os.path.expanduser(ruta.strip()))
    if os.path.isfile(ruta) and ruta.lower().endswith(".avp"):
        return os.path.dirname(ruta), ruta
    if os.path.isdir(ruta):
        avps = sorted(p for p in glob.glob(os.path.join(ruta, "*.avp"))
                      if not os.path.basename(p).startswith("."))
        return ruta, (avps[0] if avps else None)
    return None, None


def leer_avp(avp):
    """Atributos enteros del .avp: {'PRJ_PROJ_TYPE': 16, ...}"""
    with open(avp, "rb") as fh:
        datos = fh.read()
    attrs = {}
    for m in re.finditer(rb"\x01\x00\x00\x00([\x03-\x40])\x00(PRJ_[A-Z0-9_]+)", datos):
        nombre = m.group(2)
        if len(nombre) != m.group(1)[0]:
            continue
        p = m.end()
        if p + 4 <= len(datos):
            attrs[nombre.decode("ascii")] = struct.unpack("<i", datos[p:p + 4])[0]
    return attrs


def fps_de_secuencias(bins):
    """FPS más frecuente entre las secuencias del proyecto."""
    cuenta = {}
    for ruta_bin in bins:
        try:
            with avb.open(ruta_bin) as f:
                for it in f.content.items:
                    m = it.mob
                    if not it.user_placed or m is None:
                        continue
                    if getattr(m, "mob_type", "") == "CompositionMob" and \
                            getattr(m, "usage", None) is None:
                        k = round(float(m.edit_rate), 3)
                        cuenta[k] = cuenta.get(k, 0) + 1
        except Exception:
            continue
    if not cuenta:
        return None, 0, 0
    fps = max(cuenta, key=cuenta.get)
    return fps, cuenta[fps], sum(cuenta.values())


def detectar_fps(avp, bins):
    """Devuelve (fps|None, explicación)."""
    tipo = None
    if avp:
        try:
            attrs = leer_avp(avp)
            tipo = attrs.get("PRJ_PROJ_TYPE")
            if tipo in TIPOS_PROYECTO:
                return TIPOS_PROYECTO[tipo], "leído de %s" % os.path.basename(avp)
        except OSError:
            pass
    fps, n, total = fps_de_secuencias(bins)
    if fps:
        return fps, "según las secuencias del proyecto (%d de %d)" % (n, total)
    extra = " (tipo de proyecto %s, todavía no confirmado)" % tipo if tipo is not None else ""
    return None, "no hay secuencias en el proyecto para deducirlo%s" % extra


def carpeta_salida_por_defecto(carpeta_proyecto):
    """Al lado de la carpeta de proyectos, para que Avid no la tome como proyecto:
    .../Avid Projects/MiProyecto  ->  .../MiProyecto_OTROS_FPS"""
    nombre = os.path.basename(carpeta_proyecto.rstrip("\\/"))
    contenedora = os.path.dirname(os.path.dirname(carpeta_proyecto.rstrip("\\/")))
    return os.path.join(contenedora, nombre + "_OTROS_FPS")


def fps_del_clip(mob):
    """FPS de la columna FPS del bin: el edit rate de la pista de video.
    Devuelve None si el clip no tiene video (solo audio)."""
    for track in mob.tracks:
        comp = getattr(track, "component", None)
        if comp is not None and getattr(comp, "media_kind", None) == "picture":
            return float(comp.edit_rate)
    return None


# ---------------------------------------------------------------------------
# Formato (resolución y barrido)
# ---------------------------------------------------------------------------

def formato_del_clip(mob):
    """'1920x1080p', '1920x1080i', o '' si no se puede saber."""
    vistos = []
    try:
        deps = list(mob.dependant_mobs())
    except Exception:
        deps = []
    for dm in [mob] + deps:
        if getattr(dm, "mob_type", "") != "SourceMob":
            continue
        try:
            desc = dm.descriptor
        except Exception:
            continue
        descs = [desc]
        sub = getattr(desc, "descriptors", None) if desc is not None and \
            "descriptors" in getattr(desc, "property_data", {}) else None
        if sub:
            descs += list(sub)
        for d in descs:
            pd = getattr(d, "property_data", None)
            if not pd:
                continue
            w = pd.get("stored_width")
            h = pd.get("stored_height")
            if not w or not h:
                continue
            layout = pd.get("frame_layout")
            # Avid guarda el alto por campo en los formatos entrelazados
            if layout in (1, 2, 3):
                barrido = "i"
                if layout in (1, 3) and h * 2 in (480, 486, 576, 1080):
                    h = h * 2
            else:
                barrido = "p"
            vistos.append("%dx%d%s" % (w, h, barrido))
    if not vistos:
        return ""
    return max(set(vistos), key=vistos.count)


# ---------------------------------------------------------------------------
# Bins
# ---------------------------------------------------------------------------

def etiqueta_excluida(nombre):
    if not ETIQUETAS_EXCLUIDAS:
        return False
    etiquetas = re.findall(r"\[\s*([^\]]+?)\s*\]", nombre)
    excluidas = set(e.upper() for e in ETIQUETAS_EXCLUIDAS)
    return any(e.upper() in excluidas for e in etiquetas)


def listar_bins(carpeta, excluir):
    bins = []
    excluir = os.path.normcase(os.path.abspath(excluir))
    for raiz, dirs, archivos in os.walk(carpeta):
        dirs[:] = [d for d in dirs if d not in CARPETAS_IGNORADAS
                   and not d.startswith(".")]
        if os.path.normcase(os.path.abspath(raiz)).startswith(excluir):
            continue
        for a in archivos:
            # "._algo.avb" son archivos ocultos de Mac en discos exFAT/NTFS
            if a.lower().endswith(".avb") and not a.startswith("."):
                bins.append(os.path.join(raiz, a))
    return sorted(bins)


def es_clip_buscado(mob):
    """Master clips y subclips. Nada de secuencias, group clips ni multicam."""
    tipo = getattr(mob, "mob_type", "")
    uso = getattr(mob, "usage", None)
    if tipo == "MasterMob":
        return True
    if tipo == "CompositionMob" and uso == "subclip":
        return True
    return False


def asegurar_columna(bin_obj, columna):
    view = bin_obj.view_setting
    cols = list(view.columns)
    for c in cols:
        if c.get("title") == columna:
            if not c.get("hidden"):
                return
            c["hidden"] = False
            break
    else:
        cols.append({"title": columna, "format": USER_COLUMN_FORMAT,
                     "type": USER_COLUMN_TYPE, "hidden": False})
    view.columns = cols
    view.mark_modified()


def poner_valor(f, mob, columna, valor):
    if not valor:
        return
    attrs = mob.attributes
    if attrs is None:
        attrs = f.create.Attributes()
        mob.attributes = attrs
    user = attrs.get("_USER")
    if user is None:
        user = f.create.Attributes()
        attrs["_USER"] = user
    user[columna] = valor
    attrs.mark_modified()


def bloqueado(ruta_bin):
    return os.path.exists(os.path.splitext(ruta_bin)[0] + ".lck")


def escribir_bin(destino, por_bin, ruta_backup):
    """por_bin: {ruta_bin_origen: [(mob_id, formato)]}. Crea el bin destino o
    agrega a uno existente. Abre los bins de origen de a uno.
    Devuelve cuántos clips se agregaron."""
    existe = os.path.isfile(destino)
    dst = avb.open(destino) if existe else avb.open()
    try:
        ids = set(str(m.mob_id) for m in dst.content.mobs)
        agregados = 0
        for ruta_bin in sorted(por_bin):
            buscados = dict(por_bin[ruta_bin])
            with avb.open(ruta_bin) as src:
                for it in src.content.items:
                    mob = it.mob
                    if not it.user_placed or mob is None:
                        continue
                    mid = str(mob.mob_id)
                    if mid not in buscados or mid in ids:
                        continue
                    # master clip / file mobs de los que depende: ocultos, como Avid
                    for dep in mob.dependant_mobs():
                        if str(dep.mob_id) in ids:
                            continue
                        item = dst.content.add_mob(dep.copy(dst))
                        item.user_placed = False
                        ids.add(str(dep.mob_id))
                    nuevo = mob.copy(dst)
                    poner_valor(dst, nuevo, COLUMNA_FORMATO, buscados[mid])
                    dst.content.add_mob(nuevo).user_placed = True
                    ids.add(mid)
                    agregados += 1
        if not agregados:
            return 0
        asegurar_columna(dst.content, COLUMNA_FORMATO)

        if existe:
            os.makedirs(ruta_backup, exist_ok=True)
            shutil.copy2(destino, os.path.join(ruta_backup, os.path.basename(destino)))
        fd, tmp = tempfile.mkstemp(suffix=".avb", dir=os.path.dirname(destino))
        os.close(fd)
        try:
            dst.write(tmp)
        except Exception:
            os.remove(tmp)
            raise
    finally:
        dst.close()
    # permisos normales (mkstemp los deja solo para el dueño)
    try:
        if existe:
            shutil.copymode(destino, tmp)
        else:
            mask = os.umask(0)
            os.umask(mask)
            os.chmod(tmp, 0o666 & ~mask)
    except OSError:
        pass
    os.replace(tmp, destino)
    return agregados


# ---------------------------------------------------------------------------
# Programa
# ---------------------------------------------------------------------------

def preguntar(texto):
    try:
        return input(texto).strip()
    except EOFError:
        return ""


def parse_arrastrado(texto):
    """Ruta arrastrada a la ventana. Mac: '/Volumes/X\\ Y/Proy'; Windows: '"C:\\a b"'."""
    texto = texto.strip()
    if not texto:
        return ""
    if os.name != "nt":
        try:
            partes = shlex.split(texto)
            if partes:
                return partes[0]
        except ValueError:
            pass
    return texto.strip('"').strip("'")


def escanear(bins, fps_proy):
    por_fps = {}          # fps -> [(ruta_bin, mob_id, nombre, formato)]
    vistos = set()
    r = {"iguales": 0, "solo_audio": 0, "excluidos": 0, "repetidos": 0, "errores": []}
    for ruta_bin in bins:
        try:
            with avb.open(ruta_bin) as src:
                for it in src.content.items:
                    if not it.user_placed or it.mob is None:
                        continue
                    mob = it.mob
                    if not es_clip_buscado(mob):
                        continue
                    nombre = mob.name or ""
                    if etiqueta_excluida(nombre):
                        r["excluidos"] += 1
                        continue
                    fps = fps_del_clip(mob)
                    if fps is None:
                        r["solo_audio"] += 1
                        continue
                    if mismo_fps(fps, fps_proy):
                        r["iguales"] += 1
                        continue
                    mid = str(mob.mob_id)
                    if mid in vistos:          # el mismo clip en varios bins
                        r["repetidos"] += 1
                        continue
                    vistos.add(mid)
                    por_fps.setdefault(nombre_fps(fps), []).append(
                        (ruta_bin, mid, nombre, formato_del_clip(mob)))
        except Exception as e:
            r["errores"].append((os.path.basename(ruta_bin), repr(e)))
    return por_fps, r


def ruta_bin_destino(carpeta_salida, prefijo, clave):
    return os.path.join(carpeta_salida, "%s_%sfps.avb" % (prefijo, clave))


def reporte(por_fps, r, carpeta_salida, prefijo):
    print("Con el FPS del proyecto (no se copian): %d" % r["iguales"])
    if r["solo_audio"]:
        print("Solo audio (no tienen FPS de video, no se copian): %d" % r["solo_audio"])
    if r["excluidos"]:
        print("Excluidos por etiqueta %s: %d" % (ETIQUETAS_EXCLUIDAS, r["excluidos"]))
    if r["repetidos"]:
        print("Repetidos en más de un bin (se copian una sola vez): %d" % r["repetidos"])
    if not por_fps:
        print("\nNo hay clips con otro FPS. No hay nada para copiar.")
    for clave in sorted(por_fps, key=float):
        clips = por_fps[clave]
        formatos = {}
        for _, _, _, fmt in clips:
            k = fmt or "formato desconocido"
            formatos[k] = formatos.get(k, 0) + 1
        destino = ruta_bin_destino(carpeta_salida, prefijo, clave)
        print("\n== %s fps: %d clips  ->  %s%s" % (
            clave, len(clips), os.path.basename(destino),
            "  (ya existe: se agregan los que falten)" if os.path.isfile(destino) else ""))
        print("   Formatos: " + ", ".join("%s (%d)" % kv for kv in
                                         sorted(formatos.items(), key=lambda kv: -kv[1])))
        for rb, _, nombre, fmt in clips:
            print("   + %-50s %-12s [%s]" % (nombre or "(sin nombre)", fmt, os.path.basename(rb)))
    if r["errores"]:
        print("\nBins que no se pudieron leer (%d):" % len(r["errores"]))
        for b, e in r["errores"]:
            print("  ! %s: %s" % (b, e))


def copiar(por_fps, carpeta_salida, prefijo):
    os.makedirs(carpeta_salida, exist_ok=True)
    ruta_backup = os.path.join(carpeta_salida, "_backup",
                               datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    for clave in sorted(por_fps, key=float):
        destino = ruta_bin_destino(carpeta_salida, prefijo, clave)
        if bloqueado(destino):
            print("SALTEADO %s: está abierto en Avid (.lck). Cerralo y volvé a correr."
                  % os.path.basename(destino))
            continue
        por_bin = {}
        for rb, mid, _, fmt in por_fps[clave]:
            por_bin.setdefault(rb, []).append((mid, fmt))
        n = escribir_bin(destino, por_bin, ruta_backup)
        print("%-32s %4d clips agregados" % (os.path.basename(destino), n))


def main():
    ap = argparse.ArgumentParser(description="Copia los clips con otro FPS que el del "
                                             "proyecto a un bin nuevo por FPS.")
    ap.add_argument("proyecto", nargs="?", help="Carpeta del proyecto de Avid (o su .avp)")
    ap.add_argument("--fps", help="FPS del proyecto, ej. 25 o 23.98 (no lo detecta)")
    ap.add_argument("--salida", help="Carpeta donde crear los bins")
    ap.add_argument("--dry-run", action="store_true", help="Solo mostrar qué haría")
    ap.add_argument("--si", action="store_true", help="No preguntar nada")
    args = ap.parse_args()
    interactivo = not args.si

    print("=" * 64)
    print("  Copiar Otros FPS")
    print("  Un bin nuevo por cada FPS distinto al del proyecto")
    print("=" * 64)

    # --- proyecto ---
    entrada = args.proyecto
    while True:
        if not entrada:
            if not interactivo:
                sys.exit("Indicá la carpeta del proyecto.")
            entrada = parse_arrastrado(preguntar(
                "\nArrastrá a esta ventana la carpeta del proyecto (o su .avp) y apretá Enter:\n> "))
            if not entrada:
                print("Cancelado.")
                return
        carpeta_proyecto, avp = normalizar_proyecto(entrada)
        if carpeta_proyecto and (avp or glob.glob(os.path.join(carpeta_proyecto, "*.avb"))
                                 or glob.glob(os.path.join(carpeta_proyecto, "*", "*.avb"))):
            break
        print("\nEso no parece la carpeta de un proyecto de Avid:\n   %s" % entrada)
        if not interactivo:
            sys.exit(1)
        entrada = None

    nombre_proy = os.path.basename(carpeta_proyecto.rstrip("\\/"))
    carpeta_salida = os.path.abspath(os.path.expanduser(args.salida)) if args.salida \
        else carpeta_salida_por_defecto(carpeta_proyecto)
    bins = listar_bins(carpeta_proyecto, carpeta_salida)

    print("\nProyecto:    %s" % carpeta_proyecto)
    print("Bins:        %d (sin contar Trash ni Attic)" % len(bins))
    print("Bins nuevos: %s" % carpeta_salida)

    # --- FPS del proyecto ---
    if args.fps:
        fps_proy = leer_fps_texto(args.fps)
        if fps_proy is None:
            sys.exit("FPS no válido: %s" % args.fps)
        origen = "indicado con --fps"
    else:
        print("\nAveriguando el FPS del proyecto...")
        fps_proy, origen = detectar_fps(avp, bins)

    if interactivo:
        while True:
            if fps_proy:
                txt = preguntar("FPS del proyecto: %s  (%s)\n"
                                "Apretá Enter si está bien, o escribí el correcto: "
                                % (nombre_fps(fps_proy), origen))
                if not txt:
                    break
            else:
                print("No pude averiguar el FPS del proyecto: %s." % origen)
                txt = preguntar("Escribí el FPS del proyecto (ej. 25, 23.98, 29.97): ")
                if not txt:
                    print("Cancelado.")
                    return
            nuevo = leer_fps_texto(txt)
            if nuevo is None:
                print("No entiendo '%s'. Escribilo como 25, 23.98, 29.97, 50..." % txt)
                continue
            fps_proy, origen = nuevo, "indicado a mano"
            break
    elif not fps_proy:
        sys.exit("No pude averiguar el FPS del proyecto (%s). Indicalo con --fps." % origen)

    print("\nFPS del proyecto: %s  (%s)" % (nombre_fps(fps_proy), origen))
    print("\nBuscando clips con otro FPS...\n")
    por_fps, r = escanear(bins, fps_proy)
    reporte(por_fps, r, carpeta_salida, nombre_proy)

    if args.dry_run:
        print("\n--dry-run: no se modificó nada.")
        return
    if not por_fps:
        return
    if interactivo and preguntar("\n¿Crear estos bins? Escribí S y apretá Enter: ").upper() not in ("S", "SI", "SÍ", "Y"):
        print("\nCancelado. No se modificó nada.")
        return

    print("")
    copiar(por_fps, carpeta_salida, nombre_proy)
    print("\nListo. Los bins están en:\n   " + carpeta_salida)
    print("\nPara cada FPS: creá en Avid un proyecto nuevo con ese FPS y el formato")
    print("que indica la lista, cerralo, copiá el bin a la carpeta de ese proyecto")
    print("y volvé a abrirlo.")


if __name__ == "__main__":
    main()
