"""Independent checks of EDA grain and raw-parser missingness semantics."""
import duckdb
import pytest
pytest.importorskip('matplotlib')
from src.eda.analysis import derive_units, profile_pdf
from src.preprocessing.ingestion import PDF_PATH


def test_raw_structural_blanks_are_counted(extracted):
    pages, raw, totals, _ = profile_pdf(PDF_PATH, extracted)
    counts = raw.set_index('column').raw_empty_slots
    assert counts['region'] > 1000
    assert counts['inscrits'] > 800
    assert counts['candidat'] == 0
    assert pages.default_candidate_rows.sum() == 1124
    assert pages.corrected_candidate_rows.sum() == 1125
    assert totals['score'] == 2913991


def test_region_aggregations_match_independent_sql(extracted, database):
    c, regions, parties = derive_units(extracted)
    with duckdb.connect(str(database), read_only=True) as con:
        rows=con.execute('SELECT region, SUM(votants), SUM(inscrits), SUM(votants)/SUM(inscrits) '
                         'FROM mart.vw_circonscriptions GROUP BY region').fetchall()
    for region, voters, registered, turnout in rows:
        assert regions.loc[region,'voters'] == voters
        assert regions.loc[region,'registered'] == registered
        assert regions.loc[region,'weighted_turnout'] == pytest.approx(turnout)
    assert c.votants.sum()/c.inscrits.sum() == pytest.approx(0.3503619596021538)
    assert parties.votes.sum() == 2913991


def test_single_entry_margin_is_not_fabricated(extracted):
    c, _, _ = derive_units(extracted)
    assert len(c[c.entries==1]) == 11
    assert c.loc[c.entries==1,'margin_votes'].isna().all()
    assert c.loc[c.entries>1,'margin_votes'].notna().all()
    assert c.loc['122','margin_votes'] == 2
    assert c.loc['141','invalid_rate'] == pytest.approx(3423/12179)
