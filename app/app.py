"""Streamlit client of the same RAG pipeline used by the experiment runner."""
import json
import os

import pandas as pd
import streamlit as st

from src.agent.cache import ResultCache
from src.agent.llm import llm_descriptor
from src.retrieval.corpus import PDF_PATH
from src.retrieval.pipeline import APP_CONDITIONS, ElectionRAG, rag_fingerprint

st.set_page_config(page_title="CI Election Chatbot 2025", page_icon="🗳️", layout="wide")
st.title("Côte d'Ivoire 2025 — Election Chatbot")
st.caption("Chaque question est indépendante. Les résultats concernent des candidatures/listes, pas un décompte de sièges.")

LABELS = {'A': 'A · Catalogue complet', 'B': 'B · Recherche BM25', 'C': 'C · Recherche sémantique',
          'D': 'D · Recherche hybride', 'F1': 'F1 · Agent (1 recherche supplémentaire)',
          'F3': 'F3 · Agent (3 recherches supplémentaires)'}
if 'result_cache' not in st.session_state:
    st.session_state.result_cache = ResultCache()
if 'messages' not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("Options")
    default = os.getenv('RAG_CONDITION', 'B')
    condition = st.selectbox('Mode RAG', APP_CONDITIONS, format_func=LABELS.get,
                             index=APP_CONDITIONS.index(default) if default in APP_CONDITIONS else 0)
    search_only = st.toggle('Recherche dans les sources uniquement', value=False,
                            help='Recherche locale, sans appel Gemini.')
    show_sql = st.toggle('Afficher la SQL finale', value=True)
    show_proposed_sql = st.toggle('Afficher la SQL proposée', value=False)
    show_debug = st.toggle("Afficher les détails d'erreur", value=True)
    chart_mode = st.selectbox('Graphique', ['Auto', 'Bar', 'Line', 'None'])
    st.divider()
    st.markdown('''**Exemples**
- Quel est le taux de participation national ?
- Qui a gagné dans la circonscription 001 ? Donne la page source.
- Classement des partis par score total
- Taux de participation dans le Haut-Sassandra''')
    if st.button("Effacer l'historique et le cache"):
        st.session_state.messages = []
        st.session_state.result_cache.clear()
        st.rerun()
    if PDF_PATH.exists():
        st.download_button('Télécharger le PDF source', PDF_PATH.read_bytes(),
                           file_name=PDF_PATH.name, mime='application/pdf')


@st.cache_resource
def init_rag(version, condition):
    return ElectionRAG(condition)


try:
    version = rag_fingerprint(condition)
    rag = init_rag(version, condition)
except Exception as exc:
    st.error(f'Initialisation du RAG : {exc}')
    st.info('Préparer les données : python -m src.retrieval.cli prepare')
    st.stop()


def render_sources(sources, dataset_source=None):
    with st.expander(f'Contexte retrouvé · {len(sources)} fiches'):
        st.caption('Ces fiches guident la génération SQL. Les totaux affichés sont calculés sur la base complète ; '
                   'les pages des fiches ne constituent pas une preuve de tous les agrégats.')
        for card in sources:
            st.markdown(f"**{card['title']}**")
            st.caption(f"{card['id']} · {card.get('provenance', '')}")
            st.text(card['text'])
            if card.get('source_pages'):
                st.caption('Pages PDF de cette circonscription : ' + ', '.join(map(str, card['source_pages'])))
        if dataset_source:
            st.caption(f"Source : {dataset_source['file']} · SHA-256 {dataset_source['sha256']}")


def render_result(out):
    if out.get('status') == 'search':
        st.info('Recherche locale terminée — aucun appel Gemini.')
    elif out.get('status') in {'unsupported', 'needs_clarification'}:
        st.info(out['response'])
    elif not out.get('ok'):
        st.error('Impossible de répondre à cette question.')
        if out.get('error_type') == 'FallbackBudgetExceeded':
            st.warning('Gemini est indisponible et le plafond d’appels Claude (payants) est atteint.')
        if show_debug:
            st.text(f"{out.get('stage', 'application')} : {out.get('error', 'Erreur inconnue')}")
    else:
        df = pd.DataFrame(out['rows'], columns=out['columns'])
        served = ', '.join(out.get('served_by') or []) or 'modèle'
        st.success(f"{len(df)} ligne(s) · {out.get('api_calls', 0)} appel(s) · {served}"
                   + (' · réponse en cache' if out.get('cache_hit') else ''))
        st.dataframe(df, width='stretch')
        if 'source_page' in df.columns and not df.empty:
            st.caption('Pages PDF retournées par SQL : ' + ', '.join(map(str, sorted(set(df['source_page'].dropna())))))
        if show_proposed_sql:
            st.code(out.get('proposed_sql') or '', language='sql')
        if show_sql:
            st.code(out.get('final_sql') or '', language='sql')
        build_chart(df, chart_mode)
    if out.get('sources'):
        render_sources(out['sources'], out.get('dataset_source'))
    if out.get('fallback_used'):
        st.warning('Réponse produite par le modèle de secours Claude (API payante) : Gemini était indisponible.')
    if out.get('condition'):
        st.caption(f"Mode {out['condition']} · corpus {out.get('corpus_version', '')[:12]}")

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

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        if 'result' in message:
            render_result(message['result'])
        else:
            st.markdown(message['content'])

prompt = st.chat_input('Pose ta question sur les résultats électoraux')
if prompt:
    st.session_state.messages.append({'role': 'user', 'content': prompt})
    with st.chat_message('user'):
        st.markdown(prompt)
    with st.chat_message('assistant'):
        with st.spinner('Recherche dans les sources…'):
            try:
                if search_only:
                    out = {'ok': True, 'status': 'search', 'sources': rag.search(prompt),
                           'condition': condition, 'corpus_version': rag.corpus_version}
                else:
                    out = st.session_state.result_cache.query(prompt, version, rag.run_query)
            except Exception as exc:
                out = {'ok': False, 'stage': 'application', 'error': str(exc)}
        render_result(out)
        st.session_state.messages.append({'role': 'assistant', 'result': out})
st.divider()
_llm = llm_descriptor()
st.caption(f"Gemini ({_llm['primary'] or 'non configuré'})"
           + (f" · secours Claude ({_llm['fallback']}, payant, plafonné)" if _llm['fallback'] else '')
           + ' · DuckDB · RAG local — configurations partagées avec le banc d’évaluation')
