"""Comparación de dos publicaciones de capacidad de la misma fuente."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from .tabla import a_numero, buscar_columnas, detectar_decimal, limpiar, primera_columna, seleccionar_metricas

CATEGORIAS = {
    "afloramiento": "Afloramientos (0 → >0 MW)",
    "incremento": "Incrementos",
    "autoconsumo_afloramiento": "Autoconsumo RDL 7/2026: afloramientos",
    "autoconsumo_incremento": "Autoconsumo RDL 7/2026: incrementos",
    "reserva": "Reservas o concursos nuevos",
    "nudo_nuevo": "Nudos nuevos con capacidad",
}


@dataclass
class Hallazgo:
    categoria: str
    clave: str
    nudo: str
    metrica: str
    antes: float | None = None
    ahora: float | None = None
    delta: float | None = None
    detalle: str = ""
    contexto: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Resultado:
    hallazgos: list[Hallazgo]
    avisos: list[str]
    nudos_anterior: int
    nudos_actual: int

    def contadores(self) -> dict:
        cuenta = {c: 0 for c in CATEGORIAS}
        for h in self.hallazgos:
            cuenta[h.categoria] += 1
        return cuenta


class FormatoDesconocido(Exception):
    """El fichero no tiene las columnas esperadas (probable cambio de formato del publicador)."""


def _columnas(df: pd.DataFrame, exprs) -> list[str] | None:
    """Una o varias columnas (la clave puede ser compuesta, p. ej. subestación + tensión)."""
    exprs = [exprs] if isinstance(exprs, str) else list(exprs or [])
    cols = [primera_columna(df, e) for e in exprs]
    return cols if cols and all(cols) else None


def _indexar(df: pd.DataFrame, cols_clave: list[str]) -> dict[str, pd.Series]:
    indice, repetidas = {}, {}
    for _, fila in df.iterrows():
        clave = " | ".join(limpiar(fila[c]) for c in cols_clave).strip(" |")
        if not clave:
            continue
        repetidas[clave] = repetidas.get(clave, 0) + 1
        if repetidas[clave] > 1:
            clave = f"{clave} ({repetidas[clave]})"
        indice[clave] = fila
    return indice


def _modo(df: pd.DataFrame, columnas: list[str]) -> str:
    celdas = [v for c in columnas for v in df[c].tolist()]
    return detectar_decimal(celdas)


def comparar(df_ant: pd.DataFrame, df_act: pd.DataFrame, fuente: dict, umbral: float = 10.0,
             excluir_afloramientos: bool = True, vacio_como_cero: bool = True) -> Resultado:
    avisos: list[str] = []
    col_clave = _columnas(df_act, fuente["clave"])
    col_clave_ant = _columnas(df_ant, fuente["clave"])
    if not col_clave or not col_clave_ant:
        raise FormatoDesconocido(f"No encuentro la columna identificadora ({fuente['clave']}). "
                                 f"Columnas del fichero: {', '.join(list(df_act.columns)[:25])}")

    metricas = seleccionar_metricas(df_act, fuente.get("metricas", []))
    if not metricas:
        raise FormatoDesconocido("No encuentro ninguna columna de capacidad disponible en el fichero nuevo.")
    metricas_ant = {col for col, _, _ in seleccionar_metricas(df_ant, fuente.get("metricas", []))}
    for col, etiqueta, _ in metricas:
        if col not in metricas_ant:
            avisos.append(f"Columna nueva este mes (no se compara): {etiqueta}")

    cols_nombre = _columnas(df_act, fuente.get("columna_nombre"))
    cols_contexto = []
    for expr in fuente.get("contexto", []):
        cols_contexto += [c for c in buscar_columnas(df_act, expr) if c not in cols_contexto]
    cols_reserva = [c for expr in fuente.get("reserva", []) for c in buscar_columnas(df_act, expr)]

    modo_act = _modo(df_act, [c for c, _, _ in metricas])
    modo_ant = _modo(df_ant, [c for c in metricas_ant]) if metricas_ant else modo_act
    ant, act = _indexar(df_ant, col_clave_ant), _indexar(df_act, col_clave)

    def nombre_de(fila, clave):
        if not cols_nombre:
            return clave
        partes = [limpiar(fila[c]) + (" kV" if "tension" in c.lower().replace("ó", "o") else "") for c in cols_nombre
                  if limpiar(fila[c])]
        return " ".join(partes) or clave

    def contexto_de(fila):
        return {re.sub(r" #\d+$", "", c.split(" | ")[-1]): limpiar(fila[c]) for c in cols_contexto if limpiar(fila[c])}

    hallazgos: list[Hallazgo] = []
    for clave, fila in act.items():
        previa = ant.get(clave)
        nudo, ctx = nombre_de(fila, clave), contexto_de(fila)

        for col, etiqueta, categoria in metricas:
            ahora = a_numero(fila[col], modo_act)
            if ahora is None:
                continue
            if previa is None:
                if ahora > 0 and categoria == "general":
                    hallazgos.append(Hallazgo("nudo_nuevo", clave, nudo, etiqueta, None, ahora, ahora, contexto=ctx))
                continue
            if col not in metricas_ant:
                continue
            antes = a_numero(previa[col], modo_ant)
            if antes is None:
                if not vacio_como_cero:
                    continue
                antes = 0.0
            delta = round(ahora - antes, 3)
            prefijo = "autoconsumo_" if categoria == "autoconsumo" else ""
            if antes == 0 and ahora > 0:
                hallazgos.append(Hallazgo(prefijo + "afloramiento", clave, nudo, etiqueta, antes, ahora, delta, contexto=ctx))
                if excluir_afloramientos:
                    continue
            if delta >= umbral:
                hallazgos.append(Hallazgo(prefijo + "incremento", clave, nudo, etiqueta, antes, ahora, delta, contexto=ctx))

        for col in cols_reserva:
            valor = limpiar(fila[col])
            if not valor:
                continue
            valor_prev = limpiar(previa[col]) if previa is not None and col in previa.index else ""
            if valor != valor_prev:
                detalle = "Nudo nuevo en la publicación" if previa is None else (
                    f"Antes: {valor_prev}" if valor_prev else "Antes: sin reserva")
                hallazgos.append(Hallazgo("reserva", clave, nudo, col.split(" | ")[0], detalle=f"{valor}. {detalle}",
                                          contexto=ctx))

    orden = list(CATEGORIAS)
    hallazgos.sort(key=lambda h: (orden.index(h.categoria), -(h.delta or 0), h.nudo))
    return Resultado(hallazgos, avisos, len(ant), len(act))
