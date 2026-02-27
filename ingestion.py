##################################################
# Ingestion script for the data pipeline 
##################################################



## import libraries 
import pandas as pd
from ETL_fonctions.fonctions_ingest import _COLONNE_INDICES, _HEADER_NAMES, _normalize_text, _clean_val, _is_header_row, _fr_pct_to_float, _fr_int , print_summary
import pdfplumber

import argparse
import logging


from pathlib import Path
from ETL_fonctions.fonctions_ingest import * 
################################
# set up logging
###############################
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)-8s - %(message)s',
    datefmt='%H:%M:%S')

log = logging.getLogger(__name__)

################################
# extraction de pdf 
###############################

def extract_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Extracts data from a PDF file and returns it as a DataFrame.
    
    Args:
        pdf_path (str): The path to the PDF file.

    Returns:
        pd.DataFrame: A DataFrame containing the extracted data.
    """
    pdf_path = str(pdf_path)
    log.info(f"Extracting data from PDF: {pdf_path}")
    
    records = []

    # Initialisation du contexte pour stocker les valeurs extraites
    ctx = dict(
        region="", circ_id="", circ_name="",
        nb_bv="", inscrits="", votants="", taux_de_part="",
        nuls="", suffrages="", blancs_nb="", blancs_pct="",
    )


    try:

        with pdfplumber.open(pdf_path) as pdf:
            log.info("PDF ouvert — %d pages détectées", len(pdf.pages))

            for page_num, page in enumerate(pdf.pages, start=1):

                tables = page.extract_tables()
                
                if not tables:
                    log.info(f"No tables found on page {page_num }")
                    continue

                for row in tables[0]:
                    # Normalise à 16 colonnes
                    row = list(row) + [None] * (16 - len(row))

                    
                    if _is_header_row(row):
                        log.info(f"Header row detected on page {page_num}")
                        continue
                    
                    #Mise à jour du contexte 
                    cid_raw = _clean_val(row[_COLONNE_INDICES["CIRC_ID"]])
                    if cid_raw:
                        ctx["circ_id"] = cid_raw  
                        #on met a jour le contexte de la circonscription , pour les lignes suivantes , jusqu'à ce qu'on trouve une nouvelle circonscription ou une nouvelle region
                        for key in ["nb_bv", "inscrits", "votants", "taux_de_part", "nuls", "suffrages", "blancs_nb", "blancs_pct"]:
                            ctx[key] = ""  # reset des stats pour la nouvelle circonscription 

                    # extraction et normalisation des colonnes de region    
                    reg_row = _clean_val(row[_COLONNE_INDICES["REGION"]])
                    if reg_row:
                        # le texte de region est vertical dans le pdf 
                        ctx["region"] = _normalize_text(reg_row.replace("\n", " "))
                    

                    cnm_raw = _clean_val(row[_COLONNE_INDICES["CIRC_NAME"]])
                    if cnm_raw:
                        ctx["circ_name"] = cnm_raw

                    # extraction des stats ( si elles sont présentes )
                    if _clean_val(row[_COLONNE_INDICES["NB_BV"]]):
                        # NB_BV, INSCRITS, VOTANTS, TAUX, NULL, SUFFRAGES, BLANCS_NB, BLANCS_PCT
                        ctx["nb_bv"] = _clean_val(row[_COLONNE_INDICES["NB_BV"]])
                        ctx["inscrits"] = _clean_val(row[_COLONNE_INDICES["INSCRITS"]])
                        ctx["votants"] = _clean_val(row[_COLONNE_INDICES["VOTANTS"]])
                        ctx["taux_de_part"] = _clean_val(row[_COLONNE_INDICES["TAUX"]])
                        ctx["nuls"] = _clean_val(row[_COLONNE_INDICES["NULS"]])
                        ctx["suffrages"] = _clean_val(row[_COLONNE_INDICES["SUFFRAGES"]])
                        ctx["blancs_nb"] = _clean_val(row[_COLONNE_INDICES["BLANCS_NB"]])
                        ctx["blancs_pct"] = _clean_val(row[_COLONNE_INDICES["BLANCS_PCT"]])

                    # maintenant le parti et le candidat
                    part_raw = _clean_val(row[_COLONNE_INDICES["PARTIS"]])
                    cand_raw = _clean_val(row[_COLONNE_INDICES["CANDIDATS"]])
                    if part_raw or cand_raw:
                        records.append({
                            "region": ctx["region"],
                            "circ_id": ctx["circ_id"],
                            "circ_name": ctx["circ_name"],
                            "nb_bv": ctx["nb_bv"],
                            "inscrits": ctx["inscrits"],
                            "votants": ctx["votants"],
                            "taux_de_part": ctx["taux_de_part"],
                            "nuls": ctx["nuls"],
                            "suffrages": ctx["suffrages"],
                            "blancs_nb": ctx["blancs_nb"],
                            "blancs_pct": ctx["blancs_pct"],
                            "parti": part_raw,
                            "candidat": cand_raw,
                            "score": _clean_val(row[_COLONNE_INDICES["SCORE"]]),
                            "score_pct": _clean_val(row[_COLONNE_INDICES["SCORE_PCT"]]),
                            "elu":       _clean_val(row[_COLONNE_INDICES["ELU"]]) == "ELU(E)"
                        })

            log.info(f"Extraction completed — {len(records)} records extracted corresponding to the 35 pages of the PDF.")

        return pd.DataFrame(records)      

    except Exception as e:
        log.error(f"Error processing PDF: {e}")

################################
# mise en forme du dataframe, nettoyage et normalisation des données
###############################
def transform_data(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    convertit les colonnes brutes en types Python/pandas propres.

    Contexte (str)  : region, circonscription_id, circonscription_name, parti, candidat
    Entiers (Int64) : nb_bureaux_vote, inscrits, votants, bulletins_nuls,
                      suffrages_exprimes, bulletins_blancs_nb, score
    Flottants [0,1] : taux_participation, bulletins_blancs_pct, score_pct
    Booléen         : elu

    """
    df = df_raw.copy()

    df = df.rename(columns={
        "circ_id": "circonscription_id",
        "circ_name": "circonscription_name"
    })

    # ── Colonnes entières ─────────────────────────────────────────────────
    int_cols = {
        "nb_bureaux_vote":   "nb_bv",
        "inscrits":          "inscrits",
        "votants":           "votants",
        "bulletins_nuls":    "nuls",
        "suffrages_exprimes":"suffrages",
        "bulletins_blancs_nb":"blancs_nb",
        "score":             "score",
    }
    for col_out, col_in in int_cols.items():
        df[col_out] = _fr_int(df[col_in])

    # ── Colonnes pourcentages → float [0, 1] ─────────────────────────────
    pct_cols = {
        "taux_participation":   "taux_de_part",
        "bulletins_blancs_pct": "blancs_pct",
        "score_pct":            "score_pct",
    }
    for col_out, col_in in pct_cols.items():
        df[col_out] = _fr_pct_to_float(df[col_in])

    # ── Ordre logique des colonnes ────────────────────────────────────────
    ordered = [
        "region", "circonscription_id", "circonscription_name",
        "nb_bureaux_vote", "inscrits", "votants", "taux_participation",
        "bulletins_nuls", "suffrages_exprimes",
        "bulletins_blancs_nb", "bulletins_blancs_pct",
        "parti", "candidat", "score", "score_pct", "elu",
    ]
    df = df[ordered]

    log.info("Nettoyage terminé — shape finale : %s", df.shape)

    # ── Rapport qualité ───────────────────────────────────────────────────
    nulls = df.isnull().sum()
    nulls = nulls[nulls > 0]
    if not nulls.empty:
        log.warning("Valeurs nulles détectées :\n%s", nulls.to_string())
        # Affiche les 5 premières lignes où 'inscrits' est null
        if "inscrits" in nulls:
            bad_rows = df[df["inscrits"].isnull()].head(30)
            log.warning("Exemples de lignes avec 'inscrits' null :\n%s", bad_rows[["region", "circonscription_name", "parti", "candidat"]].to_string())

    return df


################################
# saving the cleaned data to a parquet file, csv file  
###############################

def save_data(df: pd.DataFrame, output_dir = "dataset" ) -> None:
    """
    Sauvegarde le DataFrame nettoyé au format Parquet et CSV.

    compression Snappy pour Parquet.

    Args:
        df (pd.DataFrame): Le DataFrame à sauvegarder.
        output_dir (str): Le répertoire de sortie pour les fichiers sauvegardés.
    """
    output_dir = Path(output_dir)

    parquet_path = output_dir / "edan_2025_resultats.parquet"
    
    df.to_parquet(
        parquet_path,
        engine="pyarrow",
        compression="snappy",
        index=False,
    )

    size_kb = parquet_path.stat().st_size / 1024
    log.info("Parquet sauvegardé → %s  (%.1f KB)", parquet_path, size_kb)
    log.info(f"Data saved to Parquet: {parquet_path}")


    csv_path = output_dir / "edan_2025_resultats.csv"
    df.to_csv(csv_path, index=False)
    log.info(f"Data saved to CSV: {csv_path}")



################################
# lauching 
###############################

pdf_path = r"dataset\EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf"
df = extract_pdf(pdf_path)

print("\n=== Aperçu des données extraites (raw) ===")
print(df.head(20))

df_clean = transform_data(df)

print("\n=== Aperçu des données transformées (clean) ===")
print_summary(df_clean)

print("\n=== Sauvegarde des données nettoyées ===")

save_data(df_clean)

