import pandas as pd
import pytest
from src.preprocessing.extract import _normalize_text, _is_header_row
from src.preprocessing.ingestion import build_audit, validate_data, OUTPUT_DIR
from src.preprocessing.schema import build_database


def test_pdf_reconciles_with_national_totals(extracted):
    audit = build_audit(extracted)
    assert audit['rows'] == 1125
    assert audit['constituencies'] == 205
    assert audit['missing_cells'] == 0
    assert audit['id_gaps_within_observed_range'] == []
    assert audit['winning_row_count_distribution'] == {'1': 205}
    assert audit['extracted_totals']['score'] == 2913991
    assert audit['national_total_differences'] == dict.fromkeys(audit['extracted_totals'], 0)


def test_known_extraction_defects(extracted):
    by_id = extracted.set_index('circonscription_id')
    assert set(by_id.loc['001', 'region']) == {'AGNEBY-TIASSA'}
    for cid, registered in [('006', 31758), ('042', 176954), ('047', 555901), ('065', 29913), ('135', 35059)]:
        assert set(by_id.loc[cid, 'inscrits']) == {registered}
        assert by_id.loc[cid, 'elu'].sum() == 1
    assert set(by_id.loc['088', 'circonscription_name']) == {'BAGOHOUO, GBAPLEU ET GUEZON, COMMUNES ET SOUS- PREFECTURES'}
    assert by_id.loc['115', 'score'].sum() == 21681
    assert len(by_id.loc[['060']]) == 1
    assert 'OSONS LE CHANGEMENT' in set(by_id.loc['061', 'candidat'])
    assert 'KOUAME KOUASSI JEAN MICHEL' in set(by_id.loc['182', 'candidat'])
    assert extracted[['source_page', 'source_table', 'source_row']].notna().all().all()
    assert extracted['source_page'].between(1, 35).all()


def test_published_artifact_matches_fresh_extraction(extracted):
    actual = pd.read_parquet(OUTPUT_DIR / 'edan_2025_resultats.parquet')
    pd.testing.assert_frame_equal(actual, extracted)


def test_text_and_header_matching():
    assert _normalize_text('DISTRICT AUTONOME DE YAMOUSSOUKRO') == 'DISTRICT AUTONOME DE YAMOUSSOUKRO'
    assert not _is_header_row(['MORONOU'])
    assert _is_header_row(['REGI\nON'])


def test_schema_rejects_inconsistency_before_mutation(tmp_path, extracted, database):
    import duckdb
    broken = extracted.copy()
    broken.loc[0, 'inscrits'] += 1
    path = tmp_path / 'bad.parquet'
    broken.to_parquet(path)
    with pytest.raises(ValueError, match='Conflicting'):
        build_database(path, database, tmp_path / 'catalog.json')
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute('SELECT SUM(inscrits) FROM mart.vw_circonscriptions').fetchone()[0] == 8597092


def test_score_corruption_is_rejected(extracted):
    broken = extracted.copy()
    broken.loc[0, 'score'] += 1
    with pytest.raises(ValueError, match='scores'):
        validate_data(broken)
