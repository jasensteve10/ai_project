# Ingestion script for the data pipeline 

## import libraries 
import pandas as pd
from ETL_fonctions.fonctions_ingest import _COLONNE_INDICES, _clean_val, _is_header_row, _normalize_text, print_summary
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

            for page_num, page in enumerate(pdf.pages):

                tables = page.extract_tables()
                
                if not tables:
                    log.warning(f"No tables found on page {page_num }")
                    continue

                for table in tables:

                    for row in table:
                        print(row)

                    if _is_header_row(row):
                        log.info(f"Header row detected on page {page_num}: {row}")
                        continue

                    reg_row = _clean_val(row[_COLONNE_INDICES["REGION"]])
                    if reg_row:
                        # le texte de region est vertical dans le pdf 
                        ctx["region"] = _normalize_text(reg_row.replace("\n", " "))
                    
                    cid_raw = _clean_val(row[_COLONNE_INDICES["CIRC_ID"]])
                    if cid_raw:
                        ctx["circ_id"] = cid_raw

                    cnm_raw = _clean_val(row[_COLONNE_INDICES["CIRC_NAME"]])
                    if cnm_raw:
                        ctx["circ_name"] = _normalize_text(cnm_raw)

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
                            **ctx,
                            "parti": _normalize_text(part_raw),
                            "candidat": _normalize_text(cand_raw),
                            "score": _clean_val(row[_COLONNE_INDICES["SCORE"]]),
                            "score_pct": _clean_val(row[_COLONNE_INDICES["SCORE_PCT"]]),
                            "elu":       _clean_val(row[_COLONNE_INDICES["ELU"]]) == "ELU(E)"
                        })

            log.info(f"Extraction completed — {len(records)} records extracted corresponding to the 35 pages of the PDF.")

        return pd.DataFrame(records)      


    except Exception as e:
        log.error(f"Error processing PDF: {e}")


pdf_path = r"dataset\EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf"
df = extract_pdf(pdf_path)

