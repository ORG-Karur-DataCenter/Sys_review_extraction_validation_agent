# Systematic Review Extraction & Validation Agent

This tool automates the extraction of data from scientific PDF articles and validates the accuracy of that data using Google Gemini. It features a self-healing pipeline that automatically detects discrepancies and re-extracts data from failed articles.

## Key Features
- **Automated Extraction:** Extracts complex data fields including Study Design, Sample Size, Comorbidities, and Outcomes.
- **Smart Validation:** Compares extracted data against the original PDF text.
    - **CRITICAL Errors:** (Data mismatch >1%, swapped cohorts) trigger re-extraction.
    - **MINOR Errors:** (Formatting, synonyms) are flagged but allowed to PASS.
- **Self-Healing:** Automatically re-processes articles that fail validation.
- **Reporting:** Generates detailed reports on what data was changed (`healing_comparison_report.xlsx`) and what discrepancies remain (`validation_discrepancies.xlsx`).
    - *Note: The main `extracted_studies.xlsx` remains clean and free of validation metadata.*

## Setup
1.  **Environment:** Ensure Python 3.x is installed.
2.  **Dependencies:** Install required packages (Playwright, Pandas, etc.).
    ```bash
    pip install playwright pandas openpyxl tqdm colorama
    playwright install
    ```
3.  **Browser:** This agent uses a persistent Chrome profile (`C:\Users\HP\gemini_chrome_profile`). You must be logged into Gemini in this profile.

## Usage

### 1. Full Pipeline (Processing All Files)
Run the master orchestration script. This will perform validation, self-healing, and final reporting for all files.
```bash
python do_it_all.py --browser chrome
```

### 2. Testing / Limiting Scope
To process only the first N files (useful for testing):
```bash
python do_it_all.py --limit 5 --browser chrome
```

### 3. File Inputs/Outputs
- **Input:** `Articles/` directory containing PDF references.
- **Input Template:** `template_extracted_studies.xlsx` (Used to reset/structure the output).
- **Output:** `extracted_studies.xlsx` (The final data).
- **Logs:**
    - `validation_discrepancies.xlsx`: Detailed list of all finding during validation.
    - `healing_comparison_report.xlsx`: Shows exactly what values changed during the self-healing process.

## Validation Logic
The agent uses a tiered system to judge accuracy:
- **PASS:** Data is 100% correct OR has only **MINOR** issues (rounding <1%, "Male" vs "Men").
- **FAIL:** Data has **CRITICAL** issues (wrong numbers, missing data).
- **FAIL:** Data has **CRITICAL** issues (wrong numbers, missing data).
- **Feedback:** Detailed feedback is logged in sections `validation_discrepancies.xlsx`. The main output file is kept clean.
