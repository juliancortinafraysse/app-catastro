from datetime import datetime
import io
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import requests

st.set_page_config(
    page_title="Estudio JCF - Catastro Nqn", page_icon="📐", layout="centered"
)

st.markdown(
    "<h1 style='text-align: center; color: #1a365d;'>Estudio JCF - Catastro"
    " Nqn</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align: center; color: #718096;'>Consulta ITC y Generador"
    " de Presupuestos VEP / CDA</p>",
    unsafe_allow_html=True,
)

ITC_BASE = "https://catastro.neuquen.gov.ar"
ITC_USER = "jfraysse"
ITC_PASS = "juchu1988"


def consultar_itc(nomenclatura):
  nomen_limpia = nomenclatura.replace("-", "").replace(" ", "").strip()
  session = requests.Session()
  login_url = f"{ITC_BASE}/site/ITC.Services/Json/SecurityServiceJson.svc/json/PostAuthenticate"
  headers = {
      "accept": "application/json, text/javascript, */*; q=0.01",
      "content-type": "application/json; charset=UTF-8",
      "origin": ITC_BASE,
      "referer": f"{ITC_BASE}/portal/apps/webappviewer/index.html?id=63f0c3f9a047425280951f418621171f",
      "user-agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/120.0.0.0 Safari/537.36"
      ),
  }
  payload = {"user": ITC_USER, "pass": ITC_PASS}

  try:
    r_login = session.post(login_url, json=payload, headers=headers, timeout=10)
    if r_login.status_code != 200:
      return None, "Error de autenticación con el ITC de Catastro."

    url_alfa = f"{ITC_BASE}/site/ITC.Services/Json/ParcelaServiceJson.svc/json/GetByNomenclatura?nomen={nomen_limpia}&ph="
    r_alfa = session.get(url_alfa, headers=headers, timeout=10)
    if r_alfa.status_code != 200:
      return None, "Catastro no respondió a la consulta."

    datos_lista = r_alfa.json().get("GetByNomenclaturaResult", [])
    if not datos_lista:
      return None, "Nomenclatura no encontrada en el sistema de Catastro."

    parcela = next(
        (p for p in datos_lista if p.get("Estado") == "A"), datos_lista[0]
    )
    object_id = parcela.get("ObjectId")
    service_id = parcela.get("ServiceId", 7)

    desc_catastro = f"{parcela.get('UbicacionLoteo', '')} {parcela.get('Tramite', '')} {parcela.get('DenominacionLote', '')}".upper(
    )
    es_rui_rue = "RUI" in desc_catastro or "RUE" in desc_catastro
    tramite = parcela.get("Tramite") or "Sin antecedente"

    lat, lon = None, None
    if object_id:
      url_geo = (
          f"{ITC_BASE}/site/ITC.Services/proxy.ashx?"
          f"{ITC_BASE}/server/rest/services/EditFeatures/FeatureServer/{service_id}/query?"
          f"f=json&where=ObjectId%20%3D%20{object_id}&returnGeometry=true&outSR=4326&outFields=*"
      )
      r_geo = session.get(url_geo, headers=headers, timeout=10)
      geo_json = r_geo.json()
      features = geo_json.get("features", [])
      if features and "geometry" in features[0]:
        rings = features[0]["geometry"].get("rings", [[]])[0]
        if rings:
          lon = sum(pt[0] for pt in rings) / len(rings)
          lat = sum(pt[1] for pt in rings) / len(rings)

    # Enlaces directos a documentos y fichas de mensura/expedientes en el ITC
    doc_url = f"{ITC_BASE}/site/Documentos/VerDocumento.aspx?nomen={nomen_limpia}&tramite={tramite}"

    resultado = {
        "nomenclatura": parcela.get("Nomenclatura"),
        "plano_antecedente": tramite,
        "ubicacion_loteo": parcela.get("UbicacionLoteo") or "-",
        "lote": parcela.get("DenominacionLote") or "-",
        "manzana": parcela.get("DenominacionManzana") or "-",
        "sup_mensura": parcela.get("SupMensura") or 0,
        "es_rui_rue": es_rui_rue,
        "google_maps_url": (
            f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            if lat and lon
            else ""
        ),
        "documento_url": doc_url,
    }
    return resultado, None
  except Exception as e:
    return None, f"Error de conexión con el servidor: {str(e)}"


def generar_pdf(
    tipo, cliente, nomenclatura, honorarios, ubicacion, plano, es_rui_rue
):
  buffer = io.BytesIO()
  pdf = canvas.Canvas(buffer, pagesize=A4)
  w, h = A4

  # Encabezado corporativo JCF
  pdf.setFillColorRGB(0.1, 0.2, 0.35)
  pdf.rect(0, h - 40, w, 40, stroke=0, fill=1)
  pdf.setFillColorRGB(1, 1, 1)
  pdf.setFont("Helvetica-Bold", 12)
  pdf.drawString(40, h - 26, "ESTUDIO DE AGRIMENSURA JCF")

  pdf.setFillColorRGB(0.2, 0.2, 0.2)
  pdf.setFont("Helvetica-Bold", 11)
  pdf.drawString(40, h - 65, "Ing. Agrim. Julián Cortina Fraysse")
  pdf.setFont("Helvetica", 9)
  pdf.setFillColorRGB(0.4, 0.4, 0.4)
  pdf.drawString(
      40, h - 80, "Matrícula Profesional AGR. 382 (Neuquén) | JCF Estudio"
  )
  pdf.drawString(40, h - 93, "Tel: 299-4116680 | juliancortinafraysse@gmail.com")

  pdf.setStrokeColorRGB(0.8, 0.8, 0.8)
  pdf.setLineWidth(0.75)
  pdf.line(40, h - 105, w - 40, h - 105)

  # Fecha y Comitente
  fecha = datetime.now().strftime("%d de octubre de 2026")
  pdf.setFont("Helvetica", 10)
  pdf.setFillColorRGB(0.2, 0.2, 0.2)
  pdf.drawString(40, h - 130, f"Neuquén, {fecha}")
  pdf.drawString(40, h - 148, f"Sr./Sra. Comitente: {cliente}")

  titulo = (
      "Verificación de Estado Parcelario (VEP)"
      if tipo == "VEP"
      else "Certificado de Deslinde y Amojonamiento (CDA)"
  )
  if es_rui_rue:
    titulo += " (RUI / RUE)"

  pdf.setFont("Helvetica-Bold", 12)
  pdf.setFillColorRGB(0.1, 0.2, 0.35)
  pdf.drawString(40, h - 182, f"REF.: PROPUESTA DE HONORARIOS - {titulo}")

  # Cuerpo técnico
  pdf.setFont("Helvetica", 10)
  pdf.setFillColorRGB(0.2, 0.2, 0.2)
  pdf.drawString(
      40,
      h - 210,
      "Me dirijo a Ud. a fin de elevar la propuesta de honorarios profesionales"
      " por los trabajos técnicos:",
  )

  pdf.setFillColorRGB(0.96, 0.97, 0.99)
  pdf.rect(40, h - 330, w - 80, 90, stroke=1, fill=1)
  pdf.setFont("Helvetica-Bold", 10)
  pdf.setFillColorRGB(0.1, 0.2, 0.35)
  pdf.drawString(55, h - 245, "DETALLE TÉCNICO DE LA PARCELA")

  pdf.setFont("Helvetica", 10)
  pdf.setFillColorRGB(0.2, 0.2, 0.2)
  pdf.drawString(55, h - 265, f"• Objeto: {titulo}")
  pdf.drawString(55, h - 285, f"• Nomenclatura Catastral: {nomenclatura}")
  pdf.drawString(55, h - 305, f"• Ubicación / Loteo: {ubicacion}")
  pdf.drawString(55, h - 320, f"• Plano Antecedente (Mensura): {plano}")

  pdf.setFont("Helvetica-Bold", 12)
  pdf.setFillColorRGB(0.1, 0.2, 0.35)
  pdf.drawString(40, h - 370, f"Honorarios Profesionales Totales: ${honorarios}")

  pdf.setFont("Helvetica-Bold", 10)
  pdf.drawString(40, h - 400, "Los honorarios profesionales incluyen:")
  pdf.setFont("Helvetica", 10)
  if tipo == "VEP":
    pdf.drawString(
        55, h - 418, "• Relevamiento planimétrico y verificación en terreno."
    )
    pdf.drawString(
        55, h - 433, "• Confección y tramitación digital de la VEP en DPCeIT."
    )
    pdf.drawString(55, h - 448, "• Obtención del Certificado Catastral.")
  else:
    pdf.drawString(55, h - 418, "• Relevamiento de antecedentes y mensura.")
    pdf.drawString(55, h - 433, "• Replanteo y amojonamiento perimetral.")
    pdf.drawString(55, h - 448, "• Confección y visado del CDA municipal.")

  pdf.setFont("Helvetica-Bold", 10)
  pdf.setFillColorRGB(0.1, 0.2, 0.35)
  pdf.drawString(40, h - 480, "Formas de Pago y Plazos de Ejecución:")
  pdf.setFont("Helvetica", 10)
  pdf.setFillColorRGB(0.2, 0.2, 0.2)

  if es_rui_rue:
    pdf.drawString(
        55,
        h - 498,
        "• 50% de anticipo en concepto de inicio de tareas técnicas y de"
        " gabinete.",
    )
    pdf.drawString(
        55,
        h - 513,
        "• 50% restante contra entrega de la documentación técnica aprobada.",
    )
  else:
    pdf.drawString(
        55, h - 498, "• Pago único contra entrega de la documentación final."
    )

  pdf.drawString(
      55, h - 528, "• Plazo estimado de ejecución: 15 a 20 días hábiles."
  )
  pdf.drawString(
      55, h - 543, "• Validez de la presente propuesta: 10 días corridos."
  )

  pdf.drawString(
      40, h - 585, "Sin otro particular, saludo a Ud. atentamente,"
  )
  pdf.setFont("Helvetica-Bold", 10)
  pdf.drawString(40, h - 645, "Ing. Agrim. Julián Cortina Fraysse")
  pdf.setFont("Helvetica", 9)
  pdf.setFillColorRGB(0.4, 0.4, 0.4)
  pdf.drawString(40, h - 658, "Matrícula Profesional AGR. 382")

  pdf.showPage()
  pdf.save()
  buffer.seek(0)
  return buffer


# --- INTERFAZ PRINCIPAL ---
nomenclatura_input = st.text_input(
    "Nomenclatura Catastral (15 dígitos):", value="092007849780000"
)

if st.button("Consultar Automático (ITC)", type="primary"):
  with st.spinner("Conectando con Catastro Neuquén..."):
    data, error = consultar_itc(nomenclatura_input)
    if error:
      st.error(error)
    else:
      st.session_state["parcela"] = data

if "parcela" in st.session_state:
  p = st.session_state["parcela"]
  st.success("¡Datos de parcela obtenidos con éxito desde Catastro!")

  st.markdown(f"**Ubicación / Loteo:** {p['ubicacion_loteo']}")
  st.markdown(
      f"**Plano Antecedente (Mensura):** :orange[**{p['plano_antecedente']}**]"
  )

  es_rui = p.get("es_rui_rue", False)
  st.markdown(
      f"**Clasificación RUI / RUE (Catastro):**"
      f" `{'Sí (Rural)' if es_rui else 'No (Urbana / Estándar)'}`"
  )

  col_link1, col_link2 = st.columns(2)
  with col_link1:
    if p.get("documento_url"):
      st.markdown(
          f"<a href='{p['documento_url']}' target='_blank'"
          " style='display:block; text-align:center; background:#2b6cb0;"
          " color:white; padding:10px; border-radius:6px; text-
