# Vigilancia de capacidades de acceso

Vigila las seis publicaciones mensuales de capacidad de acceso (REE, i-DE y e-distribución, generación y demanda). Cuando detecta un fichero nuevo lo compara con la publicación anterior y avisa por Telegram, correo o Teams de:

- **Afloramientos**: nudos o puntos de conexión que tenían 0 MW disponibles y ahora tienen más de 0 MW.
- **Incrementos**: subidas de capacidad disponible de 10 MW o más (umbral configurable).
- **Autoconsumo RDL 7/2026 (REE generación)**: afloramientos e incrementos en las seis columnas de capacidad liberada, otorgada y disponible para autoconsumo (MGES y MPE).
- **Reservas o concursos nuevos (REE)**: cambios en *Motivo de reserva de capacidad de acceso en el nudo* (generación; p. ej. `Concurso`, `NTJ`) y en *Nudo de concurso* (demanda).
- **Nudos nuevos** que aparecen ya con capacidad.

Cada publicación genera un Excel con todo el detalle y se actualiza un panel web público.

## Cómo funciona

Funciona gratis sobre GitHub. **GitHub Actions** ejecuta el programa cada 15 minutos entre los días 1 y 10 de cada mes (de 6:00 a 24:00) y cada 3 horas el resto del mes. En cada pasada lee las seis páginas, localiza el fichero vigente y solo lo descarga si ha cambiado. Los ficheros se guardan comprimidos en `data/` para comparar con el mes siguiente, y el panel se publica con **GitHub Pages** desde `docs/`.

| Fuente | Cómo se localiza el fichero |
|---|---|
| REE generación y demanda | Enlace `AAAA_MM_DD_GRT_generacion.csv` / `…_demanda.csv`. REE solo enlaza el vigente; en el primer arranque el anterior se deduce cambiando la fecha de la URL |
| i-DE generación y demanda | Enlaces `AAAA_MM_DD_r1-001_…` sin extensión: el formato (CSV/XLSX) se reconoce por el icono. Se guarda también la hora de publicación que muestra la página |
| e-distribución generación y demanda | Enlaces `AAAA_MM_DD_R1299_generación.csv` / `…demanda.csv` (se descartan EASA y Ceuta) |

La comparación se hace nudo a nudo: en REE por *Nombre y tensión del nudo*, en las distribuidoras por *Identificador del Punto de Conexión* (formato normalizado de la CNMC).

## Puesta en marcha (unos 20 minutos)

1. **Crea una cuenta en [github.com](https://github.com)** si no la tienes.
2. **Crea un repositorio nuevo**: botón *New*, nombre `vigilancia-capacidades`, visibilidad **Public** (GitHub Pages gratis exige repositorio público; los datos son públicos y las claves van en secretos, que nadie puede ver).
3. **Sube los ficheros**: en el repositorio vacío, *uploading an existing file*, arrastra **todo el contenido** de la carpeta descomprimida (incluida la carpeta `.github`) y pulsa *Commit changes*. Si tu sistema oculta la carpeta `.github`, créala a mano: *Add file → Create new file*, escribe `.github/workflows/vigilancia.yml` como nombre y pega su contenido.
4. **Permite que el programa guarde resultados**: *Settings → Actions → General → Workflow permissions →* **Read and write permissions** → *Save*.
5. **Activa el panel web**: *Settings → Pages → Build and deployment → Deploy from a branch →* rama `main`, carpeta `/docs` → *Save*. Al cabo de un minuto verás la dirección, del tipo `https://TU_USUARIO.github.io/vigilancia-capacidades/`.
6. **Guarda esa dirección** para que los avisos enlacen al Excel: *Settings → Secrets and variables → Actions →* pestaña **Variables** → *New repository variable*, nombre `PANEL_URL`, valor la dirección del paso anterior.
7. **Configura al menos un canal de aviso** (ver abajo) en *Settings → Secrets and variables → Actions →* pestaña **Secrets**.
8. **Primera ejecución**: pestaña *Actions → Vigilancia de capacidades → Run workflow*. Compara la publicación vigente de cada fuente con la anterior y te manda un primer aviso marcado como *arranque inicial* (marca *No enviar avisos* si no lo quieres). A partir de ahí funciona solo.

## Avisos

**Telegram (recomendado: llega al móvil al momento)**

1. En Telegram, habla con `@BotFather`, envía `/newbot` y sigue los pasos. Te dará un *token*.
2. Escribe cualquier mensaje a tu bot nuevo. Luego abre `https://api.telegram.org/botTU_TOKEN/getUpdates` en el navegador y copia el número de `"chat":{"id": …}`.
3. Crea los secretos `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`. Para avisar a varias personas o a un grupo, separa los identificadores con comas (en grupos el identificador empieza por `-`).

**Correo** (con el Excel adjunto): secretos `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` y `EMAIL_TO` (varios destinatarios separados por comas). Con Gmail: `smtp.gmail.com`, puerto `587` y una *contraseña de aplicación* (Cuenta de Google → Seguridad → Verificación en dos pasos → Contraseñas de aplicaciones). Los buzones corporativos de Microsoft 365 suelen tener el envío SMTP desactivado; en ese caso usa Teams.

**Microsoft Teams**: en el canal, *… → Workflows →* plantilla *Publicar en un canal cuando se reciba una solicitud de webhook*. Copia la URL que genera en el secreto `TEAMS_WEBHOOK_URL`.

Para comprobar los avisos sin esperar a una publicación, ejecuta el programa en local con `python -m capmon probar-avisos` (con las mismas variables de entorno).

## Ajustes

Todo está en `config.yaml`: el umbral de incremento (`umbral_incremento_mw: 10`), si un afloramiento se repite también como incremento, cuántas líneas lleva cada aviso y, para cada fuente, la página, el patrón del nombre de fichero y las columnas vigiladas (expresiones que ignoran mayúsculas y acentos). La frecuencia de revisión está en `.github/workflows/vigilancia.yml` (horas en UTC).

## Uso en local

```bash
pip install -r requirements.txt
python -m capmon inspeccionar ree_generacion     # enlaces encontrados y columnas del fichero vigente
python -m capmon comparar --fuente ide_generacion agosto.csv septiembre.csv --salida informe.xlsx
python -m capmon vigilar --sin-avisos            # una pasada completa
python -m pytest                                 # pruebas
```

`comparar` sirve para contrastar dos ficheros descargados a mano, sin tocar el repositorio.

## Qué tener en cuenta

- **Precisión del aviso.** Con revisiones cada 15 minutos el aviso llega, como mucho, unos 15 minutos después de la publicación; en horas de mucha carga GitHub puede retrasar las ejecuciones programadas unos minutos más. Se puede bajar a cada 5 minutos en el workflow.
- **Formatos verificados.** Los ficheros de generación de las tres compañías y el de demanda de REE se han probado con sus cabeceras reales. Los de demanda de i-DE y e-distribución siguen el mismo formato normalizado de la CNMC, pero conviene confirmar el patrón del nombre del fichero con `python -m capmon inspeccionar ide_demanda` (y `edistribucion_demanda`) antes del primer mes.
- **Cambios de formato.** Si una compañía cambia la web o el fichero y el programa no encuentra lo que espera, avisa con un mensaje de error (una sola vez, no en cada pasada) y la fuente aparece en rojo en el panel. Se corrige ajustando `config.yaml`.
- **Capacidades no sumables.** Como advierten las propias distribuidoras, las capacidades de los distintos nudos de una subestación no se suman; el programa compara cada nudo por separado.
- **Inactividad.** GitHub desactiva las tareas programadas de repositorios sin actividad durante 60 días. Cada publicación nueva genera un *commit*, así que en uso normal no ocurre; si pasara, basta con reactivarlas en la pestaña *Actions*.
- Los valores publicados son orientativos; prevalece la publicación oficial y el estudio específico de cada solicitud.
