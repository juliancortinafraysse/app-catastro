"""Estudio JCF | Consultas ITC y presupuestos VEP/CDA.

Versión revisada: no guarda credenciales en el código y no afirma obtener
planos/VEP vigentes hasta disponer de una integración documentada y probada.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from io import BytesIO
import re
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
CLASES_RURALES = {"RUI", "ROE", "RUE"}  # Confirmar el uso de RUE con ITC.


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


def consultar_itc(nomenclatura, usuario, contrasena):
    numero = limpiar_nomenclatura(nomenclatura)
    if not re.fullmatch(r"\d{15}", numero):
        return None, "Ingresá una nomenclatura de 15 dígitos (con o sin guiones)."
    if not usuario or not contrasena:
        return None, "Faltan ITC_USER e ITC_PASS en los Secrets de Streamlit."

    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "content-type": "application/json; charset=UTF-8",
        "origin": ITC_BASE,
        "referer": VISOR_BASE,
        "user-agent": "Mozilla/5.0 (compatible; EstudioJCF/1.0)",
    }
    try:
        with requests.Session() as session:
            session.headers.update(headers)
            login = session.post(
                f"{ITC_BASE}/site/ITC.Services/Json/SecurityServiceJson.svc/json/PostAuthenticate",
                json={"user": usuario, "pass": contrasena},
                timeout=15,
            )
            login.raise_for_status()
            # El formato de éxito del login debe contrastarse con ITC real.
            respuesta = session.get(
                f"{ITC_BASE}/site/ITC.Services/Json/ParcelaServiceJson.svc/json/GetByNomenclatura",
                params={"nomen": numero, "ph": ""}, timeout=15,
            )
            respuesta.raise_for_status()
            contenido = respuesta.json()
            lista = contenido.get("GetByNomenclaturaResult", []) if isinstance(contenido, dict) else []
            if isinstance(lista, dict):
                lista = [lista]
            if not isinstance(lista, list):
                return None, "El ITC devolvió una respuesta con un formato inesperado."
            # No usar por error una parcela distinta devuelta en la lista.
            candidatas = [
                p for p in lista if isinstance(p, dict)
                and limpiar_nomenclatura(str(p.get("Nomenclatura") or "")) == numero
            ]
            if not candidatas:
                return None, "No se encontró una parcela con esa nomenclatura exacta."
            activas = [p for p in candidatas if str(p.get("Estado", "")).upper() == "A"]
            if not activas and any("Estado" in p for p in candidatas):
                return None, "La parcela aparece en ITC, pero no se encontró un registro activo (Estado A)."
            parcela = activas[0] if activas else candidatas[0]

            latlon = None
            object_id = parcela.get("ObjectId")
            service_id = parcela.get("ServiceId") or 7  # Valor usado en la versión original.
            if object_id is not None and re.fullmatch(r"\d+", str(object_id)) and re.fullmatch(r"\d+", str(service_id)):
                try:
                    url_geo = (
                        f"{ITC_BASE}/site/ITC.Services/proxy.ashx?"
                        f"{ITC_BASE}/server/rest/services/EditFeatures/FeatureServer/{service_id}/query?"
                        f"f=json&where=ObjectId%20%3D%20{object_id}"
                        "&returnGeometry=true&outSR=4326&outFields=*"
                    )
                    rg = session.get(url_geo, timeout=15)
                    rg.raise_for_status()
                    features = rg.json().get("features", [])
                    if features:
                        latlon = centro_aproximado(features[0].get("geometry", {}).get("rings", []))
                except (requests.RequestException, ValueError, TypeError, KeyError):
                    pass  # Un error geográfico no cancela el presupuesto.

        return {
            "nomenclatura": numero,
            "ubicacion": str(parcela.get("UbicacionLoteo") or ""),
            "clasificacion_detectada": detectar_clasificacion(parcela),
            "estado": str(parcela.get("Estado") or "No informado"),
            "google_maps_url": (
                f"https://www.google.com/maps/search/?api=1&query={latlon[0]:.7f}%2C{latlon[1]:.7f}"
                if latlon else ""
            ),
            # Conservamos el vínculo original, SIN asegurar que seleccione la parcela.
            "visor_url": f"{VISOR_BASE}&nomenclatura={quote(numero)}",
        }, None
    except requests.Timeout:
        return None, "Se agotó el tiempo de respuesta del ITC. Probá de nuevo."
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None, "No se pudo completar la consulta al ITC. Revisá conexión, credenciales y formato de respuesta."


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
st.set_page_config(page_title="Estudio JCF - Catastro Nqn", page_icon="📐", layout="centered")
st.title("📐 Estudio JCF - Catastro Nqn")
st.caption("Consulta ITC y presupuestos VEP / CDA en PDF")


def limpiar_consulta():
    for clave in ("parcela", "ubicacion_editable", "clasificacion_editable"):
        st.session_state.pop(clave, None)


nomen_input = st.text_input(
    "Nomenclatura catastral (15 dígitos)", key="nomen_input",
    placeholder="Ej.: 092007824920000", on_change=limpiar_consulta,
)
if st.button("Consultar parcela en ITC", type="primary"):
    usuario = st.secrets.get("ITC_USER", "")
    contrasena = st.secrets.get("ITC_PASS", "")
    with st.spinner("Consultando Catastro Neuquén..."):
        datos, error = consultar_itc(nomen_input, usuario, contrasena)
    limpiar_consulta()
    if error:
        st.error(error)
    else:
        st.session_state["parcela"] = datos
        st.session_state["ubicacion_editable"] = datos["ubicacion"]
        st.session_state["clasificacion_editable"] = datos["clasificacion_detectada"]

if "parcela" in st.session_state:
    parcela = st.session_state["parcela"]
    st.success("Consulta parcelaria recibida desde ITC.")
    st.write(f"**Nomenclatura:** {parcela['nomenclatura']}")
    st.write(f"**Estado según respuesta:** {parcela['estado']}")
    st.write(f"**Clasificación detectada:** {parcela['clasificacion_detectada']}")
    if parcela["clasificacion_detectada"] == "Sin determinar":
        st.warning("ITC no aportó una clasificación reconocible. Seleccionala manualmente antes de presupuestar.")
    st.caption("La clasificación automática es orientativa hasta validar el campo específico de ITC.")

    col_a, col_b = st.columns(2)
    with col_a:
        st.link_button("Abrir visor ITC", parcela["visor_url"])
    with col_b:
        if parcela["google_maps_url"]:
            st.link_button("📍 Ubicación aproximada", parcela["google_maps_url"])
    st.caption("El enlace al visor NO está verificado como acceso directo al último plano. "
               "Tampoco descarga todavía la VEP vigente.")
    st.divider()
    st.subheader("Presupuestos profesionales")
    cliente = st.text_input("Nombre del comitente", placeholder="Completar nombre")
    ubicacion = st.text_input("Ubicación / loteo (editable)", key="ubicacion_editable")
    clasificacion = st.selectbox("Clasificación de parcela (revisar)", TIPOS_PARCELA, key="clasificacion_editable")
    if clasificacion == "Sin determinar":
        st.info("Confirmá la clasificación: ROE/RUI activa el pago 50% al inicio y 50% al final.")
    if clasificacion == "RUE":
        st.info("RUE se mantiene como opción heredada del código original; confirmá si corresponde a tu nomenclador.")
    st.write("**Pago:** " + ("50% al inicio / 50% al final" if clasificacion in CLASES_RURALES
                            else "Pago único al finalizar"))
    col1, col2 = st.columns(2)
    with col1:
        honorarios_vep = st.text_input("Honorarios VEP ($)", placeholder="Ej.: 500.000")
    with col2:
        honorarios_cda = st.text_input("Honorarios CDA ($)", placeholder="Ej.: 450.000")
    observaciones = st.text_area("Observaciones del presupuesto (opcional)", height=75)
    if not cliente.strip():
        st.info("Completá el nombre del cliente para habilitar las descargas.")
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
                mime="application/pdf", key=f"descargar_{tipo}",
            )
    st.caption("En esta etapa los archivos se descargan al equipo. Guardado en Drive / Presupuestos / Año: pendiente de implementar.")
else:
    st.info("Ingresá una nomenclatura y consultá el ITC para comenzar.")
    st.link_button("Abrir Catastro Neuquén", ITC_PORTAL)
