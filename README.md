<p align="center">
  <h1 align="center">Systematic Review Validation & Healing Agent</h1>
  <p align="center">
    Autonomous 3-phase pipeline: Validate → Self-Heal → Re-Validate.<br>
    Catches extraction errors and fixes them automatically.
  </p>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#output">Output</a> •
  <a href="#advanced">Advanced</a> •
  <a href="#contributing">Contributing</a>
</p>

---

> Part of the **Agentic AI-Powered Systematic Review Pipeline**
>
> [Deduplication Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_DeDuplication_agent) →
> [Screening Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_screening_agent) →
> [Extraction Agent](https://github.com/ORG-Karur-DataCenter/Systematic_review_extraction_agent) →
> **Validation & Healing Agent**

---

## Quickstart

### 1. Install

```bash
git clone https://github.com/ORG-Karur-DataCenter/Sys_review_extraction_validation_agent.git
cd Sys_review_extraction_validation_agent
pip install -r requirements.txt
playwright install chromium
```

### 2. Prepare Your Inputs

| Input | Description |
|-------|-------------|
| `Articles/` folder | Source PDFs (same ones used for extraction) |
| `extracted_studies.xlsx` | Output from the Extraction Agent |

### 3. Run

**Browser mode (default — free, no API key):**
```bash
python healing_pipeline.py --mode browser --browser chrome
```

**API mode (faster):**
```bash
python healing_pipeline.py --api-key YOUR_KEY
```

**API mode with key rotation (no rate-limit waits):**
```bash
python healing_pipeline.py --api-kit API_KIT.txt
```

The pipeline runs all 3 phases automatically and outputs a clean, validated dataset.

---

## How It Works

```
                    ┌──────────────────────────┐
                    │   healing_pipeline.py     │
                    └────────┬─────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
  ┌───────────┐       ┌───────────┐       ┌───────────┐
  │  Phase 1  │       │  Phase 2  │       │  Phase 3  │
  │ VALIDATE  │──→──  │ SELF-HEAL │──→──  │ RE-VALIDATE│
  │           │ FAIL  │           │       │           │
  │ Compare   │       │ Remove    │       │ Verify    │
  │ extracted │       │ failed    │       │ healed    │
  │ data vs   │       │ rows,     │       │ rows are  │
  │ source PDF│       │ re-extract│       │ now correct│
  └───────────┘       └───────────┘       └───────────┘
```

### Phase 1 — Validation

For each row in `extracted_studies.xlsx`:
1. Uploads the source PDF to Gemini
2. Sends: *"Here's the extracted data + the PDF. Are they consistent?"*
3. Gemini returns `PASS` or `FAIL` with discrepancy details
4. Discrepancies are classified:

| Severity | Example | Action |
|----------|---------|--------|
| **CRITICAL** | Wrong sample size, swapped cohorts | → FAIL → triggers Phase 2 |
| **MINOR** | Rounding <1%, synonym variation | → Downgraded to PASS |

```
Row 1 (Sanjay 1993): ✔ PASS
Row 2 (Ouyang 2017): ✘ FAIL — 3 CRITICAL discrepancies
   Study Design: "retrospective" ≠ "RCT"
   Sample Size:  "94" ≠ "9999"
   Diagnosis:    "Giant Cell Tumor" ≠ "Knee Osteoarthritis"
```

### Phase 2 — Self-Healing

For every FAIL row:
1. Removes the failed entry from the dataset
2. Re-extracts data from the original PDF using `gemini_extractor.py`
3. Appends the corrected extraction back to the dataset

### Phase 3 — Re-Validation

1. Re-validates all healed rows (same process as Phase 1)
2. Generates `healing_comparison_report.xlsx` — before/after for every changed field
3. Saves final audit trail to `validation_discrepancies.xlsx`

---

## Output

| File | Description |
|------|-------------|
| `extracted_studies.xlsx` | Final clean dataset (auto-healed) |
| `validation_discrepancies.xlsx` | All discrepancies found in Phase 1 |
| `healing_comparison_report.xlsx` | Before/after field values for healed rows |
| `pipeline_summary.json` | Machine-readable pipeline run summary |
| `pipeline_run.log` | Full timestamped execution log |
| `missing_data_justifications.json` | Per-field null reasons from re-extraction |

---

## Cross-Validation

Compare outputs from two independent extraction runs:

```bash
python cross_validate_extraction.py --file-a extracted_A.xlsx --file-b extracted_B.xlsx
```

Produces:
- `cross_agent_discrepancies.xlsx` — field-level disagreements
- `cross_validation_summary.json` — agreement rate, critical vs minor counts

---

## Advanced

### Command-Line Options

```
python healing_pipeline.py --help

Options:
  --mode MODE         Execution mode: api or browser              [default: api]
  --browser CHANNEL   Browser channel (chrome, msedge)
  --api-key KEY       Single Gemini API key
  --api-kit FILE      File with multiple API keys (one per line)
  --model NAME        Gemini model (default: gemini-2.5-flash)
  --limit N           Process only first N rows
```

### API Key Rotation

For large datasets, provide multiple keys to bypass rate limits:

```
API_KIT.txt:
AIzaSyABC...
AIzaSyDEF...
AIzaSyGHI...
```

On rate limit (429), the pipeline switches to the next key with a 2-second pause. After cycling all keys, it waits 60 seconds before the next cycle.

### End-to-End Testing

```bash
python e2e_test.py
```

Runs 23 automated tests covering:

| Category | Tests | Description |
|----------|-------|-------------|
| Deduplication | 4 | DOI, PMID, exact title, fuzzy title matching |
| Dual-Pass Screening | 4 | Include, exclude, competing diagnosis, review detection |
| Post-Processing | 6 | Percentage conversion, null reason logging |
| API Extraction | 6 | PDF upload, JSON parsing, field completeness |
| API Validation | 3 | PASS/FAIL detection, discrepancy classification |

---

## Project Structure

```
validation_agent/
├── healing_pipeline.py              # Main 3-phase pipeline orchestrator
├── validation_agent.py              # Core validation logic
├── gemini_extractor.py              # PDF extraction (used in Phase 2)
├── cross_validate_extraction.py     # Cross-agent comparison tool
├── e2e_test.py                      # End-to-end test suite (23 tests)
├── template_extracted_studies.xlsx   # Blank output template
├── requirements.txt                 # Dependencies
├── LICENSE                          # MIT License
└── Articles/                        # Source PDFs (gitignored)
```

---

## API Configuration

| Parameter | Validation | Extraction (Phase 2) |
|-----------|-----------|---------------------|
| `temperature` | `0.2` | `0.2` |
| `max_output_tokens` | `2048` | `8192` |
| Model | `gemini-2.5-flash` | `gemini-2.5-flash` |

> Extraction uses `8192` tokens to prevent truncation of the full schema JSON.

---

## Modes

| Mode | Command | Speed | Cost |
|------|---------|-------|------|
| **Browser** (default) | `python healing_pipeline.py --browser chrome` | ~30s/row | Free |
| **API** | `python healing_pipeline.py --api-key KEY` | ~10s/row | Free tier |
| **API + rotation** | `python healing_pipeline.py --api-kit API_KIT.txt` | ~10s/row | Free tier × N keys |

---

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built for systematic reviewers. Errors in, clean data out.</sub>
</p>
