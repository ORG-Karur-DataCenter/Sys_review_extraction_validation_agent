"""
healing_pipeline.py — Self-Healing Extraction Validation Pipeline

Orchestrates: Validation → Re-Extraction (of failures) → Re-Validation
Uses Google Gemini API by default. Falls back to browser automation with --mode browser.

Usage:
    python healing_pipeline.py --api-key YOUR_KEY
    python healing_pipeline.py --api-key YOUR_KEY --limit 5
    python healing_pipeline.py --mode browser --browser chrome   (legacy browser mode)
"""

import os
import re
import json
import time
import logging
import argparse
import pandas as pd
from datetime import datetime

# Import post-processing functions
from gemini_extractor import create_prompt, ALL_COLUMNS, deterministic_pct_to_count, save_null_reasons_log
from validation_agent import create_validation_prompt

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("pipeline_run.log", mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
ARTICLES_DIR = 'Articles'
OUTPUT_FILE = 'extracted_studies.xlsx'
VALIDATION_LOG = 'validation_discrepancies.xlsx'
HEALING_REPORT = 'healing_comparison_report.xlsx'
PIPELINE_SUMMARY = 'pipeline_summary.json'
MAX_HEAL_ATTEMPTS = 1  # As described in the manuscript


# ============================================================
# API MODE — Clean, fast, no browser needed
# ============================================================

# Manuscript-specified parameters (Line 45)
GENERATION_CONFIG = {
    "temperature": 0.2,
    "max_output_tokens": 2048,
}

API_KEY_POOL = []
CURRENT_KEY_INDEX = 0

def setup_api(api_key, model_name="gemini-2.5-flash"):
    """Initialize the Gemini API client with manuscript-specified parameters."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name,
            generation_config=GENERATION_CONFIG
        )
        logger.info(f"Gemini API initialized: model={model_name}, temp={GENERATION_CONFIG['temperature']}, max_tokens={GENERATION_CONFIG['max_output_tokens']}")
        return genai, model
    except ImportError:
        logger.error("google-generativeai package not installed. Run: pip install google-generativeai")
        raise
    except Exception as e:
        logger.error(f"Failed to initialize Gemini API: {e}")
        raise


def upload_pdf(genai, pdf_path):
    """Upload a PDF to Gemini API and return the file handle."""
    logger.info(f"  Uploading {os.path.basename(pdf_path)}...")
    uploaded = genai.upload_file(pdf_path)
    # Wait for processing
    while uploaded.state.name == "PROCESSING":
        time.sleep(2)
        uploaded = genai.get_file(uploaded.name)
    if uploaded.state.name != "ACTIVE":
        logger.warning(f"  File upload state: {uploaded.state.name}")
    return uploaded


def parse_json_response(response_text):
    """Extract and parse JSON from a Gemini response string."""
    # Try to find JSON block
    # First try ```json ... ``` blocks
    json_block = re.search(r'```json\s*(.*?)\s*```', response_text, re.S)
    if json_block:
        try:
            return json.loads(json_block.group(1))
        except json.JSONDecodeError:
            pass

    # Fall back to raw { ... } extraction
    start = response_text.find('{')
    end = response_text.rfind('}') + 1
    if start != -1 and end > start:
        try:
            return json.loads(response_text[start:end])
        except json.JSONDecodeError:
            pass

    return None


def api_call_with_retry(func, genai_module=None, model_name="gemini-2.5-flash", max_retries=3):
    """Wrap an API call with automatic retry. Rotates API keys on 429 errors."""
    global CURRENT_KEY_INDEX
    pool_size = max(len(API_KEY_POOL), 1)
    for attempt in range(max_retries * pool_size):
        try:
            return func()
        except Exception as e:
            err_str = str(e)
            is_rate_limit = '429' in err_str or 'ResourceExhausted' in str(type(e).__name__)
            is_permission = '403' in err_str
            
            if is_rate_limit or is_permission:
                if API_KEY_POOL and genai_module:
                    # If we've tried every key once, wait before the next full cycle
                    if (attempt + 1) % len(API_KEY_POOL) == 0:
                        wait_secs = 60
                        logger.warning(f"    All {len(API_KEY_POOL)} keys in pool hit rate limits. Waiting {wait_secs}s before next cycle...")
                        time.sleep(wait_secs)
                    
                    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEY_POOL)
                    new_key = API_KEY_POOL[CURRENT_KEY_INDEX]
                    genai_module.configure(api_key=new_key)
                    logger.warning(f"    Rotating to key #{CURRENT_KEY_INDEX+1}/{len(API_KEY_POOL)}...")
                    time.sleep(2)  # Brief pause
                else:
                    wait_match = re.search(r'retry in (\d+)', err_str)
                    wait_secs = int(wait_match.group(1)) + 5 if wait_match else 60
                    logger.warning(f"    Rate limit hit. Waiting {wait_secs}s before retry {attempt+1}/{max_retries}...")
                    time.sleep(wait_secs)
            else:
                raise
    raise Exception(f"All API keys exhausted after {max_retries * pool_size} attempts")


def validate_row_api(genai_module, model, pdf_path, row_data):
    """Validate a single row against its source PDF using the API."""
    def _do_validate():
        uploaded_file = upload_pdf(genai_module, pdf_path)
        prompt = create_validation_prompt(row_data)
        response = model.generate_content([uploaded_file, prompt])
        return response

    try:
        response = api_call_with_retry(_do_validate, genai_module=genai_module)
        data = parse_json_response(response.text)

        if not data:
            return {"status": "ERROR", "message": "No JSON in response", "discrepancies": []}

        # Apply severity logic (same as validation_agent.py)
        if data.get('status') == 'FAIL' and data.get('discrepancies'):
            critical = [d for d in data['discrepancies'] if d.get('severity') == 'CRITICAL']
            if not critical:
                logger.info(f"    Downgrading FAIL -> PASS (only MINOR discrepancies)")
                data['status'] = 'PASS'

        if data.get('status') == 'PASS' and data.get('discrepancies'):
            critical = [d for d in data['discrepancies'] if d.get('severity', 'CRITICAL') == 'CRITICAL']
            if critical:
                logger.info(f"    Overriding PASS -> FAIL (CRITICAL discrepancies found)")
                data['status'] = 'FAIL'

        return data

    except Exception as e:
        logger.error(f"    Validation API error: {e}")
        return {"status": "ERROR", "message": str(e), "discrepancies": []}


def extract_row_api(genai_module, model, pdf_path):
    """Re-extract data from a PDF using the API (no token limit for full schema)."""
    def _do_extract():
        uploaded_file = upload_pdf(genai_module, pdf_path)
        prompt = create_prompt()
        # Override max_output_tokens for extraction (full schema needs more than 2048)
        response = model.generate_content(
            [uploaded_file, prompt],
            generation_config={"temperature": 0.2, "max_output_tokens": 8192}
        )
        return response

    try:
        response = api_call_with_retry(_do_extract, genai_module=genai_module)
        data = parse_json_response(response.text)

        if data:
            data['Source File'] = os.path.basename(pdf_path)
            # Post-processing: save justification log for null fields
            save_null_reasons_log(data, os.path.basename(pdf_path))
            # Post-processing: deterministic percentage-to-count conversion
            data = deterministic_pct_to_count(data)
            return data
        else:
            logger.error(f"    No JSON in extraction response")
            return None

    except Exception as e:
        logger.error(f"    Extraction API error: {e}")
        return None


# ============================================================
# BROWSER MODE — Legacy fallback using Playwright
# ============================================================

def run_browser_mode(args):
    """Original subprocess-based browser mode (fallback)."""
    import subprocess

    def run_script(script_name, script_args=[]):
        logger.info(f">>> Running {script_name} with args: {script_args}...")
        cmd = ['python', script_name] + script_args
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Error running {script_name}: {e}")
            return False
        return True

    limit_args = ['--limit', args.limit] if args.limit else []

    logger.info("\n=== PHASE 1: VALIDATION (Browser Mode) ===")
    run_script('validation_agent.py', ['--browser', args.browser] + limit_args)

    logger.info("\n=== PHASE 2: SELF-HEALING ===")
    if os.path.exists(VALIDATION_LOG):
        disc_df = pd.read_excel(VALIDATION_LOG)
        failed_files = disc_df[disc_df['Status'] == 'FAIL']['Source File'].unique().tolist()
        failed_files = [str(f) for f in failed_files if pd.notnull(f)]
    else:
        failed_files = []

    if not failed_files:
        logger.info("No failures. Pipeline complete.")
        return {'initial_failures': 0, 'healed': 0, 'unresolved': []}

    logger.info(f"Re-extracting {len(failed_files)} failed files...")
    run_script('gemini_extractor.py', ['--browser', args.browser, '--files'] + failed_files)

    logger.info("\n=== PHASE 3: RE-VALIDATION ===")
    run_script('validation_agent.py', ['--browser', args.browser, '--files'] + failed_files)

    return {'initial_failures': len(failed_files), 'healed': 0, 'unresolved': failed_files}


# ============================================================
# CORE PIPELINE LOGIC
# ============================================================

def resolve_pdf_path(source_file, author_year=""):
    """Find the actual PDF path for a given source file entry."""
    if isinstance(source_file, str) and not pd.isna(source_file):
        path = os.path.join(ARTICLES_DIR, source_file)
        if os.path.exists(path):
            return path
        # Try basename
        path = os.path.join(ARTICLES_DIR, os.path.basename(source_file))
        if os.path.exists(path):
            return path

    # Smart match from author_year
    if author_year:
        match = re.search(r'(\w+).*?(\d{4})', str(author_year))
        if match:
            author_name = match.group(1).lower()
            year = match.group(2)
            for f in os.listdir(ARTICLES_DIR):
                f_lower = f.lower()
                if author_name in f_lower and year in f_lower and f_lower.endswith('.pdf'):
                    return os.path.join(ARTICLES_DIR, f)

    return None


def generate_healing_report(before_df, after_df, failed_files):
    """Compare old vs new extraction for healed files."""
    if before_df is None or before_df.empty or after_df is None or after_df.empty:
        return

    comparison_data = []
    before_indexed = before_df.set_index('Source File')
    after_indexed = after_df.set_index('Source File')

    for filename in failed_files:
        if filename in before_indexed.index and filename in after_indexed.index:
            old_row = before_indexed.loc[[filename]].iloc[0]
            new_row = after_indexed.loc[[filename]].iloc[0]

            common_cols = [c for c in before_indexed.columns
                          if c in after_indexed.columns and c not in ['Result', 'Sl.no']]

            for col in common_cols:
                old_val = str(old_row[col]) if pd.notnull(old_row[col]) else "NULL"
                new_val = str(new_row[col]) if pd.notnull(new_row[col]) else "NULL"

                if old_val != new_val:
                    comparison_data.append({
                        'Article': filename,
                        'Field': col,
                        'Original Value': old_val,
                        'Healed Value': new_val,
                        'Status': 'FIXED'
                    })

    if comparison_data:
        pd.DataFrame(comparison_data).to_excel(HEALING_REPORT, index=False)
        logger.info(f"Healing report saved: {HEALING_REPORT} ({len(comparison_data)} field changes)")
    else:
        logger.info("No data differences detected during healing pass.")


def run_api_mode(args):
    """Run the full pipeline using Gemini API (default mode)."""
    genai_module, model = setup_api(args.api_key, args.model)

    if not os.path.exists(OUTPUT_FILE):
        logger.error(f"{OUTPUT_FILE} not found. Run the extractor first.")
        return

    df = pd.read_excel(OUTPUT_FILE)
    if 'Source File' not in df.columns:
        logger.error("'Source File' column missing in Excel.")
        return

    if args.limit:
        df = df.head(int(args.limit))

    # ── PHASE 1: VALIDATE ALL ROWS ──────────────────────────
    logger.info(f"\n{'='*60}")
    logger.info(f"PHASE 1: VALIDATING {len(df)} ROWS")
    logger.info(f"{'='*60}\n")

    validation_results = []

    for index, row in df.iterrows():
        source_file = row['Source File']
        author_year = str(row.get('First Author (Year)', row.get('Study ID', '')))

        pdf_path = resolve_pdf_path(source_file, author_year)
        if not pdf_path:
            logger.warning(f"  Row {index+1}: PDF not found for '{source_file}'. Skipping.")
            continue

        # Skip rows with no data
        meta_cols = ['Source File', 'Sl.no', 'Unnamed: 0']
        clean_data = {k: v for k, v in row.to_dict().items() if k not in meta_cols and pd.notnull(v)}
        if not clean_data:
            logger.info(f"  Row {index+1}: No data to validate. Skipping.")
            validation_results.append({'Source File': source_file, 'status': 'NO DATA', 'discrepancies': []})
            continue

        logger.info(f"  Validating row {index+1}: {os.path.basename(pdf_path)}")
        result = validate_row_api(genai_module, model, pdf_path, row.to_dict())
        result['Source File'] = source_file
        validation_results.append(result)

        status = result.get('status', 'ERROR')
        if status == 'PASS':
            logger.info(f"    ✔ PASS")
        elif status == 'FAIL':
            for d in result.get('discrepancies', []):
                sev = d.get('severity', '?')
                field = d.get('field', '?')
                logger.info(f"    ✘ [{sev}] {field}: {d.get('description', '')[:60]}")
        else:
            logger.warning(f"    ⚠ {status}: {result.get('message', '')[:60]}")

    # Save initial validation log
    _save_validation_log(validation_results)

    # ── PHASE 2: SELF-HEALING ───────────────────────────────
    failed_files = [r['Source File'] for r in validation_results if r.get('status') == 'FAIL']
    initial_failed = list(failed_files)

    if not failed_files:
        logger.info("\n✔ All rows passed validation. No healing needed.")
        return _save_summary(validation_results, initial_failed, [], 0)

    logger.info(f"\n{'='*60}")
    logger.info(f"PHASE 2: SELF-HEALING ({len(failed_files)} failures)")
    logger.info(f"{'='*60}\n")

    # Capture the "before" state
    full_df = pd.read_excel(OUTPUT_FILE)
    before_snapshot = full_df[full_df['Source File'].isin(failed_files)].copy()

    # Remove failed rows from output file to prepare for re-extraction
    cleaned_df = full_df[~full_df['Source File'].isin(failed_files)]
    cleaned_df.to_excel(OUTPUT_FILE, index=False)
    logger.info(f"  Removed {len(failed_files)} failed entries from {OUTPUT_FILE}")

    # Re-extract each failed file
    re_extracted = []
    for source_file in failed_files:
        pdf_path = resolve_pdf_path(source_file)
        if not pdf_path:
            logger.warning(f"  Cannot re-extract '{source_file}': PDF not found.")
            continue

        logger.info(f"  Re-extracting: {os.path.basename(pdf_path)}")
        data = extract_row_api(genai_module, model, pdf_path)
        if data:
            re_extracted.append(data)
            logger.info(f"    ✔ Re-extraction successful")
        else:
            logger.error(f"    ✘ Re-extraction failed")

    # Append re-extracted data back to output file
    if re_extracted:
        new_df = pd.DataFrame(re_extracted)
        for c in ALL_COLUMNS:
            if c not in new_df.columns:
                new_df[c] = None

        cols = ['Source File'] + [c for c in ALL_COLUMNS if c in new_df.columns]
        new_df = new_df[cols]

        existing = pd.read_excel(OUTPUT_FILE)
        merged = pd.concat([existing, new_df], ignore_index=True)
        merged.to_excel(OUTPUT_FILE, index=False)
        logger.info(f"  Saved {len(re_extracted)} re-extracted rows to {OUTPUT_FILE}")

    # ── PHASE 3: RE-VALIDATE HEALED DATA ────────────────────
    logger.info(f"\n{'='*60}")
    logger.info(f"PHASE 3: RE-VALIDATING HEALED DATA")
    logger.info(f"{'='*60}\n")

    healed_df = pd.read_excel(OUTPUT_FILE)
    healed_rows = healed_df[healed_df['Source File'].isin(failed_files)]

    heal_results = []
    for index, row in healed_rows.iterrows():
        source_file = row['Source File']
        pdf_path = resolve_pdf_path(source_file)
        if not pdf_path:
            continue

        logger.info(f"  Re-validating: {os.path.basename(pdf_path)}")
        result = validate_row_api(genai_module, model, pdf_path, row.to_dict())
        result['Source File'] = source_file
        heal_results.append(result)

        status = result.get('status', 'ERROR')
        if status == 'PASS':
            logger.info(f"    ✔ HEALED → PASS")
        else:
            logger.info(f"    ✘ Still failing")

    # Generate healing comparison report
    after_snapshot = pd.read_excel(OUTPUT_FILE)
    after_snapshot = after_snapshot[after_snapshot['Source File'].isin(failed_files)]
    generate_healing_report(before_snapshot, after_snapshot, failed_files)

    # Update validation log with heal results
    # Replace old failed entries with new results
    final_results = [r for r in validation_results if r['Source File'] not in failed_files] + heal_results
    _save_validation_log(final_results)

    unresolved = [r['Source File'] for r in heal_results if r.get('status') != 'PASS']
    return _save_summary(final_results, initial_failed, unresolved, 1)


def _save_validation_log(validation_results):
    """Flatten validation results into the discrepancy Excel log."""
    flattened = []
    for res in validation_results:
        sf = res.get('Source File')
        status = res.get('status')
        if not res.get('discrepancies'):
            desc = 'None' if status != 'NO DATA' else 'Row has no extracted data points to verify'
            flattened.append({
                'Source File': sf, 'Status': status, 'Severity': None,
                'Field': None, 'Extracted Value': None,
                'Correct Value': None, 'Description': desc
            })
        else:
            for d in res['discrepancies']:
                flattened.append({
                    'Source File': sf, 'Status': status,
                    'Severity': d.get('severity', 'UNKNOWN'),
                    'Field': d.get('field'),
                    'Extracted Value': d.get('extracted_value'),
                    'Correct Value': d.get('correct_value'),
                    'Description': d.get('description')
                })

    pd.DataFrame(flattened).to_excel(VALIDATION_LOG, index=False)
    logger.info(f"Validation log saved: {VALIDATION_LOG}")


def _save_summary(validation_results, initial_failed, unresolved, heal_attempts):
    """Save the final pipeline summary as JSON."""
    summary = {
        'timestamp': datetime.now().isoformat(),
        'status': 'SUCCESS' if not unresolved else 'COMPLETED_WITH_FAILURES',
        'total_validated': len(validation_results),
        'passed': sum(1 for r in validation_results if r.get('status') == 'PASS'),
        'failed': sum(1 for r in validation_results if r.get('status') == 'FAIL'),
        'errors': sum(1 for r in validation_results if r.get('status') == 'ERROR'),
        'critical_errors': sum(
            len([d for d in r.get('discrepancies', []) if d.get('severity') == 'CRITICAL'])
            for r in validation_results
        ),
        'minor_errors': sum(
            len([d for d in r.get('discrepancies', []) if d.get('severity') == 'MINOR'])
            for r in validation_results
        ),
        'initial_failures': len(initial_failed),
        'initial_failed_files': initial_failed,
        'healing_attempts': heal_attempts,
        'unresolved_failures': len(unresolved),
        'unresolved_files': unresolved,
        'healing_report': HEALING_REPORT if os.path.exists(HEALING_REPORT) else None,
    }

    with open(PIPELINE_SUMMARY, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)

    # Print final log
    logger.info(f"\n{'='*60}")
    logger.info(f"         PIPELINE COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"  Status:            {summary['status']}")
    logger.info(f"  Total Validated:   {summary['total_validated']}")
    logger.info(f"  Passed:            {summary['passed']}")
    logger.info(f"  Initial Failures:  {summary['initial_failures']}")
    logger.info(f"  Healing Attempts:  {summary['healing_attempts']}")
    logger.info(f"  Unresolved:        {summary['unresolved_failures']}")
    logger.info(f"  Critical Errors:   {summary['critical_errors']}")
    logger.info(f"  Minor Errors:      {summary['minor_errors']}")
    logger.info(f"  Summary File:      {PIPELINE_SUMMARY}")
    logger.info(f"{'='*60}")

    return summary


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Self-Healing Extraction Validation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python healing_pipeline.py --api-key YOUR_KEY
  python healing_pipeline.py --api-key YOUR_KEY --model gemini-2.5-pro --limit 5
  python healing_pipeline.py --mode browser --browser chrome
        """
    )
    parser.add_argument("--mode", choices=["browser", "api"], default=None,
                        help="Execution mode: auto-detected if not set (browser if no key, api if key present)")
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY"),
                        help="Gemini API key — auto-enables API mode (or set GEMINI_API_KEY env var)")
    parser.add_argument("--api-kit", default=None,
                        help="Path to file with multiple API keys for rate-limit rotation")
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="Gemini model for API mode (default: gemini-2.0-flash)")
    parser.add_argument("--browser", default="chrome",
                        help="Browser channel for browser mode (chrome, msedge)")
    parser.add_argument("--limit", default=None,
                        help="Limit number of rows to validate")
    args = parser.parse_args()

    # Load API key pool from file if provided
    global API_KEY_POOL
    if args.api_kit and os.path.exists(args.api_kit):
        with open(args.api_kit, 'r') as f:
            API_KEY_POOL = [line.strip() for line in f if line.strip() and line.strip().startswith('AIza')]
        logger.info(f"Loaded {len(API_KEY_POOL)} API keys from {args.api_kit}")
        if not args.api_key and API_KEY_POOL:
            args.api_key = API_KEY_POOL[0]

    # Smart auto-detection: API key present → API mode, otherwise → browser (free)
    if args.mode is None:
        if args.api_key:
            args.mode = "api"
            logger.info("API key detected → using API mode (faster)")
        else:
            args.mode = "browser"
            logger.info("No API key → using browser mode (free, as per manuscript)")

    logger.info(f"=== Healing Pipeline Started at {datetime.now().isoformat()} ===")
    logger.info(f"Mode: {args.mode}")

    # Clear old logs
    if os.path.exists(VALIDATION_LOG):
        os.remove(VALIDATION_LOG)
        logger.info(f"Cleared old: {VALIDATION_LOG}")

    if args.mode == "api":
        if not args.api_key:
            logger.error("API key required for API mode. Use --api-key or set GEMINI_API_KEY env var.")
            return
        run_api_mode(args)
    else:
        run_browser_mode(args)


if __name__ == "__main__":
    main()
