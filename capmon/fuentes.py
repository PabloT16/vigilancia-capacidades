"""Localización del fichero vigente en cada web y descarga educada (con reintentos)."""
from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .tabla import patron, quitar_acentos

AGENTE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/128.0 Safari/537.36 VigilanciaCapacidades/1.0")


@dataclass(frozen=True)
class Enlace:
    fecha: dt.date
    formato: str
    url: str

    def nombre(self) -> str:
        return unquote(urlparse(self.url).path.rsplit("/", 1)[-1]) or "fichero"


def sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": AGENTE, "Accept-Language": "es-ES,es;q=0.9"})
    return s


def pedir(s: requests.Session, url: str, metodo: str = "GET", intentos: int = 3, **kw) -> requests.Response:
    ultimo_error = None
    for i in range(intentos):
        try:
            r = s.request(metodo, url, timeout=60, allow_redirects=True, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            return r
        except requests.RequestException as e:
            ultimo_error = e
            time.sleep(5 * 2 ** i)
    raise ultimo_error


def _formato(a, url: str) -> str | None:
    ruta = unquote(urlparse(url).path).lower()
    for ext in ("xlsx", "csv", "pdf"):
        if ruta.endswith("." + ext):
            return ext
    # i-DE no pone extensión en la URL: el formato va en el icono del enlace.
    pistas = " ".join([a.get_text(" ")] + [f"{i.get('src', '')} {i.get('alt', '')}" for i in a.find_all("img")]).lower()
    for ext in ("xlsx", "csv", "pdf"):
        if ext in pistas:
            return ext
    return None


def descubrir(html: str, base: str, expr: str) -> list[Enlace]:
    """Todos los ficheros CSV/XLSX de la página cuyo nombre encaja con el patrón, del más reciente al más antiguo."""
    rx = patron(expr)
    encontrados = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urljoin(base, a["href"].strip())
        m = rx.search(quitar_acentos(unquote(url)))
        if not m:
            continue
        formato = _formato(a, url)
        if formato not in ("csv", "xlsx"):
            continue
        try:
            fecha = dt.date(*(int(g) for g in m.groups()[:3]))
        except ValueError:
            continue
        encontrados[(formato, url)] = Enlace(fecha, formato, url)
    return sorted(encontrados.values(), key=lambda e: (e.fecha, e.formato == "csv"), reverse=True)


def elegir(enlaces: list[Enlace], antes_de: dt.date | None = None, preferido: str = "csv") -> Enlace | None:
    candidatos = [e for e in enlaces if antes_de is None or e.fecha < antes_de]
    if not candidatos:
        return None
    fecha = candidatos[0].fecha
    mismos = [e for e in candidatos if e.fecha == fecha]
    return next((e for e in mismos if e.formato == preferido), mismos[0])


def sello(html: str, expr: str | None) -> str:
    if not expr:
        return ""
    texto = quitar_acentos(re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")))
    m = patron(expr).search(texto)
    return m.group(1).strip() if m else ""


def firma(s: requests.Session, url: str) -> str:
    """Huella barata del fichero remoto (ETag / Last-Modified / tamaño) sin descargarlo."""
    try:
        r = pedir(s, url, "HEAD", intentos=2)
        if r.status_code >= 400:
            return ""
        return "|".join(r.headers.get(h, "") for h in ("ETag", "Last-Modified", "Content-Length"))
    except requests.RequestException:
        return ""


def descargar(s: requests.Session, url: str) -> bytes:
    r = pedir(s, url)
    r.raise_for_status()
    if r.content[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError(f"La URL devolvió una página web en lugar de un fichero: {url}")
    return r.content


def anterior_por_url(s: requests.Session, actual: Enlace) -> Enlace | None:
    """REE solo enlaza el fichero vigente; el del mes anterior se busca cambiando la fecha de la URL."""
    marca = actual.fecha.strftime("%Y_%m_%d")
    año, mes = (actual.fecha.year, actual.fecha.month - 1) if actual.fecha.month > 1 else (actual.fecha.year - 1, 12)
    for dia in range(1, 11):
        fecha = dt.date(año, mes, dia)
        url = actual.url.replace(marca, fecha.strftime("%Y_%m_%d"))
        try:
            r = pedir(s, url, "HEAD", intentos=1)
            if r.status_code == 200 and "html" not in r.headers.get("Content-Type", ""):
                return Enlace(fecha, actual.formato, url)
        except requests.RequestException:
            continue
    return None
