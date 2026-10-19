import streamlit as st
import requests

st.set_page_config(
    page_title="Estudio JCF - Catastro Nqn", page_icon="📐", layout="centered"
)

st.markdown("<h1 style='text-align: center; color: #1a365d;'>Estudio JCF - Catastro Nqn</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #718096;'>Consulta ITC y Generador de Presupuestos VEP / CDA</p>", unsafe_allow_html=True)

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
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

        parcela = next((p for p in datos_lista if p.get("Estado") == "A"), datos_lista[0])
        object_id = parcela.get("ObjectId")
        service_id = parcela.get("ServiceId", 7)

        desc_catastro = f"{parcela.get('UbicacionLoteo', '')} {parcela.get('Tramite', '')} {parcela.get('DenominacionLote', '')}".upper()
        es_rui_rue = "RUI" in desc_catastro or "RUE" in desc_catastro
        tramite = parcela.get("Tramite") or "Sin antecedente"

        lat, lon = None, None
        if object_id:
            url_geo = f"{ITC_BASE}/site/ITC.Services/proxy.ashx?{ITC_BASE}/server/rest/services/EditFeatures/FeatureServer/{service_id}/query?f=json&where=ObjectId%20%3D%20{object_id}&returnGeometry=true&outSR=4326&outFields=*"
            r_geo = session.get(url_geo, headers=headers, timeout=10)
            geo_json = r_geo.json()
            features = geo_json.get("features", [])
            if features and "geometry" in features[0]:
                rings = features[0]["geometry"].get("rings", [[]])[0]
                if rings:
                    lon = sum(pt[0] for pt in rings) / len(rings)
                    lat = sum(pt[1] for pt in rings) / len(rings)

        resultado = {
            "nomenclatura": parcela.get("Nomenclatura"),
            "plano_antecedente": tramite,
            "ubicacion_loteo": parcela.get("UbicacionLoteo") or "-",
            "lote": parcela.get("DenominacionLote") or "-",
            "manzana": parcela.get("DenominacionManzana") or "-",
            "sup_mensura": parcela.get("SupMensura") or 0,
            "es_rui_rue": es_rui_rue,
            "google_maps_url": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat and lon else "",
            "itc_portal_url": f"{ITC_BASE}/portal/apps/webappviewer/index.html?id=63f0c3f9a047425280951f418621171f&nomenclatura={nomen_limpia}"
        }
        return resultado, None
    except Exception as e:
        return None, f"Error de conexión con el servidor: {str(e)}"

# --- INTERFAZ PRINCIPAL ---
nomenclatura_input = st.text_input("Nomenclatura Catastral (15 dígitos):", value="092007849780000")

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
    st.markdown(f"**Plano Antecedente (Mensura):** :orange[**{p['plano_antecedente']}**]")
    
    es_rui = p.get("es_rui_rue", False)
    st.markdown(f"**Clasificación RUI / RUE (Catastro):** `{'Sí (Rural)' if es_rui else 'No (Urbana / Estándar)'}`")

    col_link1, col_link2 = st.columns(2)
    with col_link1:
        if p.get("itc_portal_url"):
            st.markdown(f"<a href='{p['itc_portal_url']}' target='_blank' style='display:block; text-align:center; background:#2b6cb0; color:white; padding:10px; border-radius:6px; text-decoration:none; font-weight:bold;'>🗺️ Ver Plano / Ficha en Catastro Nqn</a>", unsafe_allow_html=True)
    with col_link2:
        if p.get("google_maps_url"):
            st.markdown(f"<a href='{p['google_maps_url']}' target='_blank' style='display:block; text-align:center; background:#319795; color:white; padding:10px; border-radius:6px; text-decoration:none; font-weight:bold;'>📍 Ver ubicación en Google Maps</a>", unsafe_allow_html=True)

    st.divider()
    st.subheader("Armar Presupuesto Profesional Editable")
    cliente = st.text_input("Nombre del Comitente / Cliente:", value="Juan Pérez")

    col1, col2 = st.columns(2)
    with col1:
        monto_vep = st.text_input("Honorarios VEP ($):", value="500.000")
    with col2:
        monto_cda = st.text_input("Honorarios CDA ($):", value="450.000")

    condicion_pago = "50% anticipo al inicio / 50% saldo contra entrega" if es_rui else "Pago único contra entrega"
    st.info(f"📋 **Condición de pago automática aplicable:** {condicion_pago}")

    if st.button("📄 Mostrar Resumen de Presupuesto para Enviar"):
        st.markdown("---")
        st.markdown(f"### Propuesta de Honorarios - Estudio JCF")
        st.markdown(f"**Comitente:** {cliente}")
        st.markdown(f"**Nomenclatura:** {p['nomenclatura']}")
        st.markdown(f"**Ubicación:** {p['ubicacion_loteo']}")
        st.markdown(f"**Plano Antecedente:** {p['plano_antecedente']}")
        st.markdown(f"**Honorarios VEP:** ${monto_vep} + IVA")
        st.markdown(f"**Honorarios CDA:** ${monto_cda} + IVA")
        st.markdown(f"**Forma de Pago:** {condicion_pago}")
        st.markdown("---")
        st.success("¡Resumen listo para copiar, enviar por WhatsApp o mail al comitente!")
