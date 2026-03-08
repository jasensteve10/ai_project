###########################################
# Building the Data base schema 
# avec une couche brute et une couche mart 
###########################################

import duckdb as db 
from pathlib import Path
import os 

#------------------------------------------
# Définition des chemins 
#------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

PARQUET = PROJECT_ROOT / "dataset" / "clean" / "edan_2025_resultats.parquet"
DB_PATH = PROJECT_ROOT / "dataset" / "db" / "edan_2025.duckdb"

BRUTE_SCHEMA = "brute"
MART_SCHEMA = "mart"

BRUTE_TABLE = "resultats"

VW_CIRCO = "vw_circonscriptions"
VW_CAND = "vw_resultats_candidats"
VW_VAINQUEUR = "vw_vainqueur"

"""
        "region", "circonscription_id", "circonscription_name",
        "nb_bureaux_vote", "inscrits", "votants", "taux_participation",
        "bulletins_nuls", "suffrages_exprimes",
        "bulletins_blancs_nb", "bulletins_blancs_pct",
        "parti", "candidat", "score", "score_pct", "elu"
"""

#------------------------------------------
# main
#------------------------------------------


def main() -> None:
    
    if not PARQUET.exists():
        raise FileNotFoundError(f"Parquet not found: {PARQUET}")


    # Connexion à la base de données
    con = db.connect(DB_PATH)

    #------------------------------------------
    # Création des schémas
    #------------------------------------------
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {BRUTE_SCHEMA}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {MART_SCHEMA}")

    #------------------------------------------
    # Création de la table brute
    #------------------------------------------
    con.execute(f"""
        DROP TABLE IF EXISTS {BRUTE_SCHEMA}.{BRUTE_TABLE};
    """)

    con.execute(f"""
        CREATE TABLE {BRUTE_SCHEMA}.{BRUTE_TABLE} AS
        SELECT *
        FROM read_parquet('{PARQUET}');
    """)
    #------------------------------------------
    # Création des vues , circonscription , candidats , vainqueur
    #------------------------------------------

    ## Vues pour les circonscriptions ( normalisation des données au cas ou )
    con.execute(f"""
        DROP VIEW IF EXISTS {MART_SCHEMA}.{VW_CIRCO};
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW 
        {MART_SCHEMA}.{VW_CIRCO} AS 
        SELECT 
           circonscription_id :: VARCHAR(15) AS circonscription_id, 
           TRIM(REGEXP_REPLACE(ANY_VALUE(circonscription_name), '\s+', ' ', 'g'))::VARCHAR AS circonscription_name,
           ANY_VALUE(region)::VARCHAR AS region,        

           MAX(nb_bureaux_vote)::BIGINT        AS nb_bureaux_vote,
           MAX(inscrits)::BIGINT               AS inscrits,
           MAX(votants)::BIGINT                AS votants,
           MAX(taux_participation)::DOUBLE     AS taux_participation,

           MAX(bulletins_nuls)::BIGINT         AS bulletins_nuls,
           MAX(suffrages_exprimes)::BIGINT     AS suffrages_exprimes,

           MAX(bulletins_blancs_nb)::BIGINT    AS bulletins_blancs_nb,
           MAX(bulletins_blancs_pct)::DOUBLE   AS bulletins_blancs_pct    
        FROM {BRUTE_SCHEMA}.{BRUTE_TABLE}
        GROUP BY circonscription_id;
    """)

    ## Vues pour les candidats
    con.execute(f"""
        DROP VIEW IF EXISTS {MART_SCHEMA}.{VW_CAND};
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW 
        {MART_SCHEMA}.{VW_CAND} AS 
        SELECT 
            region::VARCHAR AS region,
            circonscription_id :: VARCHAR(15) AS circonscription_id, 
            parti::VARCHAR             AS parti,
            candidat::VARCHAR          AS candidat,
            CAST(score AS BIGINT)      AS score,
            CAST(score_pct AS DOUBLE)  AS score_pct,
            elu::BOOLEAN               AS elu
        FROM {BRUTE_SCHEMA}.{BRUTE_TABLE};
    """)

    ## Vues pour vainqueur
    con.execute(f"""
        DROP VIEW IF EXISTS {MART_SCHEMA}.{VW_VAINQUEUR};
    """)

    con.execute(f"""
        CREATE OR REPLACE VIEW 
        {MART_SCHEMA}.{VW_VAINQUEUR} AS 
        SELECT * FROM {BRUTE_SCHEMA}.{BRUTE_TABLE}
        WHERE elu = true;
    """)

    print(" ####### les tables sont créées avec succès ####### ")
    print(f" - Table: {BRUTE_SCHEMA}.{BRUTE_TABLE}")
    print(f" - View : {MART_SCHEMA}.{VW_CIRCO}")
    print(f" - View : {MART_SCHEMA}.{VW_CAND}")
    print(f" - View : {MART_SCHEMA}.{VW_VAINQUEUR}")


    ## show tables
    print(con.execute(f"SHOW TABLES FROM {MART_SCHEMA}").fetchall())

    ## affichange des tables 
    df_brute = con.execute(f"SELECT * FROM {BRUTE_SCHEMA}.{BRUTE_TABLE}").fetchdf()
    df_circo = con.execute(f"SELECT * FROM {MART_SCHEMA}.{VW_CIRCO}").fetchdf()
    df_cand = con.execute(f"SELECT * FROM {MART_SCHEMA}.{VW_CAND}").fetchdf()
    df_vainqueur = con.execute(f"SELECT * FROM {MART_SCHEMA}.{VW_VAINQUEUR}").fetchdf()
    
    print(df_brute.head(5))
    print("#############################")
    print(df_circo.head(5))
    print("#############################")
    print(df_cand.head(5))
    print("#############################")
    print(df_vainqueur.head(5))
    
    #------------------------------------------
    # Fermeture de la connexion
    #------------------------------------------
    con.close()

if __name__ == "__main__":
    main()  
