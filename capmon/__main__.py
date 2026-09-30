"""Vigilancia de las publicaciones mensuales de capacidad de acceso.

    python -m capmon vigilar [--sin-avisos] [--forzar] [--fuente ID ...]
    python -m capmon inspeccionar ID
    python -m capmon comparar --fuente ID ANTERIOR.csv ACTUAL.csv [--salida informe.xlsx]
    python -m capmon probar-avisos
    python -m capmon panel
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import sys
from pathlib import Path

import yaml

from . import avisos, fuentes as F
from .comparar import FormatoDesconocido, Resultado, comparar
from .informes import ahora, escribir_excel, generar_html, limpiar_error, registrar_error, registrar_publicacion
from .tabla import leer_tabla, seleccionar_metricas


def cargar_config(raiz: Path) -> dict:
    return yaml.safe_load((raiz / "config.yaml").read_text(encoding="utf-8"))


def _estado(raiz: Path) -> dict:
    ruta = raiz / "state" / "estado.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}


def _guardar_estado(raiz: Path, estado: dict) -> None:
    ruta = raiz / "state" / "estado.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def _guardar_bruto(raiz: Path, fuente_id: str, enlace: F.Enlace, contenido: bytes) -> Path:
    ruta = raiz / "data" / fuente_id / f"{enlace.fecha.isoformat()}_{hashlib.sha256(contenido).hexdigest()[:8]}.{enlace.formato}.gz"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(gzip.compress(contenido))
    return ruta


def _url_publica(raiz: Path, ruta: Path) -> str:
    base = os.environ.get("PANEL_URL", "").strip()
    return f"{base.rstrip('/')}/{ruta.relative_to(raiz / 'docs').as_posix()}" if base else ""


def procesar(raiz: Path, config: dict, fuente: dict, s, estado: dict, sin_avisos: bool, forzar: bool) -> str:
    fid = fuente["id"]
    acc = F.acceso(fuente, s)
    texto, url_pagina = acc.pagina(fuente["pagina"])
    enlaces = F.descubrir(texto, url_pagina, fuente["patron_fichero"])
    actual = F.elegir(enlaces)
    if not actual:
        raise FormatoDesconocido("No encuentro en la página ningún fichero que encaje con 'patron_fichero'.")

    previo = estado.get(fid, {})
    huella = acc.firma(actual.url)
    hoy = ahora().date().isoformat()
    if not forzar and previo.get("url") == actual.url:
        if huella and previo.get("firma") == huella:
            return "sin cambios"
        if not huella and previo.get("comprobado") == hoy:
            return "sin cambios (revisado hoy)"

    contenido = acc.descargar(actual.url)
    sha = hashlib.sha256(contenido).hexdigest()
    if not forzar and previo.get("sha256") == sha:
        previo.update(firma=huella, comprobado=hoy)
        return "sin cambios (mismo contenido)"

    df_act = leer_tabla(contenido, actual.nombre())
    ruta_act = _guardar_bruto(raiz, fid, actual, contenido)

    anterior, contenido_ant, arranque = None, None, False
    fichero_prev = raiz / previo["fichero"] if previo.get("fichero") else None
    if fichero_prev and fichero_prev.exists() and previo.get("sha256") != sha:
        anterior = F.Enlace(dt.date.fromisoformat(previo["fecha"]), previo.get("formato", "csv"), previo["url"])
        contenido_ant = gzip.decompress(fichero_prev.read_bytes())
    else:
        arranque = True
        anterior = F.elegir(enlaces, antes_de=actual.fecha)
        if not anterior and fuente.get("anterior_por_url"):
            anterior = F.anterior_por_url(s, actual)
        if anterior:
            contenido_ant = acc.descargar(anterior.url)
            _guardar_bruto(raiz, fid, anterior, contenido_ant)

    if contenido_ant is None:
        resultado = Resultado([], ["No hay publicación anterior con la que comparar; se toma esta como referencia."],
                              0, len(df_act))
    else:
        resultado = comparar(leer_tabla(contenido_ant, anterior.nombre()), df_act, fuente,
                             umbral=float(config.get("umbral_incremento_mw", 10)),
                             excluir_afloramientos=config.get("excluir_afloramientos_de_incrementos", True),
                             vacio_como_cero=config.get("vacio_como_cero", True))

    informe = raiz / "docs" / "informes" / f"{fid}_{actual.fecha.isoformat()}.xlsx"
    escribir_excel(informe, fuente, actual, anterior, resultado, float(config.get("umbral_incremento_mw", 10)))
    registrar_publicacion(raiz, fuente, actual, anterior, resultado, informe, F.sello(texto, fuente.get("sello")))
    estado[fid] = {"url": actual.url, "fecha": actual.fecha.isoformat(), "formato": actual.formato, "firma": huella,
                   "sha256": sha, "fichero": ruta_act.relative_to(raiz).as_posix(), "comprobado": hoy,
                   "detectado": ahora().isoformat()}

    cuenta = resultado.contadores()
    resumen = ", ".join(f"{k}={v}" for k, v in cuenta.items() if v) or "sin novedades"
    if not sin_avisos:
        asunto, cuerpo = avisos.componer(fuente, actual, anterior, resultado, _url_publica(raiz, informe),
                                         int(config.get("max_lineas_aviso", 15)), arranque)
        for error in avisos.enviar(asunto, cuerpo, informe):
            print(f"  ! aviso no enviado ({error})")
    return f"NUEVA publicación {actual.fecha:%d/%m/%Y} → {resumen}"


def vigilar(raiz: Path, sin_avisos: bool = False, forzar: bool = False, solo: list[str] | None = None) -> int:
    config = cargar_config(raiz)
    estado, s, fallos = _estado(raiz), F.sesion(), 0
    print(f"Vigilancia {ahora():%d/%m/%Y %H:%M} · avisos: {', '.join(avisos.canales_configurados()) or 'ninguno configurado'}")
    for fuente in config["fuentes"]:
        if solo and fuente["id"] not in solo:
            continue
        try:
            print(f"- {fuente['nombre']}: {procesar(raiz, config, fuente, s, estado, sin_avisos, forzar)}")
            limpiar_error(raiz, fuente)
        except Exception as e:
            fallos += 1
            mensaje = f"{type(e).__name__}: {e}"[:300]
            print(f"- {fuente['nombre']}: ERROR {mensaje}")
            if registrar_error(raiz, fuente, mensaje) and not sin_avisos:
                avisos.enviar(f"Vigilancia de capacidades: fallo en {fuente['nombre']}",
                              f"<b>No he podido revisar {html.escape(fuente['nombre'])}</b>\n{html.escape(mensaje)}\n"
                              f"Puede que haya cambiado la web o el formato. Diagnóstico: python -m capmon inspeccionar {fuente['id']}")
        _guardar_estado(raiz, estado)
    F.AccesoNavegador.cerrar()
    generar_html(raiz, config)
    return 1 if fallos == len([f for f in config["fuentes"] if not solo or f["id"] in solo]) else 0


def inspeccionar(raiz: Path, fid: str) -> None:
    config = cargar_config(raiz)
    fuente = next(f for f in config["fuentes"] if f["id"] == fid)
    acc = F.acceso(fuente, F.sesion())
    texto, url_pagina = acc.pagina(fuente["pagina"])
    print(f"Página: {url_pagina}\nSello: {F.sello(texto, fuente.get('sello')) or '(no encontrado)'}")
    enlaces = F.descubrir(texto, url_pagina, fuente["patron_fichero"])
    print(f"Ficheros que encajan con el patrón ({len(enlaces)}):")
    for e in enlaces[:8]:
        print(f"   {e.fecha}  {e.formato}  {e.url}")
    actual = F.elegir(enlaces)
    if not actual:
        print("Ninguno. Revisa 'patron_fichero' en config.yaml. Enlaces de la página con csv/xlsx:")
        for e in F.descubrir(texto, url_pagina, r"(\d{4})\D?(\d{2})\D?(\d{2})"):
            print(f"   {e.url}")
        return
    df = leer_tabla(acc.descargar(actual.url), actual.nombre())
    print(f"\nFichero vigente: {actual.nombre()}  ({len(df)} filas)\nColumnas detectadas:")
    for c in df.columns:
        print(f"   {c}")
    print("\nColumnas vigiladas:")
    for col, etiqueta, cat in seleccionar_metricas(df, fuente["metricas"]):
        print(f"   [{cat}] {etiqueta}")


def comparar_local(raiz: Path, fid: str, anterior: Path, actual: Path, salida: Path | None) -> None:
    config = cargar_config(raiz)
    fuente = next(f for f in config["fuentes"] if f["id"] == fid)
    df_ant, df_act = leer_tabla(anterior.read_bytes(), anterior.name), leer_tabla(actual.read_bytes(), actual.name)
    res = comparar(df_ant, df_act, fuente, float(config.get("umbral_incremento_mw", 10)),
                   config.get("excluir_afloramientos_de_incrementos", True), config.get("vacio_como_cero", True))
    hoy = dt.date.today()
    e_act, e_ant = F.Enlace(hoy, "csv", str(actual)), F.Enlace(hoy, "csv", str(anterior))
    asunto, cuerpo = avisos.componer(fuente, e_act, e_ant, res, "", 50)
    print(html.unescape(cuerpo.replace("<b>", "").replace("</b>", "")))
    if salida:
        escribir_excel(salida, fuente, e_act, e_ant, res, float(config.get("umbral_incremento_mw", 10)))
        print(f"\nInforme: {salida}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="capmon", description="Vigilancia de mapas de capacidad de acceso")
    p.add_argument("--raiz", type=Path, default=Path.cwd(), help="carpeta del repositorio (por defecto, la actual)")
    sub = p.add_subparsers(dest="orden", required=True)
    v = sub.add_parser("vigilar", help="revisa todas las fuentes y avisa si hay publicación nueva")
    v.add_argument("--sin-avisos", action="store_true")
    v.add_argument("--forzar", action="store_true", help="reprocesa aunque el fichero no haya cambiado")
    v.add_argument("--fuente", nargs="*", help="solo estas fuentes (ids de config.yaml)")
    i = sub.add_parser("inspeccionar", help="muestra enlaces y columnas detectadas de una fuente")
    i.add_argument("fuente")
    c = sub.add_parser("comparar", help="compara dos ficheros descargados a mano")
    c.add_argument("--fuente", required=True)
    c.add_argument("anterior", type=Path)
    c.add_argument("actual", type=Path)
    c.add_argument("--salida", type=Path)
    sub.add_parser("probar-avisos", help="envía un mensaje de prueba por los canales configurados")
    sub.add_parser("panel", help="regenera docs/index.html")
    a = p.parse_args(argv)

    if a.orden == "vigilar":
        return vigilar(a.raiz, a.sin_avisos, a.forzar, a.fuente)
    if a.orden == "inspeccionar":
        inspeccionar(a.raiz, a.fuente)
    elif a.orden == "comparar":
        comparar_local(a.raiz, a.fuente, a.anterior, a.actual, a.salida)
    elif a.orden == "probar-avisos":
        canales = avisos.canales_configurados()
        errores = avisos.enviar("Prueba de la vigilancia de capacidades",
                                "<b>Prueba de la vigilancia de capacidades</b>\nSi lees esto, los avisos funcionan.")
        print(f"Canales: {', '.join(canales) or 'ninguno'}; errores: {errores or 'ninguno'}")
        return 1 if errores or not canales else 0
    elif a.orden == "panel":
        print(generar_html(a.raiz, cargar_config(a.raiz)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
