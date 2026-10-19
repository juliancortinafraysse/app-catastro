import streamlit as st

st.set_page_config(
    page_title="Estudio JCF - Catastro Nqn", page_icon="📐", layout="centered"
)

st.title("Estudio JCF - Catastro Nqn")
st.success("¡Aplicación en línea y funcionando con éxito!")

# Verificación de controles básicos
nomen = st.text_input(
    "Nomenclatura Catastral (15 dígitos):", value="092007835750000"
)
cliente = st.text_input("Nombre del Comitente:", value="Juan Pérez")
es_rui = st.checkbox("¿Es trámite RUI / RUE? (50% anticipo / 50% saldo)")

if st.button("Simular Consulta y Datos"):
  st.info(f"Nomenclatura cargada: {nomen}")
  st.info(f"Cliente: {cliente}")
  if es_rui:
    st.warning(
        "Forma de pago aplicada: 50% anticipo al inicio / 50% saldo contra"
        " entrega."
    )
  else:
    st.success("Forma de pago aplicada: Pago único contra entrega.")
