"""Rebuild election data from the PDF's ruled cells, with source provenance."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import pdfplumber

from src.ETL_fonctions.fonctions_ingest import (
    _clean_val, _is_header_row, _fr_int, _fr_pct_to_float, print_summary,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_PATH = PROJECT_ROOT / 'dataset/raw/EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf'
OUTPUT_DIR = PROJECT_ROOT / 'dataset/clean'
COLUMNS = [
    'region', 'circonscription_id', 'circonscription_name', 'nb_bureaux_vote',
    'inscrits', 'votants', 'taux_participation', 'bulletins_nuls',
    'suffrages_exprimes', 'bulletins_blancs_nb', 'bulletins_blancs_pct',
    'parti', 'candidat', 'score', 'score_pct', 'elu',
]
INTEGER_COLUMNS = [COLUMNS[i] for i in [3, 4, 5, 7, 8, 9, 13]]
PERCENT_COLUMNS = [COLUMNS[i] for i in [6, 10, 14]]
CONSTITUENCY_COLUMNS = COLUMNS[:11]


def _keep_table_object(obj):
    # This PDF draws rules as thin filled rectangles. Large shaded rectangles
    # are backgrounds, not borders; their edges split otherwise merged cells.
    return obj['object_type'] != 'rect' or min(obj['width'], obj['height']) < 2


def extract_pdf(pdf_path=PDF_PATH) -> pd.DataFrame:
    records, source_totals, pending = [], {}, []
    context = None
    region = ''
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, original_page in enumerate(pdf.pages, 1):
            page = original_page.filter(_keep_table_object)
            # Some tables have no closing rule (notably page 20). Close them
            # at the bottom of their actual rules so the final row is retained.
            bottom = max(r['bottom'] for r in page.rects)
            tables = page.find_tables({'explicit_horizontal_lines': [bottom]})
            if not tables:
                raise ValueError(f'No ruled table found on page {page_number}')
            for table_number, table in enumerate(tables, 1):
                for row_number, (cells, geometry) in enumerate(zip(table.extract(), table.rows), 1):
                    if len(cells) != 16:
                        raise ValueError(f'Unexpected table width on page {page_number}: {len(cells)}')
                    row = [_clean_val(v) for v in cells]
                    if _is_header_row(row):
                        if row[0] == 'TOTAL':
                            source_totals = {COLUMNS[i]: row[i] for i in [3, 4, 5, 7, 8, 9, 13]}
                        continue
                    if row[0]:
                        # Region glyphs are placed bottom-to-top, even though
                        # this file reports them as upright. Preserve real spaces.
                        chars = page.crop(geometry.cells[0]).chars
                        region = _clean_val(''.join(c['text'] for c in sorted(chars, key=lambda c: -c['top'])))
                    if row[1]:
                        if not row[1].isdigit():
                            raise ValueError(f'Invalid constituency ID at page {page_number}: {row[1]}')
                        context = dict(zip(CONSTITUENCY_COLUMNS, [region, *row[1:11]]))
                        for record in pending:
                            record.update(context)
                        pending.clear()
                    elif any(row[1:11]):
                        raise ValueError(f'Unresolved constituency cell at page {page_number}, row {row_number}')
                    elif geometry.cells[1] is not None:
                        # A new, empty merged cell starts before the page break;
                        # its constituency label and totals occur on the next page.
                        context = None
                    if row[11] or row[12]:
                        if not region:
                            raise ValueError(f'Candidate without constituency at page {page_number}')
                        record = {
                            **(context or {}), **dict(zip(COLUMNS[11:], row[11:])),
                            'source_page': page_number, 'source_table': table_number,
                            'source_row': row_number,
                        }
                        records.append(record)
                        if context is None:
                            pending.append(record)
    if pending:
        raise ValueError('Unresolved constituency at end of PDF')
    if not source_totals:
        raise ValueError('Printed national totals were not found')
    if not records:
        raise ValueError('PDF contains no candidate records')
    df = pd.DataFrame(records)
    df.attrs['source_totals'] = source_totals
    df.attrs['source_pdf_sha256'] = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    return df


def transform_data(df_raw: pd.DataFrame) -> pd.DataFrame:
    df = df_raw.copy()
    for col in INTEGER_COLUMNS:
        df[col] = _fr_int(df[col])
    for col in PERCENT_COLUMNS:
        df[col] = _fr_pct_to_float(df[col])
    if not set(df['elu']).issubset({'', 'ELU(E)'}):
        raise ValueError('Unknown elected marker in PDF')
    df['elu'] = df['elu'].eq('ELU(E)')
    validate_data(df)
    return df


def validate_data(df: pd.DataFrame) -> None:
    """Reject extraction defects before any aggregate can hide them."""
    if df.empty or df[COLUMNS].isna().any().any():
        raise ValueError('Empty dataset or missing required election values')
    if df[COLUMNS[:3] + ['parti', 'candidat']].eq('').any().any():
        raise ValueError('Empty required text')
    if not df['circonscription_id'].str.fullmatch(r'\d{3}').all():
        raise ValueError('Constituency IDs must be three-character strings')
    conflicts = df.groupby('circonscription_id')[CONSTITUENCY_COLUMNS].nunique(dropna=False)
    if (conflicts > 1).any().any():
        raise ValueError(f'Conflicting constituency values: {conflicts.index[(conflicts > 1).any(axis=1)].tolist()}')
    if df[COLUMNS].duplicated().any():
        raise ValueError('Duplicate candidate records')
    if (df[INTEGER_COLUMNS] < 0).any().any():
        raise ValueError('Negative count')
    if ((df[PERCENT_COLUMNS] < 0) | (df[PERCENT_COLUMNS] > 1)).any().any():
        raise ValueError('Percentage outside [0, 1]')
    c = df.drop_duplicates('circonscription_id')
    if not (c['votants'] == c['bulletins_nuls'] + c['suffrages_exprimes']).all():
        raise ValueError('Voters do not reconcile with invalid and expressed ballots')
    if (c['votants'] > c['inscrits']).any():
        raise ValueError('Voters exceed registrations')
    scores = df.groupby('circonscription_id')['score'].sum()
    expected = c.set_index('circonscription_id').eval('suffrages_exprimes - bulletins_blancs_nb')
    if not scores.sort_index().equals(expected.sort_index()):
        raise ValueError('Candidate scores do not reconcile with constituency ballots')
    for numerator, denominator, pct in [('votants', 'inscrits', 'taux_participation'),
                                        ('bulletins_blancs_nb', 'suffrages_exprimes', 'bulletins_blancs_pct'),
                                        ('score', 'suffrages_exprimes', 'score_pct')]:
        if ((df[numerator] / df[denominator] - df[pct]).abs() > 0.000051).any():
            raise ValueError(f'Percentage does not reconcile: {pct}')


def build_audit(df):
    c = df.drop_duplicates('circonscription_id')
    winners = df.groupby('circonscription_id')['elu'].sum()
    totals = {col: int(c[col].sum()) for col in INTEGER_COLUMNS if col != 'score'}
    totals['score'] = int(df['score'].sum())
    source_totals = {col: int(value.replace(' ', '')) for col, value in df.attrs.get('source_totals', {}).items()}
    ids = set(df['circonscription_id'])
    return {
        'source_pdf_sha256': df.attrs.get('source_pdf_sha256'),
        'rows': len(df), 'constituencies': len(c), 'regions': df['region'].nunique(),
        'parties': df['parti'].nunique(), 'winning_rows': int(df['elu'].sum()),
        'missing_cells': int(df.isna().sum().sum()),
        'winning_row_count_distribution': {str(k): int(v) for k, v in winners.value_counts().sort_index().items()},
        'id_gaps_within_observed_range': [f'{i:03}' for i in range(int(min(ids)), int(max(ids)) + 1) if f'{i:03}' not in ids],
        'extracted_totals': totals, 'pdf_printed_national_totals': source_totals,
        'national_total_differences': {col: totals[col] - value for col, value in source_totals.items()},
        'notes': ['Counts refer only to records present in this PDF.',
                  'Winning rows are candidates/lists, not a count of seats.',
                  'Printed national totals are retained separately; no missing constituency is fabricated.'],
    }


def save_data(df, output_dir=OUTPUT_DIR):
    validate_data(df)
    audit = build_audit(df)
    if any(audit['national_total_differences'].values()):
        raise ValueError('Extracted data does not reconcile with printed national totals')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_dir / 'edan_2025_resultats.parquet', index=False)
    csv_path = output_dir / 'edan_2025_resultats.csv'
    df.to_csv(csv_path, index=False)
    audit['csv_sha256'] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    (output_dir / 'edan_2025_validation.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False) + '\n')
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, default=PDF_PATH)
    parser.add_argument('--output-dir', type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    df = transform_data(extract_pdf(args.pdf))
    audit = save_data(df, args.output_dir)
    print_summary(df)
    print(json.dumps(audit, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
