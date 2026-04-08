import unicodedata
import pandas as pd
import re

# les indices des colonnes dans le pdf
_COLONNE_INDICES = dict(
    REGION=0, CIRC_ID=1, CIRC_NAME=2, NB_BV=3, INSCRITS=4, VOTANTS=5,
    TAUX=6, NULS=7, SUFFRAGES=8, BLANCS_NB=9, BLANCS_PCT=10, PARTIS=11, CANDIDATS=12,
    SCORE=13, SCORE_PCT=14, ELU=15
)

## nous permet de verifier les entetes des row dans les tables contre les rows avec les des valeurs
_HEADER_NAMES = {"REGI", "ON", "TOTAL"}


# ══════════════════════════════════════════════════════════════════════════════
# Fonctions de Nettoyage & Normalisation
# ══════════════════════════════════════════════════════════════════════════════
def _fix_spaced_text(text: str) -> str:
    """
    Supprime les espaces entre les lettres tout en essayant 
    de préserver les séparateurs réels (comme les tirets).
    """
    if not text:
        return ""
    
    # 1. On supprime tous les espaces simples entre des lettres isolées
    # Exemple : "A S S A" -> "ASSA"
    text = re.sub(r'(?<=[A-Z])\s(?=[A-Z])', '', text)
    
    # 2. On nettoie les espaces restants autour des tirets ou caractères spéciaux
    text = text.replace(" - ", "-").replace("- ", "-").replace(" -", "-")
    
    return text.strip()


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    
    # Correction des espaces étalés avant la normalisation
    text = _fix_spaced_text(text)
    
    # Normalisation standard (accents et majuscules)
    nfkd_form = unicodedata.normalize('NFKD', str(text))
    res = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    
    return " ".join(res.split()).upper()


def _clean_val(value) -> str:
    """Nettoyage simple sans normalisation complète."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _is_header_row(row:list) -> bool:
    """
    Check if a row is a header row based on the presence of specific keywords.

    Args:
        row (list): A list of strings representing the columns of a row.
    Returns:
        bool: True if the row is a header row, False otherwise.
    """
    val = _clean_val(row[0])
    return any(marker in val for marker in _HEADER_NAMES)


# ══════════════════════════════════════════════════════════════════════════════
#  NETTOYAGE & CONVERSION DES TYPES du data frame
# ══════════════════════════════════════════════════════════════════════════════

def _fr_pct_to_float(series: pd.Series) -> pd.Series:
    """
    Convertit les porcentage en float "35,04%" → 0.3504
    Règle : supprime "%", remplace "," par ".", divise par 100.
    """
    return (
        series
        .str.replace("%", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
        .pipe(pd.to_numeric, errors="coerce")
        .div(100)
    )

def _fr_int(series: pd.Series) -> pd.Series:
    """
    Convertit "1 234" ou "1 234" (espace insécable) → 1234 (Int64 nullable).
    """
    return (
        series
        .str.replace(r"\s+", "", regex=True)
        .str.replace("\u00a0", "", regex=False)
        .str.replace("\u202f", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        .astype("Int64")
    )


# ══════════════════════════════════════════════════════════════════════════════
#  afficahge d'un résumé lisible des données extraites
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(df: pd.DataFrame) -> None:
    """Affiche un résumé lisible des données extraites."""

    sep = "═" * 60
    print(f"\n{sep}")
    print("  RÉSUMÉ — EDAN 2025 Résultats Nationaux")
    print(sep)
    print(f"  Candidats total          : {len(df):>8,}")
    print(f"  Élus (députés)           : {df['elu'].sum():>8,}")
    print(f"  Circonscriptions         : {df['circonscription_id'].nunique():>8,}")
    print(f"  Régions                  : {df['region'].nunique():>8,}")
    print(f"  Inscrits total           : {df.groupby('circonscription_id')['inscrits'].first().sum():>8,}")
    print(f"  Votants total            : {df.groupby('circonscription_id')['votants'].first().sum():>8,}")
    print(f"  Participation moyenne    : {df.groupby('circonscription_id')['taux_participation'].first().mean():.1%}")

    print(f"\n  {'PARTI':<25} {'CANDIDATS':>9} {'ÉLUS':>6} {'VOTES TOTAL':>12}")
    print(f"  {'-'*25} {'-'*9} {'-'*6} {'-'*12}")

    summary = (
        df.groupby("parti", dropna=False)
        .agg(nb_candidats=("candidat","count"), nb_elus=("elu","sum"), total_votes=("score","sum"))
        .sort_values("nb_elus", ascending=False)
        .head(12)
    )
    for parti, row in summary.iterrows():
        print(f"  {str(parti):<25} {int(row['nb_candidats']):>9,} {int(row['nb_elus']):>6,} {int(row['total_votes'] or 0):>12,}")

    print(sep + "\n")