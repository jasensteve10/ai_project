import pytest
from src.preprocessing.ingestion import extract_pdf, transform_data
from src.preprocessing.schema import build_database


@pytest.fixture(scope='session')
def extracted():
    return transform_data(extract_pdf())


@pytest.fixture(scope='session')
def database(tmp_path_factory, extracted):
    root = tmp_path_factory.mktemp('election')
    parquet = root / 'data.parquet'
    extracted.to_parquet(parquet, index=False)
    path = root / 'test.duckdb'
    build_database(parquet, path, root / 'catalog.json')
    return path
