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
      "accept": "*/*",
      "content-type": "application/json",
      "referer": (
          f"{ITC_BASE}/portal/apps/webappviewer/index.html?id=63f0c3f9a047425280951f418621171f"
      ),
      "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
  }
  payload = {"user": ITC_USER, "pass": ITC_PASS}

  try:
    r_login = session.post(login_url, json=payload, headers=headers, timeout=15)
    if r_login.status_code != 200:
      return None, "Error de autenticación con el ITC de Catastro."

    url_alfa = f"{ITC_BASE}/site/ITC.Services/Json/ParcelaServiceJson.svc/json/GetByNomenclatura?nomen={nomen_limpia}&ph="
    r_alfa = session.get(url_alfa, headers=headers, timeout=15)
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
    return None, f"Error de conexión: {str(e)}"


def generar_pdf(tipo, cliente, nomenclatura, honorarios, ubicacion):
  buffer = io.BytesIO()
  p = canvas.Canvas(buffer, pagesize=A4)
  width, height = A4

  p.setFont("Helvetica-Bold", 14)
  p.drawString(50, height - 50, "ESTUDIO DE AGRIMENSURA JCF")
  p.setFont("Helvetica", 10)
  p.drawString(50, height - 65, "Ing. Agrim. Julián Cortina Fraysse")
  p.drawString(
      50, height - 80, "Mat. Prof. AGR. 382 | Alias: FILO.RAYO.ABEDUL"
  )
  p.drawString(50, height - 95, "Tel: 299-4116680 | juliancortinafraysse@gmail.com")
  p.setLineWidth(0.5)
  p.line(50, height - 105, width - 50, height - 105)

  fecha = datetime.now().strftime("%d de octubre de 2026")
  p.drawString(50, height - 130, f"Neuquén, {fecha}")
  p.drawString(50, height - 150, f"Sr./Sra.: {cliente}")

  titulo = (
      "Verificación de Estado Parcelario (VEP)"
      if tipo == "VEP"
      else "Certificado de Deslinde y Amojonamiento (CDA)"
  )
  p.setFont("Helvetica-Bold", 11)
  p.drawString(50, height - 180, f"Ref.: {titulo}")

  p.setFont("Helvetica", 10)
  p.drawString(
      50,
      height - 200,
      "Me dirijo a Ud. a fin de elevar el presupuesto del trabajo técnico:",
  )
  p.setFont("Helvetica-Bold", 10)
  p.drawString(50, height - 230, f"● Objeto: {titulo}")
  p.setFont("Helvetica", 10)
  p.drawString(70, height - 245, f"Nomenclatura Catastral: {nomenclatura}")
  p.drawString(70, height - 260, f"Ubicación: {ubicacion}")

  p.setFont("Helvetica-Bold", 11)
  p.drawString(50, height - 295, f"Honorarios Profesionales: ${honorarios} + IVA")

  p.setFont("Helvetica-Bold", 10)
  p.drawString(50, height - 325, "Los honorarios incluyen:")
  p.setFont("Helvetica", 10)
  if tipo == "VEP":
    p.drawString(
        70,
        height - 345,
        "• Tramitación de la VEP en la DPCeIT y Certificado Catastral.",
    )
    p.drawString(
        70, height - 360, "• Sellados provinciales y gastos de oficina técnica."
    )
  else:
    p.drawString(
        70,
        height - 345,
        "• Relevamiento planimétrico y replanteo perimetral.",
    )
    p.drawString(
        70, height - 360, "• Tramitación del CDA municipal y estacas."
    )

  p.setFont("Helvetica-Bold", 10)
  p.drawString(50, height - 410, "Formas de pago y Plazos:")
  p.setFont("Helvetica", 10)
  p.drawString(
      70,
      height - 430,
      "• Pago único contra entrega de documentación registrada.",
  )
  p.drawString(
      70, height - 445, "• Plazo estimado: 15 a 30 días hábiles."
  )

  p.drawString(50, height - 510, "Sin otro particular, saluda atentamente,")
  p.setFont("Helvetica-Bold", 10)
  p.drawString(50, height - 550, "Ing. Agrim. Julián Cortina Fraysse")
  p.drawString(50, height - 565, "Matrícula Profesional AGR. 382")

  p.showPage()
  p.save()
  buffer.seek(0)
  return buffer


nomenclatura_input = st.text_input(
    "Nomenclatura Catastral (15 dígitos):", value="092007835750000"
)

if st.button("Consultar Parcelas en ITC", type="primary"):
  with st.spinner("Conectando con el ITC de Catastro..."):
    data, error = consultar_itc(nomenclatura_input)
    if error:
      st.error(error)
    else:
      st.session_state["parcela"] = data

if "parcela" in st.session_state:
  p = st.session_state["parcela"]
  st.success("¡Parcela encontrada con éxito!")

  st.markdown(f"**Ubicación:** {p['ubicacion_loteo']}")
  st.markdown(f"**Lote / Manzana:** Lote {p['lote']} - Mz {p['manzana']}")
  st.markdown(f"**Superficie de Mensura:** {p['sup_mensura']} m²")
  st.markdown(
      f"**Plano Antecedente:** :orange[**{p['plano_antecedente']}**]"
  )

  if p["google_maps_url"]:
    st.markdown(
        f"<a href='{p['google_maps_url']}' target='_blank' style='display:block;"
        " text-align:center; background:#319795; color:white; padding:10px;"
        " border-radius:6px; text-decoration:none; font-weight:bold;'>📍 Abrir"
        " ubicación exacta en Google Maps</a>",
        unsafe_allow_html=True,
    )

  st.divider()
  st.subheader("Armar Presupuesto Automático")
  cliente = st.text_input("Nombre del Comitente / Cliente:", value="Juan Pérez")

  col1, col2 = st.columns(2)
  with col1:
    monto_vep = st.text_input("Honorarios VEP ($):", value="500.000")
  with col2:
    monto_cda = st.text_input("Honorarios CDA ($):", value="450.000")

  col_btn1, col_btn2 = st.columns(2)
  with col_btn1:
    if st.button("📄 Generar PDF VEP"):
      pdf_buffer = generar_pdf(
          "VEP",
          cliente,
          p["nomenclatura"],
          monto_vep,
          p["ubicacion_loteo"],
      )
      st.download_button(
          label="⬇️ Descargar PDF VEP",
          data=pdf_buffer,
          file_name=f"Presupuesto_VEP_{p['nomenclatura']}.pdf",
          mime="application/pdf",
      )

  with col_btn2:
    if st.button("📄 Generar PDF CDA"):
      pdf_buffer = generar_pdf(
          "CDA",
          cliente,
          p["nomenclatura"],
          monto_cda,
          p["ubicacion_loteo"],
      )
      st.download_button(
          label="⬇️ Descargar PDF CDA",
          data=pdf_buffer,
          file_name=f"Presupuesto_CDA_{p['nomenclatura']}.pdf",
          mime="application/pdf",
      )
