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
          "Chrome/131.0.0.0 Safari/537.36")
CABECERAS = {
    "User-Agent": AGENTE,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


@dataclass(frozen=True)
class Enlace:
    fecha: dt.date
    formato: str
    url: str

    def nombre(self) -> str:
        return unquote(urlparse(self.url).path.rsplit("/", 1)[-1]) or "fichero"


def sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(CABECERAS)
    return s


def pedir(s: requests.Session, url: str, metodo: str = "GET", intentos: int = 4, **kw) -> requests.Response:
    ultimo_error = None
    for i in range(intentos):
        try:
            r = s.request(metodo, url, timeout=60, allow_redirects=True, **kw)
            if r.status_code in (403, 429, 500, 502, 503, 504):
                servidor = r.headers.get("Server", "desconocido")
                raise requests.HTTPError(f"HTTP {r.status_code} (servidor: {servidor}; "
                                         f"posible bloqueo a las máquinas de GitHub)", response=r)
            return r
        except requests.RequestException as e:
            ultimo_error = e
            if i < intentos - 1:
                time.sleep(10 * 2 ** i)
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


# ---------------------------------------------------------------- acceso por HTTP o con navegador real
class AccesoHTTP:
    """Acceso normal con requests (REE y e-distribución)."""

    def __init__(self, s: requests.Session):
        self.s = s

    def pagina(self, url: str) -> tuple[str, str]:
        r = pedir(self.s, url)
        r.raise_for_status()
        return r.text, r.url

    def firma(self, url: str) -> str:
        return firma(self.s, url)

    def descargar(self, url: str) -> bytes:
        return descargar(self.s, url)


_JS_DESCARGA = """async (url) => {
  const r = await fetch(url, {credentials: 'include'});
  if (!r.ok) return {estado: r.status};
  const b = new Uint8Array(await r.arrayBuffer());
  let s = '';
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return {estado: 200, tipo: r.headers.get('content-type') || '', datos: btoa(s)};
}"""

_JS_FIRMA = """async (url) => {
  try {
    const r = await fetch(url, {method: 'HEAD', credentials: 'include'});
    if (!r.ok) return '';
    return ['etag', 'last-modified', 'content-length'].map(h => r.headers.get(h) || '').join('|');
  } catch (e) { return ''; }
}"""


class AccesoNavegador:
    """Chromium real sin pantalla, para webs que bloquean las peticiones que no vienen de un navegador (i-DE).

    La página se abre como lo haría una persona y los ficheros se descargan con fetch() desde dentro
    de esa misma página, de modo que llevan la huella y las cookies del navegador."""

    _pw = _navegador = None

    def __init__(self, espera_s: int = 8):
        self.espera_s = espera_s
        self.pagina_abierta = None

    @classmethod
    def _lanzar(cls):
        if cls._navegador is None:
            from playwright.sync_api import sync_playwright

            cls._pw = sync_playwright().start()
            argumentos = ["--disable-blink-features=AutomationControlled", "--lang=es-ES"]
            try:  # modo headless "nuevo", más difícil de distinguir de un Chrome normal
                cls._navegador = cls._pw.chromium.launch(channel="chromium", args=argumentos)
            except Exception:
                cls._navegador = cls._pw.chromium.launch(args=argumentos)
        return cls._navegador

    @classmethod
    def cerrar(cls):
        if cls._navegador is not None:
            cls._navegador.close()
            cls._pw.stop()
            cls._navegador = cls._pw = None

    def pagina(self, url: str) -> tuple[str, str]:
        contexto = self._lanzar().new_context(user_agent=AGENTE, locale="es-ES", timezone_id="Europe/Madrid",
                                              viewport={"width": 1366, "height": 900},
                                              extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"})
        contexto.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        pg = contexto.new_page()
        estado = None
        for intento in range(3):
            respuesta = pg.goto(url, wait_until="domcontentloaded", timeout=90_000)
            try:
                pg.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            estado = respuesta.status if respuesta else None
            if estado and estado < 400:
                break
            pg.wait_for_timeout(self.espera_s * 1000 * (intento + 1))  # algunos filtros validan y recargan
        if not estado or estado >= 400:
            titulo = pg.title()
            contexto.close()
            raise requests.HTTPError(f"HTTP {estado} también con navegador real (título: '{titulo[:80]}'); "
                                     f"el bloqueo parece por dirección de origen")
        self.pagina_abierta = pg
        return pg.content(), pg.url

    def firma(self, url: str) -> str:
        return self.pagina_abierta.evaluate(_JS_FIRMA, url) if self.pagina_abierta else ""

    def descargar(self, url: str) -> bytes:
        import base64

        if self.pagina_abierta is None:
            raise RuntimeError("Hay que abrir la página antes de descargar")
        r = self.pagina_abierta.evaluate(_JS_DESCARGA, url)
        if r.get("estado") != 200:
            raise requests.HTTPError(f"HTTP {r.get('estado')} al descargar con navegador: {url}")
        contenido = base64.b64decode(r["datos"])
        if contenido[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
            raise ValueError(f"La URL devolvió una página web en lugar de un fichero: {url}")
        return contenido


def acceso(fuente: dict, s: requests.Session):
    return AccesoNavegador() if fuente.get("navegador") else AccesoHTTP(s)
