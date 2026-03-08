# =========================
# QUICK TEST
# =========================


import Agent


if __name__ == "__main__":
    agent = Agent.ElectionSQLAgent()

    tests = [
        "Top 10 candidats par score_pct dans la circonscription 001",
        "Taux de participation moyen par région",
        "Liste des élus (elu=true) pour la circonscription 001",
        "Tous les résultats de la circonscription 001",
    ]

    import time
    for question in tests:
        out = agent.run_query(question)
        print(f"\nQUESTION : {question}")
        print(f"OK : {out['ok']} | Tentatives : {out.get('attempts')}")
        if out["ok"]:
            print(f"SQL final : {out['final_sql']}")
            print(f"Lignes    : {len(out['rows'])}")
        else:
            print(f"Erreur : {out['error']}")
        time.sleep(15)