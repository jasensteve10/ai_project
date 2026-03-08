"""
Agent Text-to-SQL pour l'analyse des résultats électoraux EDAN 2025.
Architecture : Génération SQL → Validation → Réparation (boucle).
"""

# =========================
# IMPORTS
# =========================
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import duckdb
import sqlglot
from sqlglot import exp
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

# =========================
# CONFIG
# =========================
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH      = PROJECT_ROOT / "dataset" / "db" / "edan_2025.duckdb"
PROMPT_DIR   = PROJECT_ROOT / "src" / "prompts"

ALLOWED_RELATIONS: set[str] = {
    "mart.vw_circonscriptions",
    "mart.vw_resultats_candidats",
    "mart.vw_vainqueur",
}

BLOCKED_KEYWORDS: set[str] = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "copy", "attach", "detach", "pragma", "call", "export", "import",
    "merge", "grant", "revoke", "vacuum",
}

DEFAULT_LIMIT     = 100
MAX_LIMIT         = 500
MAX_REPAIR_ATTEMPTS = 3

# Registre explicite : clé métier → fichier sur disque
_PROMPT_REGISTRY: dict[str, str] = {
    "sql_system": "sql_system.md",
    "sql_repair": "sql_repair.md",
}

# =========================
# PROMPT LOADER
# =========================
def _load_all_prompts() -> dict[str, str]:
    """
    Charge tous les prompts enregistrés une seule fois au démarrage.
    Lève FileNotFoundError immédiatement si un fichier est manquant.
    """
    store: dict[str, str] = {}
    for key, filename in _PROMPT_REGISTRY.items():
        path = PROMPT_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt manquant : {path}")
        store[key] = path.read_text(encoding="utf-8")
    return store

PROMPT_STORE: dict[str, str] = _load_all_prompts()


# =========================
# SQL VALIDATION (helpers privés)
# =========================
def _has_multiple_statements(sql: str) -> bool:
    s = sql.strip()
    return ";" in s[:-1] or s.count(";") > 1


def _ensure_limit(sql: str, parsed: exp.Expression) -> str:
    """
    Garantit qu'un LIMIT est présent et <= MAX_LIMIT.
    - Absent   → injecte DEFAULT_LIMIT.
    - Trop grand → plafonne à MAX_LIMIT.
    Reçoit le parsed déjà calculé pour éviter un double parse.
    """
    limit_exp = parsed.args.get("limit")

    if limit_exp is None:
        return f"{sql.rstrip().rstrip(';')} LIMIT {DEFAULT_LIMIT}"

    try:
        lit = limit_exp.expression
        if isinstance(lit, exp.Literal) and lit.is_int and int(lit.this) > MAX_LIMIT:
            limit_exp.set("expression", exp.Literal.number(MAX_LIMIT))
            return parsed.sql(dialect="duckdb")
    except Exception:
        pass

    return sql


def _extract_relations(parsed: exp.Expression) -> List[str]:
    """Extrait les noms de tables/vues depuis un arbre sqlglot déjà parsé."""
    tables = []
    for t in parsed.find_all(exp.Table):
        schema = t.args.get("db")
        tables.append(f"{schema}.{t.this}" if schema else str(t.this))
    return tables


# =========================
# SQL VALIDATION (point d'entrée)
# =========================
def validate_sql(sql: str) -> Tuple[bool, str, str]:
    """
    Valide et sécurise une requête SQL.
    Retourne (ok, sql_corrigé, message_erreur).
    """
    if not sql or not sql.strip():
        return False, sql, "Requête vide."

    raw = sql.strip()

    # 1. Pas de multi-statements
    if _has_multiple_statements(raw):
        return False, raw, "Plusieurs instructions SQL détectées — interdit."

    # 2. Blocage des mots-clés DDL/DML
    lowered = raw.lower()
    for kw in BLOCKED_KEYWORDS:
        if kw in lowered:
            return False, raw, f"Mot-clé interdit détecté : '{kw}'."

    # 3. Parse syntaxique
    try:
        parsed = sqlglot.parse_one(raw, read="duckdb")
    except Exception as e:
        return False, raw, f"Erreur de syntaxe SQL : {e}"

    # 4. SELECT uniquement (WITH ... SELECT et UNION acceptés)
    if not list(parsed.find_all(exp.Select)):
        return False, raw, "Seules les requêtes SELECT sont autorisées."

    # 5. Vérification des relations autorisées
    relations = _extract_relations(parsed)
    normalized = [r.replace('"', "").replace("`", "") for r in relations]

    for r in normalized:
        if r in ALLOWED_RELATIONS:
            continue
        # Tolérance : nom sans schéma → on résout automatiquement si unique
        if "." not in r:
            candidates = [a for a in ALLOWED_RELATIONS if a.endswith(f".{r}")]
            if len(candidates) == 1:
                continue
        return (
            False, raw,
            f"Relation non autorisée : '{r}'. Autorisées : {sorted(ALLOWED_RELATIONS)}",
        )

    # 6. Injection / plafonnement du LIMIT
    fixed = _ensure_limit(raw, parsed)
    return True, fixed, ""


# =========================
# EXÉCUTION SÉCURISÉE
# =========================
def run_safe_sql(sql: str) -> Dict[str, Any]:
    """
    Valide puis exécute la requête en lecture seule.
    Retourne un dict uniforme avec clé 'ok'.
    """
    ok, fixed_sql, err = validate_sql(sql)
    if not ok:
        return {"ok": False, "stage": "validation", "error": err, "sql": sql}

    try:
        with duckdb.connect(DB_PATH, read_only=True) as con:
            cur = con.execute(fixed_sql)
            return {
                "ok":      True,
                "sql":     fixed_sql,
                "columns": [d[0] for d in cur.description],
                "rows":    cur.fetchall(),
            }
    except Exception as e:
        return {"ok": False, "stage": "execution", "error": str(e), "sql": fixed_sql}


# =========================
# AGENT TEXT-TO-SQL
# =========================
class ElectionSQLAgent:
    """
    Agent principal : traduit une question en SQL, valide, exécute,
    et tente de réparer en cas d'échec (jusqu'à MAX_REPAIR_ATTEMPTS fois).
    """

    def __init__(self) -> None:
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0,
        )
        self._system_prompt  = PROMPT_STORE["sql_system"]
        self._repair_tpl     = PROMPT_STORE["sql_repair"]

    # --------------------------------------------------
    # Point d'entrée public
    # --------------------------------------------------
    def run_query(self, question: str) -> Dict[str, Any]:
        """
        Boucle : Génération → Validation → Réparation.
        Retourne un dict uniforme avec clé 'ok'.
        """
        initial_sql  = self._generate_sql(question)
        current_sql  = initial_sql
        last_error   = ""

        for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
            result = run_safe_sql(current_sql)

            if result["ok"]:
                return {
                    "ok":           True,
                    "attempts":     attempt,
                    "proposed_sql": initial_sql,
                    "final_sql":    result["sql"],
                    "columns":      result["columns"],
                    "rows":         result["rows"],
                }

            last_error = f"[{result.get('stage', '?')}] {result['error']}"

            if attempt == MAX_REPAIR_ATTEMPTS:
                break

            current_sql = self._repair_sql(question, current_sql, last_error)

        return {
            "ok":           False,
            "attempts":     attempt,
            "proposed_sql": initial_sql,
            "final_sql":    None,
            "error":        f"Échec après {attempt} tentative(s). Dernière erreur : {last_error}",
        }

    # --------------------------------------------------
    # Méthodes privées LLM
    # --------------------------------------------------
    def _generate_sql(self, question: str) -> str:
        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=question),
        ]
        return self.llm.invoke(messages).content.strip()

    def _repair_sql(self, question: str, bad_sql: str, error: str) -> str:
        repair_msg = self._repair_tpl.format(
            question=question,
            bad_sql=bad_sql,
            error=error,
            relations=", ".join(sorted(ALLOWED_RELATIONS)),
        )
        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=repair_msg),
        ]
        return self.llm.invoke(messages).content.strip()


