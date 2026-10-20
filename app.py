"""Estudio JCF | Consultas ITC y presupuestos VEP/CDA.

Versión revisada: no guarda credenciales en el código y no afirma obtener
planos/VEP vigentes hasta disponer de una integración documentada y probada.
"""

from datetime import datetime
from PIL import Image, ImageSequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from io import BytesIO
import re
import json
import unicodedata
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


ITC_BASE = "https://catastro.neuquen.gov.ar"
ITC_PORTAL = f"{ITC_BASE}/itc"
VISOR_BASE = (
    f"{ITC_BASE}/portal/apps/webappviewer/index.html"
    "?id=63f0c3f9a047425280951f418621171f"
)
TZ_NEUQUEN = ZoneInfo("America/Argentina/Salta")
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre"
)
TIPOS_PARCELA = ["Sin determinar", "Urbana", "RUI", "ROE", "RUE", "Otra"]
CLASES_RURALES = {"RUI", "ROE"}  # La regla 50/50 solicitada aplica a estas dos categorías.


# --- Consulta ITC ---

def limpiar_nomenclatura(valor):
    return re.sub(r"[\s-]", "", valor.strip())


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c)).upper()


def detectar_clasificacion(parcela):
    """Solo usa campos que parezcan describir clasificación; evita falsos positivos."""
    pistas = ("TIPO", "CLASE", "CLASIF", "ZONA", "AREA", "AMBITO", "RURAL", "CATEGORIA")
    valores = []
    for clave, valor in parcela.items():
        if any(pista in normalizar(clave) for pista in pistas) and valor is not None:
            valores.append(normalizar(valor))
    texto = " ".join(valores)
    for sigla in ("RUI", "ROE", "RUE"):
        if re.search(rf"\b{sigla}\b", texto):
            return sigla
    if "RURAL INTENSIVA" in texto:
        return "RUI"
    if "RURAL EXTENSIVA" in texto:
        return "ROE"  # Etiqueta tentativa; confirmar la codificación real del ITC.
    if re.search(r"\bURBANA?\b", texto):
        return "Urbana"
    return "Sin determinar"


def centro_aproximado(rings):
    """Centroide del anillo exterior; es orientación aproximada, no mensura."""
    if not rings or len(rings[0]) < 3:
        return None
    puntos = rings[0]
    if puntos[0] == puntos[-1]:
        puntos = puntos[:-1]
    if len(puntos) < 3:
        return None
    doble_area = 0.0
    sx = sy = 0.0
    for i, (x1, y1) in enumerate(puntos):
        x2, y2 = puntos[(i + 1) % len(puntos)]
        cruz = x1 * y2 - x2 * y1
        doble_area += cruz
        sx += (x1 + x2) * cruz
        sy += (y1 + y2) * cruz
    if abs(doble_area) < 1e-12:
        x = sum(p[0] for p in puntos) / len(puntos)
        y = sum(p[1] for p in puntos) / len(puntos)
    else:
        x = sx / (3 * doble_area)
        y = sy / (3 * doble_area)
    if not (-180 <= x <= 180 and -90 <= y <= 90):
        return None
    return y, x  # latitud, longitud


# Servicios identificados mediante análisis ESTÁTICO del ejecutable facilitado.
# Son servicios de Catastro Neuquén, no un portal o visor externo.
PARCELA_COMPLETA_URL = (
    f"{ITC_BASE}/site/ITC.Services/Json/ParcelaServiceJson.svc/json/"
    "GetCompletaByNomenclaturaList"
)
PARCELA_SIMPLE_URL = (
    f"{ITC_BASE}/site/ITC.Services/Json/ParcelaServiceJson.svc/json/"
    "GetByNomenclatura"
)
DOCUMENTOS_URL = (
    f"{ITC_BASE}/site/ITC.Services/Json/GestorDocumentoServiceJson.svc/json/"
    "GetByNomenclaturaAndExpediente"
)
DESCARGA_URL = f"{ITC_BASE}/site/ITC.Services/Services/FileDownload.aspx"


def _json_catastral(respuesta):
    respuesta.raise_for_status()
    dato = respuesta.json()
    # Los servicios WCF pueden encapsular la lista, en algunas instalaciones, en "d".
    if isinstance(dato, dict) and "d" in dato:
        dato = dato["d"]
    if isinstance(dato, str):
        dato = json.loads(dato)
    return dato


def _lista_resultado(dato, clave):
    if isinstance(dato, dict):
        dato = dato.get(clave, dato.get("Result", []))
    if isinstance(dato, str):
        dato = json.loads(dato)
    if isinstance(dato, dict):
        # Algunas respuestas dan un solo objeto, no una lista.
        dato = [dato]
    return [x for x in dato if isinstance(x, dict)] if isinstance(dato, list) else []


def consultar_itc(nomenclatura, usuario="", contrasena=""):
    """Consulta completa + documentos sin abrir la web de Catastro.

    La aplicación de escritorio proporcionada utiliza llamadas GET y añade el
    usuario como parámetro. Si los servicios exigen sesión en el entorno real,
    los errores se mostrarán sin declarar que se obtuvieron documentos.
    """
    numero = limpiar_nomenclatura(nomenclatura)
    if not re.fullmatch(r"\d{15}", numero):
        return None, "Ingresá una nomenclatura catastral de 15 dígitos."

    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "user-agent": "EstudioJCF-Catastro/2.0",
    }
    with requests.Session() as sesion:
        sesion.headers.update(headers)
        # Autenticación opcional, para instalaciones ITC que exigen una sesión.
        if usuario and contrasena:
            try:
                login = sesion.post(
                    f"{ITC_BASE}/site/ITC.Services/Json/SecurityServiceJson.svc/json/PostAuthenticate",
                    json={"user": usuario, "pass": contrasena}, timeout=(7, 15),
                )
                login.raise_for_status()
            except requests.RequestException:
                # La aplicación de escritorio consulta por GET sin esa llamada.
                # No impedir una consulta de acceso público por un fallo del login.
                pass

        ultima_excepcion = None
        parcelas = []
        for url, parametros, clave in (
            (PARCELA_COMPLETA_URL, {"nomen": numero, "id": "1"}, "GetCompletaByNomenclaturaListResult"),
            (PARCELA_COMPLETA_URL, {"nomen": numero, "id": "0"}, "GetCompletaByNomenclaturaListResult"),
            (PARCELA_SIMPLE_URL, {"nomen": numero, "ph": ""}, "GetByNomenclaturaResult"),
        ):
            try:
                respuesta = sesion.get(url, params=parametros, timeout=(7, 20))
                parcelas = _lista_resultado(_json_catastral(respuesta), clave)
                if parcelas:
                    break
            except requests.exceptions.ConnectTimeout:
                # Si no hay conexión al servidor, insistir contra el mismo
                # dominio tres veces solo aumenta la espera del usuario.
                return None, (
                    "No se pudo conectar con el servidor de Catastro desde Streamlit "
                    "(ConnectTimeout). La app está funcionando, pero el ITC no respondió. "
                    "Probá nuevamente más tarde. Si el programa de Windows funciona "
                    "y esta página sigue fallando, puede haber una restricción de red "
                    "para las conexiones desde Streamlit Cloud."
                )
            except (requests.RequestException, ValueError, TypeError) as exc:
                ultima_excepcion = exc
        if not parcelas:
            detalle = "El servicio no devolvió información parcelaria."
            if ultima_excepcion:
                detalle += f" Error HTTP o de respuesta: {type(ultima_excepcion).__name__}."
            return None, detalle

        coincidencias = [
            q for q in parcelas
            if limpiar_nomenclatura(str(q.get("Nomenclatura", ""))) == numero
        ]
        if not coincidencias:
            return None, "La consulta respondió, pero no identificó la nomenclatura exacta."
        activos = [q for q in coincidencias if str(q.get("Estado", "")).upper() == "A"]
        parcela = activos[0] if activos else coincidencias[0]
        nomen_efectiva = limpiar_nomenclatura(str(parcela.get("Nomenclatura") or numero))
        tramite_id = parcela.get("TramiteId") or 0
        try:
            tramite_id = int(tramite_id)
        except (TypeError, ValueError):
            tramite_id = 0

        documentos = []
        error_documentos = ""
        # Según el ejecutable, el servicio admite 'usuario' y 'username'.
        for campo_usuario in ("usuario", "username"):
            try:
                respuesta = sesion.get(
                    DOCUMENTOS_URL,
                    params={"nomen": nomen_efectiva, "idtramite": tramite_id, campo_usuario: usuario},
                    timeout=(7, 25),
                )
                documentos = _lista_resultado(
                    _json_catastral(respuesta), "GetByNomenclaturaAndExpedienteResult"
                )
                error_documentos = ""
                if documentos:
                    break
            except requests.exceptions.ConnectTimeout:
                error_documentos = "El Gestor de Documentos de Catastro no respondió (ConnectTimeout)."
                break
            except (requests.RequestException, ValueError, TypeError) as exc:
                error_documentos = f"No se pudo consultar documentos ({type(exc).__name__})."

        latlon = None
        object_id = parcela.get("ObjectId")
        service_id = parcela.get("ServiceId") or 7
        if str(object_id).isdigit() and str(service_id).isdigit():
            try:
                geo = sesion.get(
                    f"{ITC_BASE}/site/ITC.Services/proxy.ashx?"
                    f"{ITC_BASE}/server/rest/services/EditFeatures/FeatureServer/{service_id}/query?"
                    f"f=json&where=ObjectId%20%3D%20{object_id}"
                    "&returnGeometry=true&outSR=4326&outFields=*", timeout=(7, 12),
                )
                geo.raise_for_status()
                features = geo.json().get("features", [])
                if features:
                    latlon = centro_aproximado(features[0].get("geometry", {}).get("rings", []))
            except (requests.RequestException, ValueError, TypeError, KeyError):
                pass

    return {
        "version_datos": 3,
        "nomenclatura": nomen_efectiva,
        "ubicacion": str(parcela.get("UbicacionLoteo") or ""),
        "clasificacion_detectada": detectar_clasificacion(parcela),
        "estado": str(parcela.get("Estado") or "No informado"),
        "google_maps_url": (
            f"https://www.google.com/maps/search/?api=1&query={latlon[0]:.7f}%2C{latlon[1]:.7f}"
            if latlon else ""
        ),
        "documentos": documentos,
        "documentos_error": error_documentos,
        "usuario_itc": usuario,
        "tramite_id": tramite_id,
    }, None


def id_archivo(doc):
    """El ID exacto del adjunto; de no estar presente, no inventar descarga."""
    for clave in ("IDArchivo", "ArchivoId", "ArchivoID", "IdArchivo"):
        valor = doc.get(clave)
        if valor is not None and str(valor).strip().isdigit() and int(valor) > 0:
            return int(valor)
    # El programa compartido usa como alternativa CodigoDigitalCompleto.
    codigo = str(doc.get("CodigoDigitalCompleto") or "").strip()
    fragmento = codigo.rsplit("-", 1)[-1]
    if codigo and fragmento.isdigit() and int(fragmento) > 0:
        return int(fragmento)
    return None


def texto_documento(doc):
    return " ".join(str(doc.get(c) or "") for c in (
        "TipoDocumento", "Descripcion", "Nombre", "Documento",
    )).upper()


def es_vep(doc):
    t = normalizar(texto_documento(doc))
    return bool(re.search(r"\bVEP\b|VERIFICACION.*ESTADO PARCELARIO", t))


def es_plano_mensura(doc):
    t = normalizar(texto_documento(doc))
    return bool(re.search(r"MENSURA|\bPLANO\b", t))


def vigencia_documento(doc):
    codigo = str(doc.get("Vigencia") or "").upper().strip()
    if codigo == "S":
        return "Vigente (según documento)"
    if codigo == "N":
        return "No vigente (según documento)"
    return "Sin dato de vigencia"


def fecha_documento(doc):
    """Conserva la fecha original y la ordena sin asumir la vigencia."""
    fecha = doc.get("Fecha") or doc.get("FechaAlta") or ""
    if isinstance(fecha, (int, float)):
        return datetime.fromtimestamp(fecha / 1000 if fecha > 1e11 else fecha, TZ_NEUQUEN).strftime("%d/%m/%Y")
    texto = str(fecha).strip()
    m = re.search(r"/Date\((-?\d+)", texto)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)) / 1000, TZ_NEUQUEN).strftime("%d/%m/%Y")
        except (OverflowError, OSError, ValueError):
            pass
    return texto[:24] if texto else "Sin fecha"


def fecha_orden(doc):
    valor = fecha_documento(doc)
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(valor[:10], formato)
        except ValueError:
            pass
    return datetime.min


def documento_nombre(doc):
    return str(doc.get("Nombre") or doc.get("Descripcion") or doc.get("TipoDocumento") or "Documento")


def descargar_documento(doc, usuario=""):
    """Descarga únicamente documentos de Catastro; devuelve PDF o imagen convertida a PDF."""
    archivo_id = id_archivo(doc)
    if not archivo_id:
        raise ValueError("Este documento no incluye un identificador de descarga verificable.")
    respuesta = requests.get(
        DESCARGA_URL,
        params={"IDTipoArchivo": "0", "IDArchivo": str(archivo_id), "username": usuario},
        headers={"User-Agent": "EstudioJCF-Catastro/2.0"},
        timeout=(8, 40),
        stream=True,
    )
    try:
        respuesta.raise_for_status()
        max_bytes = 35 * 1024 * 1024
        buf = BytesIO()
        for porcion in respuesta.iter_content(chunk_size=65536):
            buf.write(porcion)
            if buf.tell() > max_bytes:
                raise ValueError("El archivo supera el límite de 35 MB por descarga.")
        contenido = buf.getvalue()
    finally:
        respuesta.close()
    if not contenido:
        raise ValueError("Catastro devolvió un archivo vacío.")
    if contenido[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("Catastro devolvió una página HTML en lugar del documento.")
    if contenido.startswith(b"%PDF-"):
        return contenido
    # Admite JPEG, PNG, TIFF recibidos desde Catastro: todos se entregan como PDF.
    try:
        with Image.open(BytesIO(contenido)) as img:
            paginas = []
            for frame in ImageSequence.Iterator(img):
                paginas.append(frame.convert("RGB"))
                if len(paginas) > 100:
                    raise ValueError("El documento tiene demasiadas páginas para convertir.")
            salida = BytesIO()
            paginas[0].save(salida, format="PDF", save_all=True, append_images=paginas[1:])
            return salida.getvalue()
    except (OSError, IndexError) as exc:
        raise ValueError("El archivo recibido no es un PDF ni una imagen admitida.") from exc


def nombre_pdf_documento(doc, nomenclatura):
    nombre = documento_nombre(doc)
    seguro = re.sub(r"[^\w\-. ]+", "_", nombre, flags=re.UNICODE).strip(" ._")[:75]
    numero = id_archivo(doc) or "sin_id"
    return f"{seguro or 'Documento'}_{nomenclatura}_{numero}.pdf"


# --- Compatibilidad con consultas anteriores guardadas por Streamlit ---

def adaptar_parcela(datos):
    """Estandariza datos antiguos y actuales sin asumir clases rurales no verificadas.

    Streamlit puede conservar session_state tras actualizar app.py. La primera
    versión guardaba `es_rui_rue`, `ubicacion_loteo` y `documento_url`; la nueva
    espera `estado`, `clasificacion_detectada`, `ubicacion` y `visor_url`.
    """
    if not isinstance(datos, dict):
        return None
    nom = limpiar_nomenclatura(str(datos.get("nomenclatura") or ""))
    if not re.fullmatch(r"\d{15}", nom):
        return None

    clase = datos.get("clasificacion_detectada") or "Sin determinar"
    if clase not in TIPOS_PARCELA:
        clase = "Sin determinar"
    # Un indicador booleano viejo como es_rui_rue NO permite distinguir ROE/RUI.
    # En esos casos forzamos confirmación manual del tipo antes de presupuestar.
    return {
        "version_datos": 3,
        "nomenclatura": nom,
        "ubicacion": str(datos.get("ubicacion") or datos.get("ubicacion_loteo") or ""),
        "clasificacion_detectada": clase,
        "estado": str(datos.get("estado") or datos.get("Estado") or "No informado"),
        "google_maps_url": str(datos.get("google_maps_url") or ""),
        "documentos": datos.get("documentos", []) if isinstance(datos.get("documentos"), list) else [],
        "documentos_error": str(datos.get("documentos_error") or ""),
        "usuario_itc": str(datos.get("usuario_itc") or ""),
        "tramite_id": datos.get("tramite_id") or 0,
    }


# --- PDF de honorarios ---

def leer_importe(texto):
    """Admite 500000, 500.000 o 500.000,50 (formato argentino)."""
    limpio = str(texto).replace("$", "").replace(" ", "").strip()
    if not limpio or not re.fullmatch(r"\d[\d.,]*", limpio):
        raise ValueError("Ingresá un importe numérico válido, por ejemplo 500.000.")
    if "," in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif "." in limpio:
        partes = limpio.split(".")
        if len(partes) > 2 or len(partes[-1]) == 3:
            limpio = limpio.replace(".", "")
    try:
        importe = Decimal(limpio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("No pude interpretar el importe.") from exc
    if importe <= 0:
        raise ValueError("El importe debe ser mayor a cero.")
    return importe


def pesos(importe):
    texto = f"{importe:,.2f}"
    entero, decimal = texto.split(".")
    entero = entero.replace(",", ".")
    return f"$ {entero}" if decimal == "00" else f"$ {entero},{decimal}"


def fecha_local():
    ahora = datetime.now(TZ_NEUQUEN)
    return ahora, f"{ahora.day} de {MESES[ahora.month - 1]} de {ahora.year}"


def generar_pdf(tipo, cliente, nomenclatura, importe, ubicacion, clasificacion, observaciones=""):
    ahora, fecha = fecha_local()
    azul = colors.HexColor("#1a365d")
    gris = colors.HexColor("#526176")
    fondo = colors.HexColor("#f1f5f9")
    estilos = getSampleStyleSheet()
    estilos.add(ParagraphStyle(name="MarcaJCF", parent=estilos["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=azul, spaceAfter=6))
    estilos.add(ParagraphStyle(name="SubJCF", parent=estilos["Normal"], fontSize=9, leading=13, textColor=gris))
    estilos.add(ParagraphStyle(name="TituloJCF", parent=estilos["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=azul, spaceBefore=14, spaceAfter=9))
    estilos.add(ParagraphStyle(name="CuerpoJCF", parent=estilos["Normal"], fontName="Helvetica", fontSize=10, leading=15, spaceAfter=7))
    estilos.add(ParagraphStyle(name="MiniJCF", parent=estilos["Normal"], fontSize=9, leading=13))
    e = lambda valor: escape(str(valor or "-"))
    def p(texto, estilo="CuerpoJCF"):
        return Paragraph(texto, estilos[estilo])

    titulo = (
        "Verificación de Estado Parcelario (VEP)"
        if tipo == "VEP" else "Certificado de Línea de Mejoramiento (CDA)"
    )
    rural = clasificacion in CLASES_RURALES
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=22*mm, rightMargin=22*mm,
        topMargin=20*mm, bottomMargin=18*mm,
        title=f"Presupuesto {tipo} - {nomenclatura}",
        author="Estudio de Agrimensura JCF",
    )
    contenido = [
        p("ESTUDIO DE AGRIMENSURA JCF", "MarcaJCF"),
        p("Ing. Agrim. Julián Cortina Fraysse | Matrícula Profesional AGR. 382 (Neuquén)", "SubJCF"),
        p("Tel.: 299-4116680 | juliancortinafraysse@gmail.com", "SubJCF"),
        Spacer(1, 16),
        p(f"Neuquén, {fecha}"),
        p(f"<b>Comitente:</b> {e(cliente)}"),
        p("PROPUESTA DE HONORARIOS PROFESIONALES", "TituloJCF"),
        p(f"<b>Trabajo:</b> {e(titulo)}"),
        p("DATOS DE LA PARCELA", "TituloJCF"),
    ]
    filas = [
        [p("Nomenclatura", "MiniJCF"), p(e(nomenclatura), "MiniJCF")],
        [p("Ubicación / Loteo", "MiniJCF"), p(e(ubicacion), "MiniJCF")],
        [p("Clasificación", "MiniJCF"), p(e(clasificacion), "MiniJCF")],
    ]
    tabla = Table(filas, colWidths=[39*mm, 122*mm], hAlign="LEFT")
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), fondo),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LINEBELOW", (0,0), (-1,-2), 0.4, colors.HexColor("#e2e8f0")),
    ]))
    contenido += [tabla, Spacer(1, 15), p("ALCANCE DE LOS TRABAJOS", "TituloJCF")]
    tareas = (
        ["Revisión de antecedentes y documentación parcelaria disponible.",
         "Relevamiento y verificación en terreno.",
         "Confección y gestión de la VEP ante la DPCeIT."]
        if tipo == "VEP" else
        ["Revisión de antecedentes catastrales y documentación disponible.",
         "Verificación de la línea de mejoramiento conforme a la información oficial aplicable.",
         "Preparación de la documentación correspondiente al certificado."]
    )
    for tarea in tareas:
        contenido.append(p("• " + e(tarea)))
    contenido += [
        p("HONORARIOS", "TituloJCF"),
        p(f"<b>Honorarios profesionales totales: {pesos(importe)}</b>"),
        p("FORMA DE PAGO", "TituloJCF"),
    ]
    if rural:
        anticipo = (importe * Decimal("0.5")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        saldo = importe - anticipo
        contenido += [
            p(f"50% al inicio de los trabajos: <b>{pesos(anticipo)}</b>."),
            p(f"50% restante al finalizar los trabajos: <b>{pesos(saldo)}</b>."),
        ]
    else:
        contenido.append(p("Pago único al finalizar los trabajos."))
    if observaciones.strip():
        contenido += [p("OBSERVACIONES", "TituloJCF"), p(e(observaciones).replace("\n", "<br/>"))]
    contenido += [Spacer(1, 24), p("Sin otro particular, saludo atentamente."), Spacer(1, 24),
                  p("<b>Ing. Agrim. Julián Cortina Fraysse</b>"), p("Matrícula Profesional AGR. 382", "MiniJCF")]
    doc.build(contenido)
    return buffer.getvalue()


# --- Interfaz Streamlit ---
st.set_page_config(page_title="Estudio JCF - Catastro Nqn", page_icon="📐", layout="wide")
st.title("📐 Estudio JCF - Catastro Nqn")


def secreto(clave):
    """Lee la configuración permanente de Streamlit Cloud (no GitHub)."""
    try:
        return str(st.secrets.get(clave, "") or "").strip()
    except (FileNotFoundError, OSError, KeyError):
        return ""


# Se carga UNA sola vez desde la configuración privada de Streamlit.
# No hay campo para escribir el usuario en cada consulta.
USUARIO_ITC = secreto("ITC_USER")
CONTRASENA_ITC = secreto("ITC_PASS")

st.caption("Planos y documentos de Catastro dentro de la aplicación | Presupuestos VEP / CDA")


def limpiar_consulta():
    for clave in ("parcela", "ubicacion_editable", "clasificacion_editable", "pdf_documento", "pdf_archivo_nombre"):
        st.session_state.pop(clave, None)


nomen_input = st.text_input(
    "Nomenclatura catastral (15 dígitos)", key="nomen_input",
    placeholder="Ej.: 092007824920000", on_change=limpiar_consulta,
)
if USUARIO_ITC:
    st.caption("🔒 Usuario de ITC guardado en la configuración privada de Streamlit; no hace falta volver a ingresarlo.")
else:
    st.info(
        "Para que el usuario de ITC quede guardado de manera permanente, configurá "
        "`ITC_USER` una sola vez en **Streamlit Cloud → Manage app → Settings → Secrets**. "
        "Mientras tanto, podés intentar la consulta sin usuario, pero ciertas descargas podrían no estar disponibles."
    )

if st.button("🔎 Consultar parcela y documentos", type="primary"):
    with st.spinner("Consultando datos y documentos del ITC..."):
        try:
            datos, error = consultar_itc(nomen_input, USUARIO_ITC, CONTRASENA_ITC)
        except Exception as exc:
            datos, error = None, f"Error inesperado al consultar: {type(exc).__name__}."
    limpiar_consulta()
    if error:
        st.error(error)
    else:
        st.session_state["parcela"] = datos
        st.session_state["ubicacion_editable"] = datos["ubicacion"]
        st.session_state["clasificacion_editable"] = datos["clasificacion_detectada"]

if "parcela" in st.session_state:
    normalizada = adaptar_parcela(st.session_state["parcela"])
    if normalizada is None:
        limpiar_consulta()
        st.warning("Datos viejos en sesión. Consultá nuevamente la nomenclatura.")
    else:
        st.session_state["parcela"] = normalizada
        st.session_state.setdefault("ubicacion_editable", normalizada["ubicacion"])
        st.session_state.setdefault("clasificacion_editable", normalizada["clasificacion_detectada"])
        if st.session_state["clasificacion_editable"] not in TIPOS_PARCELA:
            st.session_state["clasificacion_editable"] = "Sin determinar"

if "parcela" in st.session_state:
    parcela = st.session_state["parcela"]
    st.success("Consulta parcelaria recibida del ITC.")
    st.markdown(f"**Nomenclatura:** {parcela['nomenclatura']}  |  **Estado:** {parcela.get('estado','No informado')}  |  "
                f"**Ubicación:** {parcela.get('ubicacion') or 'Sin dato'}")
    if parcela.get("google_maps_url"):
        st.link_button("📍 Ubicación aproximada en Google Maps", parcela["google_maps_url"])

    tab_docs, tab_presup = st.tabs(["📂 Planos y documentos", "📄 Presupuestos VEP / CDA"])
    with tab_docs:
        docs = parcela.get("documentos", [])
        if parcela.get("documentos_error"):
            st.warning(parcela["documentos_error"])
        st.write(f"**Documentos encontrados:** {len(docs)}")
        st.caption("Listado obtenido desde los servicios del Gestor de Documentos de Catastro, sin abrir su portal web.")
        if not docs:
            st.info("No se encontraron documentos. Puede deberse a un acceso restringido, a una respuesta vacía o a que no hay archivos para esta parcela.")
        else:
            planos = [d for d in docs if es_plano_mensura(d)]
            veps = [d for d in docs if es_vep(d)]
            for rotulo, candidatos in (("Plano de mensura", planos), ("VEP", veps)):
                vigentes = [d for d in candidatos if str(d.get("Vigencia") or "").strip().upper() == "S"]
                ordenados = sorted(vigentes or candidatos, key=fecha_orden, reverse=True)
                if ordenados:
                    mejor = ordenados[0]
                    if vigentes:
                        st.success(f"{rotulo} marcado vigente: {documento_nombre(mejor)} — {fecha_documento(mejor)}")
                    else:
                        st.info(f"{rotulo} más reciente encontrado: {documento_nombre(mejor)} — {fecha_documento(mejor)}. **Vigencia sin confirmar.**")
            # Atajos a documentos identificados como vigentes por el Gestor.
            ultimos_vigentes = {}
            for nombre, grupo in (("plano", planos), ("vep", veps)):
                candidatos = [d for d in grupo if str(d.get("Vigencia") or "").strip().upper() == "S" and id_archivo(d)]
                if candidatos:
                    ultimos_vigentes[nombre] = sorted(candidatos, key=fecha_orden, reverse=True)[0]
            atajo_plano, atajo_vep = st.columns(2)
            with atajo_plano:
                if st.button("📐 Preparar último plano marcado vigente", disabled="plano" not in ultimos_vigentes, use_container_width=True):
                    with st.spinner("Descargando plano..."):
                        try:
                            d = ultimos_vigentes["plano"]
                            st.session_state["pdf_documento"] = descargar_documento(d, USUARIO_ITC or parcela.get("usuario_itc", ""))
                            st.session_state["pdf_archivo_nombre"] = nombre_pdf_documento(d, parcela["nomenclatura"])
                        except (requests.RequestException, ValueError) as exc:
                            st.session_state.pop("pdf_documento", None)
                            st.error(f"No se pudo obtener el plano: {exc}")
            with atajo_vep:
                if st.button("📋 Preparar última VEP marcada vigente", disabled="vep" not in ultimos_vigentes, use_container_width=True):
                    with st.spinner("Descargando VEP..."):
                        try:
                            d = ultimos_vigentes["vep"]
                            st.session_state["pdf_documento"] = descargar_documento(d, USUARIO_ITC or parcela.get("usuario_itc", ""))
                            st.session_state["pdf_archivo_nombre"] = nombre_pdf_documento(d, parcela["nomenclatura"])
                        except (requests.RequestException, ValueError) as exc:
                            st.session_state.pop("pdf_documento", None)
                            st.error(f"No se pudo obtener la VEP: {exc}")
            if len(ultimos_vigentes) < 2:
                st.caption("Si un atajo está deshabilitado, el servicio no informó un documento de ese tipo con vigencia 'S' e ID digital. Revisá el listado manualmente.")
            st.caption("'Vigente' aquí refleja un indicador del documento; no reemplaza la comprobación legal de la subsistencia de la VEP.")
            filtro = st.radio("Filtrar documentos", ["Todos", "Planos / mensuras", "VEP"], horizontal=True)
            visibles = docs if filtro == "Todos" else (planos if filtro == "Planos / mensuras" else veps)
            visibles = sorted(visibles, key=fecha_orden, reverse=True)
            if not visibles:
                st.info("No hay documentos de este tipo en la respuesta.")
            else:
                registros = []
                for i, d in enumerate(visibles):
                    registros.append({
                        "#": i+1,
                        "Documento": documento_nombre(d),
                        "Tipo": str(d.get("TipoDocumento") or "-"),
                        "Fecha": fecha_documento(d),
                        "Vigencia": vigencia_documento(d),
                        "Archivo": "Disponible" if id_archivo(d) else "Sin ID digital",
                    })
                st.dataframe(registros, hide_index=True, use_container_width=True)
                con_id = [d for d in visibles if id_archivo(d)]
                if con_id:
                    indices = range(len(con_id))
                    elegido = st.selectbox(
                        "Elegí un documento para verlo y descargarlo dentro de esta página",
                        indices,
                        format_func=lambda i: f"{documento_nombre(con_id[i])} | {fecha_documento(con_id[i])} | ID {id_archivo(con_id[i])}",
                    )
                    documento = con_id[elegido]
                    if st.button("👁️ Cargar y visualizar documento", type="primary", key="abrir_doc"):
                        with st.spinner("Descargando documento desde Catastro..."):
                            try:
                                st.session_state["pdf_documento"] = descargar_documento(
                                    documento, USUARIO_ITC or parcela.get("usuario_itc", "")
                                )
                                st.session_state["pdf_archivo_nombre"] = nombre_pdf_documento(
                                    documento, parcela["nomenclatura"]
                                )
                            except (requests.RequestException, ValueError) as exc:
                                st.session_state.pop("pdf_documento", None)
                                st.error(f"Catastro no entregó este archivo: {str(exc)}")
                else:
                    st.warning("Los registros no incluyen identificadores digitales de archivo descargables.")

        if "pdf_documento" in st.session_state:
            st.download_button(
                "⬇️ Descargar documento en PDF",
                data=st.session_state["pdf_documento"],
                file_name=st.session_state["pdf_archivo_nombre"],
                mime="application/pdf", key="descargar_documento_pdf",
            )
            if hasattr(st, "pdf"):
                st.pdf(st.session_state["pdf_documento"], height=650)
            else:
                st.info("Para ver el PDF dentro de la página instalá streamlit[pdf]. La descarga ya está disponible.")

    with tab_presup:
        st.subheader("Presupuesto profesional")
        cliente = st.text_input("Nombre del comitente", placeholder="Nombre del cliente")
        ubicacion = st.text_input("Ubicación / loteo (editable)", key="ubicacion_editable")
        clasificacion = st.selectbox("Clasificación de parcela (revisar)", TIPOS_PARCELA, key="clasificacion_editable")
        st.caption("Si Catastro no indica la clasificación, confirmala manualmente antes de presupuestar.")
        if clasificacion == "Sin determinar":
            st.info("Confirmá la clasificación antes de presupuestar.")
        st.write("**Pago:** " + ("50% al inicio / 50% al final" if clasificacion in CLASES_RURALES else "Pago único al finalizar"))
        col_vep, col_cda = st.columns(2)
        with col_vep:
            honorarios_vep = st.text_input("Honorarios VEP ($)", placeholder="Ej.: 500.000")
        with col_cda:
            honorarios_cda = st.text_input("Honorarios CDA ($)", placeholder="Ej.: 450.000")
        observaciones = st.text_area("Observaciones del presupuesto (opcional)", height=75)
        for tipo, entrada in (("VEP", honorarios_vep), ("CDA", honorarios_cda)):
            if not entrada.strip():
                continue
            try:
                importe = leer_importe(entrada)
            except ValueError as exc:
                st.error(f"{tipo}: {exc}")
                continue
            if cliente.strip() and clasificacion != "Sin determinar":
                pdf = generar_pdf(
                    tipo, cliente.strip(), parcela["nomenclatura"], importe,
                    ubicacion, clasificacion, observaciones,
                )
                fecha_archivo = datetime.now(TZ_NEUQUEN).strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    f"⬇️ Descargar presupuesto {tipo} (PDF)", data=pdf,
                    file_name=f"Presupuesto_{tipo}_{parcela['nomenclatura']}_{fecha_archivo}.pdf",
                    mime="application/pdf", key=f"descargar_presupuesto_{tipo}",
                )
        st.caption("Los presupuestos se descargan a tu equipo. Google Drive / Presupuestos / Año se incorporará después.")
else:
    st.info("Ingresá una nomenclatura para consultar la parcela y sus documentos directamente desde la aplicación.")
