# Systematic Review Extraction Validation & Healing Agent

An autonomous validation and self-healing pipeline that verifies AI-extracted research data against source PDFs using Google Gemini, automatically detects discrepancies, and re-extracts failed entries — without human intervention.

Part of the **Agentic AI-Powered Systematic Review Pipeline** described in:
> *"Agentic AI for Systematic Reviews: A Four-Agent Pipeline for Deduplication, Screening, Extraction, and Validation"*

**Related Repositories:**
- [Deduplication Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_DeDuplication_agent)
- [Screening Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_screening_agent)
- [Extraction Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_extraction_agent)

---

## Features

- **3-Phase autonomous pipeline** — Validate → Self-Heal → Re-Validate, fully automated
- **Tiered discrepancy classification**:
  - `CRITICAL`: wrong numbers, swapped cohorts, missing data → triggers re-extraction
  - `MINOR`: rounding differences <1%, synonym variation → logged but PASS
- **Self-healing loop** — failed rows are removed, re-extracted from PDF, and re-validated
- **Healing comparison report** — `healing_comparison_report.xlsx` shows exact field changes
- **Cross-validation** — extract with two LLM calls; flag fields where extractors disagree
- **API key rotation** — provide `API_KIT.txt` to automatically switch keys on rate limits
- **Free by default** — Playwright browser automation; no API costs required

---

## Installation

```bash
git clone https://github.com/ORG-Karur-DataCenter/Sys_review_extraction_validation_agent.git
cd Sys_review_extraction_validation_agent
pip install -r requirements.txt
playwright install chromium
```

---

## Usage

### Free Mode (Browser Automation)

```bash
python healing_pipeline.py --browser chrome
```

### API Mode (Faster, Recommended)

```bash
python healing_pipeline.py --api-key YOUR_GEMINI_KEY
```

### With API Key Rotation (No Rate Limit Waits)

```bash
python healing_pipeline.py --api-kit API_KIT.txt
```

`API_KIT.txt` — one API key per line:
```
AIzaSyABC...
AIzaSyDEF...
AIzaSyGHI...
```

On rate limit (429), the pipeline automatically switches to the next key (2-second pause) instead of waiting minutes. After cycling all keys, it waits 60 seconds before the next cycle.

---

## Project Structure

```
validation_agent/
├── healing_pipeline.py              # Main 3-phase validation + healing pipeline
├── validation_agent.py              # Core validation logic
├── gemini_extractor.py              # PDF extraction module (used in Phase 2)
├── cross_validate_extraction.py     # Cross-validation between two LLM extractors
├── e2e_test.py                      # End-to-end test suite (23 tests)
├── template_extracted_studies.xlsx  # Blank output template (schema reference)
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

---

## Pipeline Phases

### Phase 1: Validation

For each row in `extracted_studies.xlsx`:
1. Uploads the source PDF to Gemini
2. Sends extracted data + PDF for comparison
3. Gemini returns: `status` (PASS/FAIL), `discrepancies[]` with `severity` and `explanation`
4. CRITICAL discrepancies → FAIL; MINOR only → downgraded to PASS

```
Row 1 (Sanjay 1993): ✔ PASS  — correct data confirmed
Row 2 (Ouyang 2017): ✘ FAIL  — 3 CRITICAL discrepancies:
   Study Design: "retrospective" ≠ "RCT"
   Sample Size:  "94" ≠ "9999"
   Diagnosis:    "Giant Cell Tumor" ≠ "Knee Osteoarthritis"
```

### Phase 2: Self-Healing

For every FAIL row:
1. Removes the failed entry from `extracted_studies.xlsx`
2. Re-extracts data from the PDF using `gemini_extractor.py`
3. Appends the corrected extraction back to `extracted_studies.xlsx`

### Phase 3: Re-Validation

1. Re-validates all healed rows (same process as Phase 1)
2. Generates `healing_comparison_report.xlsx` — shows before/after for every changed field
3. Saves final `validation_discrepancies.xlsx` with full audit trail

---

## Output Files

| File | Description |
|---|---|
| `extracted_studies.xlsx` | Final clean extraction data (auto-healed) |
| `validation_discrepancies.xlsx` | All discrepancies found in Phase 1 |
| `healing_comparison_report.xlsx` | Before/after field values for healed rows |
| `pipeline_summary.json` | Machine-readable pipeline run summary |
| `pipeline_run.log` | Full timestamped execution log |
| `missing_data_justifications.json` | Per-field null reasons from extraction |

---

## Discrepancy Classification

| Severity | Trigger | Action |
|---|---|---|
| `CRITICAL` | Numeric mismatch >1%, wrong study design, swapped cohorts | FAIL → Re-extract |
| `MINOR` | Rounding <1%, synonym variation, formatting | Log only → PASS |

---

## API Configuration

| Parameter | Validation | Extraction |
|---|---|---|
| `temperature` | `0.2` | `0.2` |
| `max_output_tokens` | `2048` | `8192` |
| Model | `gemini-2.5-flash` | `gemini-2.5-flash` |

> Extraction uses `8192` tokens to ensure the full schema JSON is never truncated.

---

## End-to-End Test Results

The pipeline was validated with 23 automated tests and a full integration test:

| Phase | Tests | Result |
|---|---|---|
| Deduplication | 4/4 | ✅ PASS |
| Dual-Pass Screening | 4/4 | ✅ PASS |
| Post-Processing | 6/6 | ✅ PASS |
| API Extraction | 6/6 | ✅ PASS |
| API Validation | 3/3 | ✅ PASS |
| **Full Healing Integration** | **1/1** | **✅ COMPLETE** |

Full integration: planted 3 CRITICAL errors in extracted data → validated → self-healed → re-validated → **all errors corrected automatically**.

---

## License

MIT License — see validation_agent directory for details.
