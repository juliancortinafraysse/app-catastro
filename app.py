from datetime import datetime
import io
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import requests

st.set_page_config(
    page_title="Estudio JCF - Catastro Nqn", page_icon="📐", layout="centered"
)

st.markdown("""
    <style>
    .main { background-color: #f4f6f9; }
    h1 { color: #1a365d; text-align: center; font-size: 24px; }
    .subtitle { text-align: center; color: #718096; font-size: 14px; margin-bottom: 20px; }
    </style>
""", unsafe_allow_html=True)

st.markdown("<h1>Estudio JCF - Catastro Nqn</h1>", unsafe_allow_html=True)
st.markdown(
    "<div class='subtitle'>Consulta ITC y Generador de Presupuestos VEP /"
    " CDA</div>",
    unsafe_allow_html=True,
)

ITC_BASE = "https://catastro.neuquen.gov.ar"
ITC_USER = "jfraysse"
ITC_PASS = "juchu1988"


def consultar_itc(nomenclatura):
  nomen_limpia = nomenclatura.replace("-", "").replace(" ", "").strip()
  session = requests.Session()

  login_url = (
      f"{ITC_BASE}/site/ITC.Services/Json/SecurityServiceJson.svc/json/PostAuthenticate"
  )
  headers = {
      "accept": "application/json, text/javascript, */*; q=0.01",
      "content-type": "application/json; charset=UTF-8",
      "origin": ITC_BASE,
      "referer": (
          f"{ITC_BASE}/portal/apps/webappviewer/index.html?id=63f0c3f9a047425280951f418621171f"
      ),
      "user-agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/120.0.0.0 Safari/537.36"
      ),
  }
  payload = {"user": ITC_USER, "pass": ITC_PASS}

  try:
    r_login = session.post(login_url, json=payload, headers=headers, timeout=15)
    if r_login.status_code != 200:
      return None, "Error de autenticación con el ITC de Catastro."

    url_alfa = f"{ITC_BASE}/site/ITC.Services/Json/ParcelaServiceJson.svc/json/GetByNomenclatura?nomen={nomen_limpia}&ph="
    r_alfa = session.get(url_alfa, headers=headers, timeout=15)
    
    if r_alfa.status_code != 200:
      return None, "Catastro no respondió a la consulta de nomenclatura."

    datos_lista = r_alfa.json().get("GetByNomenclaturaResult", [])

    if not datos_lista:
      return None, "Nomenclatura no encontrada en el sistema de Catastro."

    parcela = next(
        (p for p in datos_lista if p.get("Estado") == "A"), datos_lista[0]
    )
    object_id = parcela.get("ObjectId")
    service_id = parcela.get("ServiceId", 7)

    lat, lon = None, None
    if object_id:
      url_geo = (
          f"{ITC_BASE}/site/ITC.Services/proxy.ashx?"
          f"{ITC_BASE}/server/rest/services/EditFeatures/FeatureServer/{service_id}/query?"
          f"f=json&where=ObjectId%20%3D%20{object_id}&returnGeometry=true&outSR=4326&outFields=*"
      )
      r_geo = session.get(url_geo, headers=headers, timeout=15)
      geo_json = r_geo.json()
      features = geo_json.get("features", [])
      if features and "geometry" in features[0]:
        rings = features[0]["geometry"].get("rings", [[]])[0]
        if rings:
          lon = sum(pt[0] for pt in rings) / len(rings)
          lat = sum(pt[1] for pt in rings) / len(rings)

    resultado = {
        "nomenclatura": parcela.get("Nomenclatura"),
        "plano_antecedente": parcela.get("Tramite") or "Sin antecedente",
        "ubicacion_loteo": parcela.get("UbicacionLoteo") or "-",
        "lote": parcela.get("DenominacionLote") or "-",
        "manzana": parcela.get("DenominacionManzana") or "-",
        "sup_mensura": parcela.get("SupMensura") or 0,
        "google_maps_url": (
            f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            if lat and lon
            else ""
        ),
    }
    return resultado, None
  except Exception as e:
    return None, f"Error de conexión con el servidor: {str(e)}"


def generar_pdf(
    tipo, cliente, nomenclatura, honorarios, ubicacion, es_rui_rue
):
  buffer = io.BytesIO()
  p = canvas.Canvas(buffer, pagesize=A4)
  width, height = A4

  # --- ENCABEZADO PROFESIONAL ---
  p.setFillColorRGB(0.1, 0.2, 0.35)
  p.rect(0, height - 40, width, 40, stroke=0, fill=1)

  p.setFillColorRGB(1, 1, 1)
  p.setFont("Helvetica-Bold", 12)
  p.drawString(40, height - 26, "ESTUDIO DE AGRIMENSURA JCF")

  p.setFillColorRGB(0.2, 0.2, 0.2)
  p.setFont("Helvetica-Bold", 11)
  p.drawString(40, height - 65, "Ing. Agrim. Julián Cortina Fraysse")

  p.setFont("Helvetica", 9)
  p.setFillColorRGB(0.4, 0.4, 0.4)
  p.drawString(
      40, height - 80, "Matrícula Profesional AGR. 382 (Neuquén) | JCF Estudio"
  )
  p.drawString(
      40, height - 93, "Tel: 299-4116680  |  juliancortinafraysse@gmail.com"
  )

  p.setStrokeColorRGB(0.8, 0.8, 0.8)
  p.setLineWidth(0.75)
  p.line(40, height - 105, width - 40, height - 105)

  # --- DATOS DEL COMITENTE Y FECHA ---
  fecha = datetime.now().strftime("%d de octubre de 2026")
  p.setFont("Helvetica", 10)
  p.setFillColorRGB(0.2, 0.2, 0.2)
  p.drawString(40, height - 130, f"Neuquén, {fecha}")
  p.drawString(40, height - 148, f"Sr./Sra. Comitente: {cliente}")

  titulo = (
      "Verificación de Estado Parcelario (VEP)"
      if tipo == "VEP"
      else "Certificado de Deslinde y Amojonamiento (CDA)"
  )
  if es_rui_rue:
    titulo += " (RUI / RUE)"

  p.setFont("Helvetica-Bold", 12)
  p.setFillColorRGB(0.1, 0.2, 0.35)
  p.drawString(40, height - 182, f"REF.: PROPUESTA DE HONORARIOS - {titulo}")

  # --- CUERPO DEL PRESUPUESTO ---
  p.setFont("Helvetica", 10)
  p.setFillColorRGB(0.2, 0.2, 0.2)
  p.drawString(
      40,
      height - 210,
      "Me dirijo a Ud. a fin de elevar la propuesta de honorarios profesionales"
      " por los trabajos técnicos",
  )
  p.drawString(40, height - 225, "que se detallan a continuación:")

  p.setFillColorRGB(0.96, 0.97, 0.99)
  p.rect(40, height - 315, width - 80, 75, stroke=1, fill=1)

  p.setFont("Helvetica-Bold", 10)
  p.setFillColorRGB(0.1, 0.2, 0.35)
  p.drawString(55, height - 250, "DETALLE TÉCNICO DE LA PARCELA")

  p.setFont("Helvetica", 10)
  p.setFillColorRGB(0.2, 0.2, 0.2)
  p.drawString(55, height - 270, f"• Objeto: {titulo}")
  p.drawString(55, height - 286, f"• Nomenclatura Catastral: {nomenclatura}")
  p.drawString(55, height - 302, f"• Ubicación / Loteo: {ubicacion}")

  # --- HONORARIOS ---
  p.setFont("Helvetica-Bold", 12)
  p.setFillColorRGB(0.1, 0.2, 0.35)
  p.drawString(
      40, height - 355, f
