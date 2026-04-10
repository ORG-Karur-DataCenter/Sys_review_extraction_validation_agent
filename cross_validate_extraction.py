"""
cross_validate_extraction.py — Cross-Agent Extraction Redundancy

As described in the manuscript:
"Two independent extraction agents processed the same dataset using slightly 
different parsing strategies. Outputs were compared, and discrepancies were 
flagged for human adjudication."

This script compares outputs from:
  - Extractor A: template-based extraction (extraction_agent/gemini_extractor.py)
  - Extractor B: hardcoded schema extraction (validation_agent/gemini_extractor.py)

Usage:
    python cross_validate_extraction.py --file-a extracted_studies_A.xlsx --file-b extracted_studies_B.xlsx
    python cross_validate_extraction.py  (uses defaults)
"""

import os
import json
import logging
import pandas as pd
import argparse
from datetime import datetime

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("cross_validation_run.log", mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_FILE_A = 'extracted_studies.xlsx'
DEFAULT_FILE_B = 'extracted_studies_api.xlsx'
OUTPUT_REPORT = 'cross_agent_discrepancies.xlsx'
OUTPUT_SUMMARY = 'cross_validation_summary.json'

# Fields to skip during comparison (metadata, not extracted data)
SKIP_FIELDS = {'Source File', 'Unnamed: 0', 'Sl.no', 'Result'}


def normalize_value(val):
    """Normalize a value for comparison — handles NaN, whitespace, casing."""
    if pd.isna(val) or val is None:
        return None
    val = str(val).strip().lower()
    if val in ('nan', 'none', 'null', 'n/a', 'na', '-', ''):
        return None
    return val


def compare_extractions(df_a, df_b):
    """
    Compare two extraction DataFrames study-by-study, field-by-field.
    Returns list of discrepancy dicts and agreement statistics.
    """
    discrepancies = []
    total_comparisons = 0
    total_agreements = 0

    # Index by Source File
    if 'Source File' not in df_a.columns or 'Source File' not in df_b.columns:
        logger.error("Both files must have a 'Source File' column.")
        return [], 0, 0

    a_indexed = df_a.set_index('Source File')
    b_indexed = df_b.set_index('Source File')

    common_files = set(a_indexed.index) & set(b_indexed.index)
    common_cols = [c for c in a_indexed.columns if c in b_indexed.columns and c not in SKIP_FIELDS]

    if not common_files:
        logger.warning("No common Source Files found between the two extraction outputs.")
        return [], 0, 0

    logger.info(f"Comparing {len(common_files)} studies across {len(common_cols)} fields...")

    for source_file in sorted(common_files):
        row_a = a_indexed.loc[source_file]
        row_b = b_indexed.loc[source_file]

        # Handle case where there are duplicate Source File entries
        if isinstance(row_a, pd.DataFrame):
            row_a = row_a.iloc[0]
        if isinstance(row_b, pd.DataFrame):
            row_b = row_b.iloc[0]

        for col in common_cols:
            val_a = normalize_value(row_a.get(col))
            val_b = normalize_value(row_b.get(col))
            total_comparisons += 1

            if val_a == val_b:
                total_agreements += 1
            else:
                # Both null = agreement (already counted above)
                # One null, one has value = discrepancy
                discrepancies.append({
                    'Source File': source_file,
                    'Field': col,
                    'Extractor_A_Value': str(row_a.get(col)) if pd.notnull(row_a.get(col)) else 'NULL',
                    'Extractor_B_Value': str(row_b.get(col)) if pd.notnull(row_b.get(col)) else 'NULL',
                    'Severity': classify_discrepancy(col, val_a, val_b),
                    'Status': 'FLAGGED_FOR_HUMAN_ADJUDICATION'
                })

    only_in_a = set(a_indexed.index) - set(b_indexed.index)
    only_in_b = set(b_indexed.index) - set(a_indexed.index)

    if only_in_a:
        logger.warning(f"  {len(only_in_a)} studies only in Extractor A: {list(only_in_a)[:5]}...")
    if only_in_b:
        logger.warning(f"  {len(only_in_b)} studies only in Extractor B: {list(only_in_b)[:5]}...")

    return discrepancies, total_comparisons, total_agreements


def classify_discrepancy(field, val_a, val_b):
    """Classify a discrepancy as CRITICAL or MINOR based on field type and value difference."""
    # If one is null and other has data — critical (extraction missed it)
    if val_a is None or val_b is None:
        return 'CRITICAL'

    # Numeric comparison — check if values are numbers and differ by >1%
    try:
        num_a = float(val_a.replace('%', '').replace(',', ''))
        num_b = float(val_b.replace('%', '').replace(',', ''))
        if num_a != 0:
            pct_diff = abs(num_a - num_b) / abs(num_a) * 100
            return 'MINOR' if pct_diff <= 1.0 else 'CRITICAL'
        elif num_b != 0:
            return 'CRITICAL'
        return 'MINOR'  # Both are 0
    except (ValueError, AttributeError):
        pass

    # String comparison — synonym-level differences are MINOR
    if val_a.replace(' ', '') == val_b.replace(' ', ''):
        return 'MINOR'  # Whitespace only

    return 'CRITICAL'


def main():
    parser = argparse.ArgumentParser(
        description="Cross-validate two extraction agent outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file-a", default=DEFAULT_FILE_A,
                        help=f"First extraction output (default: {DEFAULT_FILE_A})")
    parser.add_argument("--file-b", default=DEFAULT_FILE_B,
                        help=f"Second extraction output (default: {DEFAULT_FILE_B})")
    args = parser.parse_args()

    logger.info(f"=== Cross-Agent Extraction Validation ===")
    logger.info(f"  Extractor A: {args.file_a}")
    logger.info(f"  Extractor B: {args.file_b}")

    if not os.path.exists(args.file_a):
        logger.error(f"File not found: {args.file_a}")
        return
    if not os.path.exists(args.file_b):
        logger.error(f"File not found: {args.file_b}")
        return

    df_a = pd.read_excel(args.file_a)
    df_b = pd.read_excel(args.file_b)

    logger.info(f"  Extractor A: {len(df_a)} rows, {len(df_a.columns)} columns")
    logger.info(f"  Extractor B: {len(df_b)} rows, {len(df_b.columns)} columns")

    discrepancies, total_comparisons, total_agreements = compare_extractions(df_a, df_b)

    agreement_rate = (total_agreements / total_comparisons * 100) if total_comparisons > 0 else 0
    critical_count = sum(1 for d in discrepancies if d['Severity'] == 'CRITICAL')
    minor_count = sum(1 for d in discrepancies if d['Severity'] == 'MINOR')

    # Save discrepancy report
    if discrepancies:
        pd.DataFrame(discrepancies).to_excel(OUTPUT_REPORT, index=False)
        logger.info(f"Discrepancy report saved: {OUTPUT_REPORT}")
    else:
        logger.info("No discrepancies found — perfect agreement between extractors.")

    # Save summary JSON
    summary = {
        'timestamp': datetime.now().isoformat(),
        'extractor_a': args.file_a,
        'extractor_b': args.file_b,
        'total_field_comparisons': total_comparisons,
        'agreements': total_agreements,
        'agreement_rate_pct': round(agreement_rate, 2),
        'total_discrepancies': len(discrepancies),
        'critical_discrepancies': critical_count,
        'minor_discrepancies': minor_count,
    }
    with open(OUTPUT_SUMMARY, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    logger.info(f"Summary saved: {OUTPUT_SUMMARY}")

    # Final log
    logger.info(f"\n{'='*50}")
    logger.info(f"  CROSS-VALIDATION COMPLETE")
    logger.info(f"{'='*50}")
    logger.info(f"  Total Comparisons:  {total_comparisons}")
    logger.info(f"  Agreement Rate:     {agreement_rate:.1f}%")
    logger.info(f"  Critical Issues:    {critical_count}")
    logger.info(f"  Minor Issues:       {minor_count}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    main()
