"""Avisos por Telegram, correo y Teams. Cada canal se activa solo si están sus secretos."""
from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import requests

from .comparar import CATEGORIAS, Resultado
from .informes import fmt_fecha


def _mw(v) -> str:
    return "—" if v is None else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").rstrip("0").rstrip(",")


def componer(fuente: dict, actual, anterior, resultado: Resultado, url_informe: str, maximo: int = 15,
             arranque: bool = False) -> tuple[str, str]:
    """Devuelve (asunto, cuerpo HTML sencillo compatible con Telegram, correo y Teams)."""
    cuenta = resultado.contadores()
    total = sum(cuenta.values())
    asunto = f"{fuente['nombre']}: nueva publicación del {fmt_fecha(actual.fecha)}"
    if arranque:
        asunto += " (arranque inicial)"
    lineas = [f"<b>{html.escape(asunto)}</b>"]
    lineas.append(f"Comparada con la del {fmt_fecha(anterior.fecha)}." if anterior else "Sin publicación anterior con la que comparar.")
    if total == 0:
        lineas.append("Sin afloramientos, incrementos ni reservas nuevas.")
    for categoria, titulo in CATEGORIAS.items():
        items = [h for h in resultado.hallazgos if h.categoria == categoria]
        if not items:
            continue
        lineas.append(f"\n<b>{html.escape(titulo)}: {len(items)}</b>")
        for h in items[:maximo]:
            if categoria == "reserva":
                lineas.append(f"• {html.escape(h.nudo)}: {html.escape(h.detalle)}")
            else:
                extra = h.contexto.get("Nudo Afección RdT") or h.contexto.get("Provincia") or h.contexto.get("Comunidad Autónoma") or ""
                extra = f" ({html.escape(extra)})" if extra else ""
                lineas.append(f"• {html.escape(h.nudo)}{extra}, {html.escape(h.metrica)}: "
                              f"{_mw(h.antes)} → {_mw(h.ahora)} MW (+{_mw(h.delta)})")
        if len(items) > maximo:
            lineas.append(f"… y {len(items) - maximo} más en el informe.")
    for aviso in resultado.avisos:
        lineas.append(f"\n⚠️ {html.escape(aviso)}")
    if url_informe:
        lineas.append(f'\n<a href="{html.escape(url_informe)}">Informe completo en Excel</a>')
    lineas.append(f'<a href="{html.escape(actual.url)}">Fichero publicado</a>')
    return asunto, "\n".join(lineas)


# ---------------------------------------------------------------- canales
def _telegram(cuerpo: str) -> None:
    token, chats = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chats:
        return
    texto = cuerpo if len(cuerpo) < 4000 else cuerpo[:3900] + "\n… (mensaje recortado, ver informe)"
    for chat in [c.strip() for c in chats.split(",") if c.strip()]:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, json={
            "chat_id": chat, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True})
        r.raise_for_status()


def _correo(asunto: str, cuerpo: str, adjunto: Path | None) -> None:
    host, destino = os.environ.get("SMTP_HOST"), os.environ.get("EMAIL_TO")
    if not host or not destino:
        return
    puerto = int(os.environ.get("SMTP_PORT", "587"))
    usuario, clave = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = asunto, os.environ.get("EMAIL_FROM", usuario), destino
    msg.set_content("Hay una nueva publicación de capacidad de acceso. Abre este correo en formato HTML.")
    msg.add_alternative(f"<div style='font-family:Arial,sans-serif;font-size:14px'>{cuerpo.replace(chr(10), '<br>')}</div>",
                        subtype="html")
    if adjunto and adjunto.exists():
        msg.add_attachment(adjunto.read_bytes(), maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=adjunto.name)
    contexto = ssl.create_default_context()
    if puerto == 465:
        with smtplib.SMTP_SSL(host, puerto, context=contexto, timeout=60) as s:
            s.login(usuario, clave)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, puerto, timeout=60) as s:
            s.starttls(context=contexto)
            if usuario:
                s.login(usuario, clave)
            s.send_message(msg)


def _teams(asunto: str, cuerpo: str) -> None:
    url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        return
    import re
    texto = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"[\2](\1)", cuerpo)
    texto = re.sub(r"</?b>", "**", texto)
    texto = html.unescape(re.sub(r"<[^>]+>", "", texto))
    tarjeta = {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                    "version": "1.4", "body": [{"type": "TextBlock", "text": texto, "wrap": True}]}}]}
    requests.post(url, json=tarjeta, timeout=30).raise_for_status()


def enviar(asunto: str, cuerpo: str, adjunto: Path | None = None) -> list[str]:
    """Envía por todos los canales configurados. Devuelve la lista de errores (sin detener la ejecución)."""
    errores = []
    for nombre, funcion in (("Telegram", lambda: _telegram(cuerpo)), ("correo", lambda: _correo(asunto, cuerpo, adjunto)),
                            ("Teams", lambda: _teams(asunto, cuerpo))):
        try:
            funcion()
        except Exception as e:  # un canal caído no debe tumbar a los demás
            errores.append(f"{nombre}: {e}")
    return errores


def canales_configurados() -> list[str]:
    activos = []
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        activos.append("Telegram")
    if os.environ.get("SMTP_HOST") and os.environ.get("EMAIL_TO"):
        activos.append("correo")
    if os.environ.get("TEAMS_WEBHOOK_URL"):
        activos.append("Teams")
    return activos
