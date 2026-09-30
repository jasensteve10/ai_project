"""Stage 1 — exploratory data analysis of the source PDF and the cleaned CSV.

    python -m src.pipeline.eda      # writes outputs/eda/{figures,tables,summary.json,manifest.json}
"""
import argparse
from pathlib import Path

from src.eda.analysis import run_analysis
from src.preprocessing.ingestion import OUTPUT_DIR, PDF_PATH

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs/eda')
    args = parser.parse_args(argv)
    run_analysis(PDF_PATH, OUTPUT_DIR / 'edan_2025_resultats.csv', args.output_dir)


if __name__ == '__main__':
    main()
