import streamlit as st

st.set_page_config(
    page_title="Estudio JCF - Catastro Nqn", page_icon="📐", layout="centered"
)
st.title("Estudio JCF - Catastro Nqn")
st.write("¡Aplicación funcionando correctamente!")

nomenclatura = st.text_input(
    "Nomenclatura Catastral:", value="092007835750000"
)
if st.button("Probar"):
  st.success(f"Nomenclatura ingresada: {nomenclatura}")
