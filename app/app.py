#---------------- 
# imports
#-----------------
# Assure que src/ est importable
import streamlit as st
import pandas as pd
from src.agent.Agent import ElectionSQLAgent

#---------------- 
# streamlit page configuration
#-----------------

st.set_page_config(
    page_title="CI Election Chatbot 2025",
    page_icon="🗳️",
    layout="wide"
)

st.title("Côte d'Ivoire 2025 — Election Chatbot")
st.markdown("""
    Posez vos questions sur les résultats, la participation ou demandez des classements.
    *en manque d'inspiration regardez les exemples  dans la sidebar !*""")

st.caption("Assistant IA entraîné sur les données électorales de Côte d'Ivoire 2025")

#------------------------
#  sidebar 
#-----------------------

#-----------------
# initialize agent
#-----------------
@st.cache_resource
def init_agent():
    return ElectionSQLAgent()

agent = init_agent()

#------------------------
#  sidebar 
#-----------------------

with st.sidebar:
    st.header(" Options")

    show_sql = st.toggle("Afficher la SQL finale", value=True)
    show_proposed_sql = st.toggle("Afficher la SQL proposée", value=False)
    show_debug = st.toggle("Afficher les détails d'erreur", value=True)

    chart_mode = st.selectbox(
        "Graphique",
        [ "None", "Auto", "Bar", "Line"],
        index=0
    )

    st.divider()
    st.subheader(" Exemples de questions")
    st.markdown(
        """
       
- Top 10 candidats par score_pct dans la circonscription 001
- Liste des vainqueurs (elu=true) par circonscription
- Classement des partis par score total
- Taux de participation moyen par région 
        """.strip()
    )

    try:
        _meta = agent.run_query("SELECT * FROM mart.vw_circonscriptions LIMIT 1")
        st.info(f"Tables indexées : {len(_meta['columns'])} colonnes")
    except Exception:
        st.warning(" Impossible de lire les métadonnées (DB non prête ?)")


    st.divider()
    if st.button("Effacer l'historique"):
        st.session_state.messages = []
        st.rerun()




#-----------------
# session state
#-----------------

if "messages" not in st.session_state:
    st.session_state.messages = []

## gestion de messages et historique 
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        if message.get("df") is not None:
            st.dataframe(message["df"])

#-----------------
# chart et graphique builder
#-----------------

def build_chart(df: pd.DataFrame, mode: str):
    """
    Construit un graphique simple et sûr (Streamlit natif).
    Auto:
      - Si df a 1 col cat + 1 col numeric => bar
      - Si df a seulement numeric => bar simple
    """
    if df.empty or mode == "None":
        return

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]

    if mode == "Auto":
        if len(numeric_cols) >= 1 and len(non_numeric_cols) >= 1:
            x = non_numeric_cols[0]
            y = numeric_cols[0]
            chart_df = df[[x, y]].copy().set_index(x)
            st.bar_chart(chart_df)
            return
        if len(numeric_cols) >= 1:
            st.bar_chart(df[numeric_cols[:1]])
            return
        st.info("Pas assez de colonnes numériques pour tracer un graphique.")
        return

    if mode == "Bar":
        if len(numeric_cols) >= 1 and len(non_numeric_cols) >= 1:
            x = non_numeric_cols[0]
            y = numeric_cols[0]
            st.bar_chart(df[[x, y]].set_index(x))
        elif len(numeric_cols) >= 1:
            st.bar_chart(df[numeric_cols[:1]])
        else:
            st.info("Aucune colonne numérique pour un bar chart.")
        return

    if mode == "Line":
        if len(numeric_cols) >= 1:
            st.line_chart(df[numeric_cols[:1]])
        else:
            st.info("Aucune colonne numérique pour un line chart.")
        return

#-----------------
# gestion de saisie utilisateur
#-----------------

prompt = st.chat_input("Pose ta question sur les résultats (ex: 'Top 10 candidats par score_pct en 001')")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Réflexion en cours..."):
            out = {
                "response": "Aucune réponse générée.",
                "error": None,
                "df": None,
                "chart": None,
                "ok": False
            }
            try:
                out = agent.run_query(prompt)
            except Exception as e:
                st.error("Erreur interne (agent). Vérifie GOOGLE_API_KEY et les prompts.")
                if show_debug:
                    st.exception(e)
                out = {
                    "response": "Désolé, une erreur est survenue. Vérifie ta clé API et les prompts.",
                    "error": str(e),
                    "df": None,
                    "chart": None,
                    "ok": False
                }

        if not out.get("ok"):
            st.error(" Erreur du chatbot — impossible de répondre à la question.")
            st.markdown(f"**Détail bug :** {out.get('error', 'erreur inconnue')}")
            st.session_state.messages.append({
                "role": "assistant",
                "content": out.get("response", "Erreur."),
                "df": None,    
                "chart": None
            })
        else:
            if show_proposed_sql:
                st.markdown("**SQL proposée (première génération)**")
                st.code(out.get("proposed_sql", ""), language="sql")

            if show_sql:
                st.markdown("**SQL exécutée (après validation/repair)**")
                st.code(out.get("final_sql", ""), language="sql")

            # construction du DataFrame
            cols = out["columns"]
            rows = out["rows"]
            df = pd.DataFrame(rows, columns=cols)

            # Rendu résultat
            st.success(f" {len(df)} ligne(s) retournée(s) — attempts: {out.get('attempts', 0)}")
            st.dataframe(df, use_container_width=True)

            # Chart c'est une option 
            st.markdown("### Visualisation")
            build_chart(df, chart_mode)

            st.session_state.messages.append({
                "role": "assistant",
                "content": (
                    "Résultats disponibles.\n\n"
                    + (f"SQL finale:\n```sql\n{out.get('final_sql','')}\n```" if show_sql else "")
                ),
                "df": df,
            })

# Footer
st.divider()
st.caption("By Jasen using Google Gemini API")