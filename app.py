import datetime
import io
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import requests

app = FastAPI(title="App Catastro JCF - Neuquén")

# =====================================================================
# MOTOR DE CONEXIÓN Y AUTENTICACIÓN ITC (DPCeIT)
# =====================================================================
ITC_BASE = "https://catastro.neuquen.gov.ar"

# Tus credenciales oficiales del ITC para que la app gestione la sesión sola
ITC_USER = "jfraysse"
ITC_PASS = "juchu1988"


def obtener_sesion_y_datos_parcela(nomenclatura: str):
  nomen_limpia = nomenclatura.replace("-", "").replace(" ", "").strip()
  session = requests.Session()

  # 1) Autenticación automática en el ITC
  login_url = f"{ITC_BASE}/site/ITC.Services/Json/SecurityServiceJson.svc/json/PostAuthenticate"
  headers = {
      "accept": "*/*",
      "content-type": "application/json",
      "referer": (
          f"{ITC_BASE}/portal/apps/webappviewer/index.html?id=63f0c3f9a047425280951f418621171f"
      ),
      "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
  }
  payload = {"user": ITC_USER, "pass": ITC_PASS}

  r_login = session.post(login_url, json=payload, headers=headers, timeout=15)
  if r_login.status_code != 200:
    raise HTTPException(
        status_code=401, detail="Error de autenticación con el ITC de Catastro."
    )

  # 2) Consulta Alfanumérica de la Parcela
  url_alfa = f"{ITC_BASE}/site/ITC.Services/Json/ParcelaServiceJson.svc/json/GetByNomenclatura?nomen={nomen_limpia}&ph="
  r_alfa = session.get(url_alfa, headers=headers, timeout=15)
  datos_lista = r_alfa.json().get("GetByNomenclaturaResult", [])

  if not datos_lista:
    raise HTTPException(
        status_code=404,
        detail=(
            "Nomenclatura no encontrada en el sistema de Catastro de Neuquén."
        ),
    )

  parcela = next(
      (p for p in datos_lista if p.get("Estado") == "A"), datos_lista[0]
  )
  object_id = parcela.get("ObjectId")
  service_id = parcela.get("ServiceId", 7)

  # 3) Consulta Geográfica (Proxy FeatureServer con outSR=4326 para GPS exacto)
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

  return {
      "nomenclatura": parcela.get("Nomenclatura"),
      "plano_antecedente": parcela.get("Tramite")
      or "Sin antecedente registrado",
      "ubicacion_loteo": parcela.get("UbicacionLoteo") or "-",
      "lote": parcela.get("DenominacionLote") or "-",
      "manzana": parcela.get("DenominacionManzana") or "-",
      "localidad": parcela.get("Localidad") or "NEUQUEN",
      "departamento": parcela.get("Departamento") or "CONFLUENCIA",
      "sup_mensura": parcela.get("SupMensura") or 0,
      "tipo_parcela": parcela.get("TipoParcela", {}).get("Descripcion", "URBANA"),
      "lat": lat,
      "lon": lon,
      "google_maps_url": (
          f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
          if lat and lon
          else ""
      ),
  }


# =====================================================================
# GENERADOR DE PRESUPUESTOS EN PDF (ESTILO ESTUDIO JCF)
# =====================================================================


def generar_pdf_presupuesto(
    tipo: str, cliente: str, nomenclatura: str, honorarios: str, detalle_extra: str
):
  buffer = io.BytesIO()
  p = canvas.Canvas(buffer, pagesize=A4)
  width, height = A4

  # Encabezado
  p.setFont("Helvetica-Bold", 14)
  p.drawString(50, height - 50, "ESTUDIO DE AGRIMENSURA JCF")
  p.setFont("Helvetica", 10)
  p.drawString(50, height - 65, "Ing. Agrim. Julián Cortina Fraysse")
  p.drawString(
      50, height - 80, "Matrícula Profesional AGR. 382 (Neuquén) | CBU / Alias: FILO.RAYO.ABEDUL"
  )
  p.drawString(50, height - 95, "Tel: 299-4116680 | Correo: juliancortinafraysse@gmail.com")

  p.setLineWidth(0.5)
  p.line(50, height - 105, width - 50, height - 105)

  # Fecha y Destinatario
  fecha_actual = datetime.datetime.now().strftime("%d de octubre de 2026")
  p.drawString(50, height - 130, f"Neuquén, {fecha_actual}")
  p.drawString(50, height - 150, f"Sr./Sra.: {cliente}")

  # Asunto / Título
  titulo_tramite = (
      f"Verificación de Estado Parcelario (VEP)"
      if tipo == "VEP"
      else f"Certificado de Deslinde y Amojonamiento (CDA)"
  )
  p.setFont("Helvetica-Bold", 11)
  p.drawString(50, height - 180, f"Ref.: {titulo_tramite}")

  p.setFont("Helvetica", 10)
  p.drawString(
      50,
      height - 200,
      "Me dirijo a Ud. a fin de elevar el presupuesto del trabajo técnico que se detalla a continuación:",
  )

  # Detalle
  p.setFont("Helvetica-Bold", 10)
  p.drawString(
      50,
      height - 230,
      f"● Objeto: {titulo_tramite} de la parcela con Nomenclatura Catastral:",
  )
  p.setFont("Helvetica", 10)
  p.drawString(70, height - 245, f"NC: {nomenclatura}")
  if detalle_extra:
    p.drawString(70, height - 260, f"Ubicación: {detalle_extra}")

  # Honorarios
  p.setFont("Helvetica-Bold", 11)
  p.drawString(50, height - 295, f"Honorarios Profesionales: ${honorarios} + IVA")

  p.setFont("Helvetica-Bold", 10)
  p.drawString(50, height - 325, "Los honorarios incluyen:")
  p.setFont("Helvetica", 10)
  if tipo == "VEP":
    p.drawString(70, height - 345, "• Tramitación de la VEP en la Dirección Provincial de Catastro e Info. Territorial (DPCeIT).")
    p.drawString(70, height - 360, "• Certificado Catastral y sellados provinciales.")
    p.drawString(70, height - 375, "• Materiales y gastos de trabajo técnico y de gabinete.")
  else:
    p.drawString(70, height - 345, "• Relevamiento planimétrico y replanteo perimetral en el terreno.")
    p.drawString(70, height - 360, "• Confección y tramitación del CDA ante la Municipalidad.")
    p.drawString(70, height - 375, "• Materiales, estacas y gastos operativos de campaña.")

  # Condiciones de pago y plazos
  p.setFont("Helvetica-Bold", 10)
  p.drawString(50, height - 410, "Formas de pago y Plazos:")
  p.setFont("Helvetica", 10)
  if tipo == "VEP":
    p.drawString(
        70,
        height - 430,
        "• Pago único al contado contra entrega de la VEP registrada en la DPCeIT.",
    )
  else:
    p.drawString(
        70,
        height - 430,
        "• Pago único al contado contra registración del CDA en la Municipalidad.",
    )
  p.drawString(70, height - 445, "• Plazo estimado: 15 a 30 días hábiles (según celeridad del organismo público).")

  # Firma final
  p.drawString(50, height - 510, "Sin otro particular, saluda a Ud. atentamente,")
  p.setFont("Helvetica-Bold", 10)
  p.drawString(50, height - 550, "Ing. Agrim. Julián Cortina Fraysse")
  p.drawString(50, height - 565, "Matrícula Profesional AGR. 382")

  p.showPage()
  p.save()
  buffer.seek(0)
  return buffer


# =====================================================================
# INTERFAZ MÓVIL PARA ANDROID (PWA UI)
# =====================================================================


@app.get("/", response_class=HTMLResponse)
def home():
  return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Estudio JCF - Catastro Nqn</title>
        <style>
            body { font-family: system-ui, sans-serif; background: #f4f6f9; margin: 0; padding: 15px; color: #333; }
            .container { max-width: 500px; margin: auto; background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
            h2 { color: #1a365d; text-align: center; font-size: 20px; margin-bottom: 5px; }
            .subtitle { text-align: center; color: #718096; font-size: 13px; margin-bottom: 20px; }
            label { font-weight: bold; font-size: 14px; display: block; margin-top: 15px; }
            input, select { width: 100%; padding: 12px; margin-top: 5px; border: 1px solid #cbd5e0; border-radius: 8px; font-size: 16px; box-sizing: border-box; }
            button { width: 100%; background: #2b6cb0; color: white; border: none; padding: 14px; border-radius: 8px; font-size: 16px; font-weight: bold; margin-top: 20px; cursor: pointer; }
            button:active { background: #2c5282; }
            .result-card { background: #e2e8f0; padding: 15px; border-radius: 8px; margin-top: 20px; display: none; }
            .result-card p { margin: 8px 0; font-size: 14px; }
            .btn-map { background: #319795; text-decoration: none; display: block; text-align: center; color: white; padding: 10px; border-radius: 6px; margin-top: 10px; font-weight: bold; }
            .actions { display: flex; gap: 10px; margin-top: 15px; }
            .btn-vep { background: #dd6b20; flex: 1; padding: 12px; color: white; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; }
            .btn-cda { background: #38a169; flex: 1; padding: 12px; color: white; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Estudio JCF - Catastro Nqn</h2>
            <div class="subtitle">Consulta ITC y Generador de Presupuestos</div>
            
            <label>Nomenclatura Catastral (15 dígitos):</label>
            <input type="text" id="nomenclatura" placeholder="Ej: 092007835750000" value="092007835750000">
            
            <button onclick="consultarITC()">Consultar Parcelas en ITC</button>
            
            <div id="resultado" class="result-card">
                <p><b>Ubicación:</b> <span id="res-ubicacion"></span></p>
                <p><b>Lote / Manzana:</b> Lote <span id="res-lote"></span> - Mz <span id="res-mz"></span></p>
                <p><b>Sup. Mensura:</b> <span id="res-sup"></span> m²</p>
                <p><b>Plano Antecedente:</b> <span id="res-antecedente" style="color: #b7791f; font-weight: bold;"></span></p>
                
                <a id="link-map" href="#" target="_blank" class="btn-map">📍 Abrir en Google Maps</a>

                <label style="margin-top: 15px;">Nombre del Comitente / Cliente:</label>
                <input type="text" id="cliente" placeholder="Ej: Juan Pérez">

                <div style="display: flex; gap: 10px; margin-top: 10px;">
                    <div style="flex:1;">
                        <label>Honorarios VEP ($):</label>
                        <input type="text" id="monto-vep" value="500.000">
                    </div>
                    <div style="flex:1;">
                        <label>Honorarios CDA ($):</label>
                        <input type="text" id="monto-cda" value="450.000">
                    </div>
                </div>

                <div class="actions">
                    <button class="btn-vep" onclick="generarPDF('VEP')">📄 Presupuesto VEP</button>
                    <button class="btn-cda" onclick="generarPDF('CDA')">📄 Presupuesto CDA</button>
                </div>
            </div>
        </div>

        <script>
            let datosParcela = {};

            async function consultarITC() {
                const nom = document.getElementById('nomenclatura').value;
                if(!nom) { alert('Ingrese una nomenclatura'); return; }
                
                document.querySelector('button').innerText = 'Consultando ITC...';
                try {
                    let res = await fetch('/api/consultar?nomenclatura=' + nom);
                    let data = await res.json();
                    if(res.ok) {
                        datosParcela = data;
                        document.getElementById('res-ubicacion').innerText = data.ubicacion_loteo;
                        document.getElementById('res-lote').innerText = data.lote;
                        document.getElementById('res-mz').innerText = data.manzana;
                        document.getElementById('res-sup').innerText = data.sup_mensura;
                        document.getElementById('res-antecedente').innerText = data.plano_antecedente;
                        
                        if(data.google_maps_url) {
                            document.getElementById('link-map').href = data.google_maps_url;
                            document.getElementById('link-map').style.display = 'block';
                        } else {
                            document.getElementById('link-map').style.display = 'none';
                        }
                        
                        document.getElementById('resultado').style.display = 'block';
                    } else {
                        alert('Error: ' + data.detail);
                    }
                } catch(e) {
                    alert('Error de conexión con el servidor ITC');
                }
                document.querySelector('button').innerText = 'Consultar Parcelas en ITC';
            }

            function generarPDF(tipo) {
                const cliente = document.getElementById('cliente').value || 'Cliente';
                const monto = tipo === 'VEP' ? document.getElementById('monto-vep').value : document.getElementById('monto-cda').value;
                const url = `/api/presupuesto?tipo=${tipo}&cliente=${encodeURIComponent(cliente)}&nomenclatura=${datosParcela.nomenclatura}&honorarios=${monto}&ubicacion=${encodeURIComponent(datosParcela.ubicacion_loteo)}`;
                window.open(url, '_blank');
            }
        </script>
    </body>
    </html>
    """


@app.get("/api/consultar")
def api_consultar(nomenclatura: str):
  return obtener_sesion_y_datos_parcela(nomenclatura)


@app.get("/api/presupuesto")
def api_presupuesto(
    tipo: str,
    cliente: str,
    nomenclatura: str,
    honorarios: str,
    ubicacion: str = "",
):
  pdf_buffer = generar_pdf_presupuesto(
      tipo, cliente, nomenclatura, honorarios, ubicacion
  )
  filename = f"Presupuesto_{tipo}_{nomenclatura}.pdf"
  return StreamingResponse(
      pdf_buffer,
      media_type="application/pdf",
      headers={"Content-Disposition": f"inline; filename={filename}"},
  )