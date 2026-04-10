"""
e2e_test.py — End-to-End Test for the Systematic Review Pipeline

Tests each phase of the pipeline with real PDFs to verify
all components work together as described in the manuscript.
"""

import os
import sys
import json
import time
import shutil
import traceback
import pandas as pd

API_KEY = "AIzaSyDWfx3KJoRONENZESBVSehQRNXZ59-p_tw"
MODEL = "gemini-2.5-flash"
TEST_DIR = os.path.dirname(os.path.abspath(__file__))

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"

results = []


def log_result(phase, test_name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append({"phase": phase, "test": test_name, "passed": passed, "detail": detail})
    print(f"  {status} {test_name}" + (f" -- {detail}" if detail else ""))


def phase_header(name):
    print(f"\n{'='*60}")
    print(f"  PHASE: {name}")
    print(f"{'='*60}")


# ============================================================
# PHASE 1: DEDUPLICATION
# ============================================================
def test_deduplication():
    phase_header("DEDUPLICATION")
    
    # Create synthetic test data
    test_ris = """TY  - JOUR
TI  - Giant cell tumor of the cervical spine: a case report
AU  - Sanjay, BKS
AU  - Sim, FH
PY  - 1993
DO  - 10.1234/test001
AB  - We report a rare case of giant cell tumor in the cervical spine.
ER  - 

TY  - JOUR
TI  - Giant cell tumor of the cervical spine: a case report
AU  - Sanjay, BKS
AU  - Sim, FH
PY  - 1993
DO  - 10.1234/test001
AB  - We report a rare case of giant cell tumor in the cervical spine.
ER  - 

TY  - JOUR
TI  - Surgical management of giant cell tumors of the cervical spine
AU  - Ouyang, HQ
AU  - Deng, ZY
PY  - 2017
DO  - 10.1234/test002
AB  - Giant cell tumors of the cervical spine are rare. We review surgical outcomes.
ER  - 
"""
    test_file = os.path.join(TEST_DIR, "test_input.ris")
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write(test_ris)
    
    # Add dedup_agent to path
    dedup_dir = os.path.join(os.path.dirname(TEST_DIR), "dedup_agent")
    sys.path.insert(0, dedup_dir)
    
    try:
        from deduplicate_files import parse_ris, process_file, Record
        
        records = parse_ris(test_file)
        log_result("Dedup", "RIS parser loads 3 records", len(records) == 3, f"Got {len(records)}")
        
        # Check abstract extraction
        has_abstract = all(r.abstract and len(r.abstract) > 10 for r in records)
        log_result("Dedup", "Abstract field extracted", has_abstract,
                   f"Abstracts: {[len(r.abstract) for r in records]}")
        
        # Check deduplication
        seen_dois = set()
        seen_titles = set()
        unique = []
        deduped, original_count = process_file(records, "test_input.ris", seen_dois, seen_titles, unique)
        
        log_result("Dedup", "Duplicate detected (3->2)", len(deduped) == 2, 
                   f"3 in -> {len(deduped)} kept")
        
        # Check title preservation
        titles = [r.title for r in deduped]
        log_result("Dedup", "Titles preserved in output", all(len(t) > 5 for t in titles),
                   f"Titles: {titles}")
        
    except Exception as e:
        log_result("Dedup", "Deduplication test", False, str(e))
        traceback.print_exc()
    finally:
        sys.path.pop(0)
        if os.path.exists(test_file):
            os.remove(test_file)


# ============================================================
# PHASE 2: SCREENING (Dual-Pass)
# ============================================================
def test_screening():
    phase_header("SCREENING (Dual-Pass)")
    
    # Create synthetic parsed_articles.json
    test_articles = [
        {
            "key": "sanjay1993",
            "title": "Giant cell tumor of the cervical spine: a case report",
            "abstract": "We report a rare case of giant cell tumor in the cervical spine treated surgically.",
            "author": "Sanjay, BKS and Sim, FH",
            "year": "1993",
            "doi": "10.1234/test001"
        },
        {
            "key": "ouyang2017",
            "title": "Surgical management of giant cell tumors of the cervical spine",
            "abstract": "Giant cell tumors of the cervical spine are rare. We review surgical outcomes in 15 patients.",
            "author": "Ouyang, HQ and Deng, ZY",
            "year": "2017",
            "doi": "10.1234/test002"
        },
        {
            "key": "smith2020",
            "title": "Systematic review of osteoblastoma treatment",
            "abstract": "This systematic review examines osteoblastoma treatment outcomes.",
            "author": "Smith, J",
            "year": "2020",
            "doi": "10.1234/test003"
        }
    ]
    
    screening_dir = os.path.join(os.path.dirname(TEST_DIR), "screening_agent")
    json_path = os.path.join(screening_dir, "test_parsed_articles.json")
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(test_articles, f, indent=2)
    
    sys.path.insert(0, screening_dir)
    
    try:
        from screen_articles import screen_single_pass, dual_pass_screening
        
        # Test single pass
        pass_results = screen_single_pass(test_articles)
        log_result("Screening", "Single pass runs", len(pass_results) == 3, f"Got {len(pass_results)} results")
        
        included = [r for r in pass_results if r['Decision'] == 'Include']
        excluded = [r for r in pass_results if r['Decision'] == 'Exclude']
        log_result("Screening", "GCT cervical articles included", len(included) == 2,
                   f"Included: {len(included)}, Excluded: {len(excluded)}")
        log_result("Screening", "Non-GCT article excluded", len(excluded) == 1,
                   f"Excluded: {[r['Title'][:30] for r in excluded]}")
        
        # Test dual pass
        agreed, disagreements, _ = dual_pass_screening(json_path)
        log_result("Screening", "Dual-pass agreement 100%", len(disagreements) == 0,
                   f"Agreed: {len(agreed)}, Disagreements: {len(disagreements)}")
        
    except Exception as e:
        log_result("Screening", "Screening test", False, str(e))
        traceback.print_exc()
    finally:
        sys.path.pop(0)
        for f in [json_path, 
                  os.path.join(screening_dir, "screening_results.csv"),
                  os.path.join(screening_dir, "included_articles.ris"),
                  os.path.join(screening_dir, "screening_run.log")]:
            if os.path.exists(f):
                os.remove(f)


# ============================================================
# PHASE 3: EXTRACTION (API Mode)
# ============================================================
def test_extraction_api():
    phase_header("EXTRACTION (API Mode)")
    
    import google.generativeai as genai
    
    try:
        genai.configure(api_key=API_KEY)
        model = genai.GenerativeModel(MODEL, generation_config={
            "temperature": 0.2,
            "max_output_tokens": 2048
        })
        
        # Test with one PDF
        pdf_path = os.path.join(TEST_DIR, "Articles", "BKS Sanjay 1993.pdf")
        if not os.path.exists(pdf_path):
            log_result("Extraction", "PDF file exists", False, f"Not found: {pdf_path}")
            return
        
        log_result("Extraction", "PDF file exists", True, pdf_path)
        
        # Upload and extract
        print("  Uploading PDF to Gemini API...")
        uploaded = genai.upload_file(pdf_path)
        while uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = genai.get_file(uploaded.name)
        
        log_result("Extraction", "PDF upload successful", uploaded.state.name == "ACTIVE",
                   f"State: {uploaded.state.name}")
        
        # Simple extraction prompt
        prompt = """Extract from this PDF:
1. Title of the study
2. Authors
3. Year
4. Study type (case report, cohort, etc.)
5. Sample size

Return as JSON: {"title": "...", "authors": "...", "year": "...", "study_type": "...", "sample_size": "..."}
Return ONLY the JSON."""

        print("  Generating extraction...")
        response = model.generate_content([uploaded, prompt])
        
        log_result("Extraction", "API response received", response.text is not None,
                   f"Response length: {len(response.text)} chars")
        
        # Parse JSON
        import re
        text = response.text
        json_match = re.search(r'\{.*\}', text, re.S)
        if json_match:
            data = json.loads(json_match.group())
            log_result("Extraction", "Valid JSON returned", True, f"Keys: {list(data.keys())}")
            log_result("Extraction", "Title extracted", bool(data.get('title')),
                       f"Title: {str(data.get('title', ''))[:50]}")
            log_result("Extraction", "Year extracted", bool(data.get('year')),
                       f"Year: {data.get('year')}")
        else:
            log_result("Extraction", "Valid JSON returned", False, f"Raw: {text[:100]}")
            
        # Clean up uploaded file
        try:
            genai.delete_file(uploaded.name)
        except:
            pass
            
    except Exception as e:
        log_result("Extraction", "Extraction API test", False, str(e))
        traceback.print_exc()


# ============================================================
# PHASE 4: VALIDATION (API Mode)
# ============================================================
def test_validation_api():
    phase_header("VALIDATION (API Mode)")
    
    import google.generativeai as genai
    
    try:
        genai.configure(api_key=API_KEY)
        model = genai.GenerativeModel(MODEL, generation_config={
            "temperature": 0.2,
            "max_output_tokens": 2048
        })
        
        pdf_path = os.path.join(TEST_DIR, "Articles", "BKS Sanjay 1993.pdf")
        if not os.path.exists(pdf_path):
            log_result("Validation", "PDF exists", False)
            return
        
        # Create test data with a DELIBERATE ERROR
        test_row = {
            "Study ID": "Sanjay 1993",
            "Year": "1993",
            "Sample Size (Total)": "999",  # WRONG — deliberate error
            "Study Design": "Case Report",
        }
        
        print("  Uploading PDF for validation...")
        uploaded = genai.upload_file(pdf_path)
        while uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = genai.get_file(uploaded.name)
        
        # Validation prompt
        sys.path.insert(0, TEST_DIR)
        from validation_agent import create_validation_prompt
        prompt = create_validation_prompt(test_row)
        sys.path.pop(0)
        
        print("  Running validation against PDF...")
        response = model.generate_content([uploaded, prompt])
        
        log_result("Validation", "Validation response received", response.text is not None,
                   f"Length: {len(response.text)} chars")
        
        # Parse result
        import re
        json_match = re.search(r'\{.*\}', response.text, re.S)
        if json_match:
            data = json.loads(json_match.group())
            status = data.get('status', 'UNKNOWN')
            disc = data.get('discrepancies', [])
            
            log_result("Validation", "Returns valid JSON with status", status in ['PASS', 'FAIL'],
                       f"Status: {status}")
            log_result("Validation", "Catches planted error (sample_size=999)", 
                       status == 'FAIL' or len(disc) > 0,
                       f"Discrepancies: {len(disc)}")
            
            if disc:
                for d in disc[:2]:
                    print(f"    [{d.get('severity','?')}] {d.get('field','?')}: {d.get('description','')[:60]}")
        else:
            log_result("Validation", "Returns valid JSON", False, f"Raw: {response.text[:100]}")
        
        try:
            genai.delete_file(uploaded.name)
        except:
            pass
            
    except Exception as e:
        log_result("Validation", "Validation test", False, str(e))
        traceback.print_exc()


# ============================================================
# PHASE 5: POST-PROCESSING (Deterministic Conversion)
# ============================================================
def test_post_processing():
    phase_header("POST-PROCESSING")
    
    sys.path.insert(0, TEST_DIR)
    try:
        from gemini_extractor import deterministic_pct_to_count, save_null_reasons_log
        
        # Test percentage conversion
        test_data = {
            "Sample Size (Total)": "500",
            "Surgical Site Infection (SSI)": "10%",
            "Mortality": "2.5%",
            "Study ID": "Test 2024",
            "Other Notes": "No complications"  # Should NOT be converted
        }
        
        result = deterministic_pct_to_count(test_data.copy())
        
        log_result("PostProc", "SSI 10% -> 50/500 (10%)", "50/500" in str(result.get("Surgical Site Infection (SSI)", "")),
                   f"Got: {result.get('Surgical Site Infection (SSI)')}")
        log_result("PostProc", "Mortality 2.5% -> 12/500 (2.5%)", "12/500" in str(result.get("Mortality", "")),
                   f"Got: {result.get('Mortality')}")
        log_result("PostProc", "Non-pct field unchanged", result.get("Other Notes") == "No complications",
                   f"Got: {result.get('Other Notes')}")
        
        # Test null reasons log
        test_reasons_data = {
            "Study ID": "Test",
            "_null_reasons": {
                "BMI": "Not reported in study",
                "Fusion Success": "Outcome not assessed"
            }
        }
        
        log_path = os.path.join(TEST_DIR, "test_justifications.json")
        save_null_reasons_log(test_reasons_data.copy(), "test_study.pdf", log_path=log_path)
        
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                saved = json.load(f)
            log_result("PostProc", "Null reasons saved to JSON", "test_study.pdf" in saved,
                       f"Keys: {list(saved.keys())}")
            log_result("PostProc", "Reasons correctly persisted", 
                       saved.get("test_study.pdf", {}).get("BMI") == "Not reported in study",
                       f"BMI: {saved.get('test_study.pdf', {}).get('BMI')}")
            os.remove(log_path)
        else:
            log_result("PostProc", "Null reasons saved", False, "File not created")
        
        # Verify _null_reasons was popped from data (use same reference, not .copy())
        test_reasons_ref = {
            "Study ID": "Test",
            "_null_reasons": {
                "BMI": "Not reported in study",
                "Fusion Success": "Outcome not assessed"
            }
        }
        save_null_reasons_log(test_reasons_ref, "test_study2.pdf", log_path=log_path)
        log_result("PostProc", "_null_reasons removed from data", "_null_reasons" not in test_reasons_ref,
                   "Key properly removed")
        if os.path.exists(log_path):
            os.remove(log_path)
        
    except Exception as e:
        log_result("PostProc", "Post-processing test", False, str(e))
        traceback.print_exc()
    finally:
        sys.path.pop(0)


# ============================================================
# FINAL SUMMARY
# ============================================================
def print_summary():
    print(f"\n{'='*60}")
    print(f"  END-TO-END TEST SUMMARY")
    print(f"{'='*60}")
    
    total = len(results)
    passed = sum(1 for r in results if r['passed'])
    failed = total - passed
    
    print(f"  Total Tests:  {total}")
    print(f"  Passed:       {passed}")
    print(f"  Failed:       {failed}")
    print(f"  Success Rate: {passed/total*100:.0f}%")
    
    if failed > 0:
        print(f"\n  FAILED TESTS:")
        for r in results:
            if not r['passed']:
                print(f"    [{r['phase']}] {r['test']}: {r['detail']}")
    
    print(f"{'='*60}")
    
    # Save results
    with open("e2e_test_results.json", "w") as f:
        json.dump({
            "total": total,
            "passed": passed,
            "failed": failed,
            "tests": results
        }, f, indent=2)
    print(f"  Results saved: e2e_test_results.json")
    
    return failed == 0


# ============================================================
# PHASE 6: POST-PROCESSING EDGE CASES
# ============================================================
def test_post_processing_edge_cases():
    phase_header("POST-PROCESSING EDGE CASES")
    
    sys.path.insert(0, TEST_DIR)
    try:
        from gemini_extractor import deterministic_pct_to_count
        
        # Edge 1: No sample size -> should return data unchanged
        no_sample = {
            "Study ID": "Test",
            "Complication Rate": "15%",
        }
        result = deterministic_pct_to_count(no_sample.copy())
        log_result("EdgeCase", "No sample size -> pct unchanged", 
                   result.get("Complication Rate") == "15%",
                   f"Got: {result.get('Complication Rate')}")
        
        # Edge 2: Non-percentage values should NOT be converted
        mixed = {
            "Sample Size (Total)": "100",
            "Mean Age": "45.2",
            "Title": "A Study",
            "Follow-up": "12 months",
            "Rate": "30%",
        }
        result = deterministic_pct_to_count(mixed.copy())
        log_result("EdgeCase", "Non-pct 'Mean Age' unchanged",
                   result.get("Mean Age") == "45.2",
                   f"Got: {result.get('Mean Age')}")
        log_result("EdgeCase", "Non-pct 'Follow-up' unchanged",
                   result.get("Follow-up") == "12 months",
                   f"Got: {result.get('Follow-up')}")
        log_result("EdgeCase", "Pct '30%' converted with n=100",
                   "30/100" in str(result.get("Rate", "")),
                   f"Got: {result.get('Rate')}")
        
        # Edge 3: 0% and 100%
        extremes = {
            "Sample Size (Total)": "200",
            "Zero Rate": "0%",
            "Full Rate": "100%",
        }
        result = deterministic_pct_to_count(extremes.copy())
        log_result("EdgeCase", "0% -> 0/200 (0%)",
                   "0/200" in str(result.get("Zero Rate", "")),
                   f"Got: {result.get('Zero Rate')}")
        log_result("EdgeCase", "100% -> 200/200 (100%)",
                   "200/200" in str(result.get("Full Rate", "")),
                   f"Got: {result.get('Full Rate')}")
        
        # Edge 4: Empty dict
        result = deterministic_pct_to_count({})
        log_result("EdgeCase", "Empty dict handled", result == {}, f"Got: {result}")
        
        # Edge 5: Sample size = 0 or non-numeric
        bad_sample = {
            "Sample Size (Total)": "N/A",
            "Rate": "50%",
        }
        result = deterministic_pct_to_count(bad_sample.copy())
        log_result("EdgeCase", "Non-numeric sample size -> unchanged",
                   result.get("Rate") == "50%",
                   f"Got: {result.get('Rate')}")
        
    except Exception as e:
        log_result("EdgeCase", "Edge case test", False, str(e))
        traceback.print_exc()
    finally:
        sys.path.pop(0)


# ============================================================
# PHASE 7: EXTRACTION — BOTH PDFs
# ============================================================
def test_extraction_both_pdfs():
    phase_header("EXTRACTION - BOTH PDFs")
    
    import google.generativeai as genai
    
    try:
        genai.configure(api_key=API_KEY)
        model = genai.GenerativeModel(MODEL, generation_config={
            "temperature": 0.2,
            "max_output_tokens": 2048
        })
        
        pdfs = [
            ("BKS Sanjay 1993.pdf", "1993", "sanjay"),
            ("HQ Ouyang 2017.pdf", "2017", "ouyang"),
        ]
        
        prompt = """Extract from this PDF:
1. Title of the study
2. Authors (first author last name)
3. Year of publication
4. Study type (case report, case series, cohort, RCT, etc.)
5. Number of patients / sample size
6. Main diagnosis or condition studied

Return as JSON: {"title": "...", "first_author": "...", "year": "...", "study_type": "...", "sample_size": "...", "diagnosis": "..."}
Return ONLY the JSON."""

        for pdf_name, expected_year, author_hint in pdfs:
            pdf_path = os.path.join(TEST_DIR, "Articles", pdf_name)
            if not os.path.exists(pdf_path):
                log_result("BothPDF", f"{pdf_name} exists", False, "Not found")
                continue
            
            print(f"  Uploading {pdf_name}...")
            uploaded = genai.upload_file(pdf_path)
            while uploaded.state.name == "PROCESSING":
                time.sleep(2)
                uploaded = genai.get_file(uploaded.name)
            
            print(f"  Extracting {pdf_name}...")
            response = model.generate_content([uploaded, prompt])
            
            import re
            json_match = re.search(r'\{.*\}', response.text, re.S)
            if json_match:
                data = json.loads(json_match.group())
                log_result("BothPDF", f"{pdf_name}: valid JSON", True,
                           f"Keys: {list(data.keys())}")
                log_result("BothPDF", f"{pdf_name}: correct year",
                           str(data.get('year', '')).startswith(expected_year),
                           f"Expected {expected_year}, got {data.get('year')}")
                log_result("BothPDF", f"{pdf_name}: author detected",
                           author_hint.lower() in str(data.get('first_author', '')).lower() or
                           author_hint.lower() in str(data.get('title', '')).lower(),
                           f"Author: {data.get('first_author')}")
                log_result("BothPDF", f"{pdf_name}: sample size extracted",
                           data.get('sample_size') is not None and str(data.get('sample_size')) != 'null',
                           f"N={data.get('sample_size')}")
            else:
                log_result("BothPDF", f"{pdf_name}: JSON parse", False, 
                           f"Raw: {response.text[:80]}")
            
            try:
                genai.delete_file(uploaded.name)
            except:
                pass
            
            time.sleep(3)  # Rate limit safety
            
    except Exception as e:
        log_result("BothPDF", "Both PDF extraction", False, str(e))
        traceback.print_exc()


# ============================================================
# PHASE 8: VALIDATION — PASS CASE (correct data)
# ============================================================
def test_validation_pass_case():
    phase_header("VALIDATION - CORRECT DATA (should PASS)")
    
    import google.generativeai as genai
    
    try:
        genai.configure(api_key=API_KEY)
        model = genai.GenerativeModel(MODEL, generation_config={
            "temperature": 0.2,
            "max_output_tokens": 2048
        })
        
        pdf_path = os.path.join(TEST_DIR, "Articles", "BKS Sanjay 1993.pdf")
        if not os.path.exists(pdf_path):
            log_result("PassCase", "PDF exists", False)
            return
        
        # CORRECT data — should validate as PASS
        correct_row = {
            "Study ID": "Sanjay 1993",
            "Year": "1993",
            "Study Design": "Case Series",
        }
        
        print("  Uploading PDF for PASS validation test...")
        uploaded = genai.upload_file(pdf_path)
        while uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = genai.get_file(uploaded.name)
        
        sys.path.insert(0, TEST_DIR)
        from validation_agent import create_validation_prompt
        prompt = create_validation_prompt(correct_row)
        sys.path.pop(0)
        
        print("  Validating correct data against PDF...")
        response = model.generate_content([uploaded, prompt])
        
        import re
        json_match = re.search(r'\{.*\}', response.text, re.S)
        if json_match:
            data = json.loads(json_match.group())
            status = data.get('status', 'UNKNOWN')
            disc = data.get('discrepancies', [])
            critical = [d for d in disc if d.get('severity') == 'CRITICAL']
            
            log_result("PassCase", "Correct data returns PASS or only MINOR",
                       status == 'PASS' or len(critical) == 0,
                       f"Status: {status}, Critical: {len(critical)}, Minor: {len(disc) - len(critical)}")
        else:
            log_result("PassCase", "JSON response", False, f"Raw: {response.text[:80]}")
        
        try:
            genai.delete_file(uploaded.name)
        except:
            pass
            
    except Exception as e:
        log_result("PassCase", "Pass case test", False, str(e))
        traceback.print_exc()


# ============================================================
# PHASE 9: FULL HEALING PIPELINE INTEGRATION
# ============================================================
def test_healing_pipeline_integration():
    phase_header("HEALING PIPELINE INTEGRATION")
    
    import google.generativeai as genai
    
    try:
        # Create a minimal extracted_studies.xlsx with one deliberate error
        test_xlsx = os.path.join(TEST_DIR, "extracted_studies.xlsx")
        backup_xlsx = os.path.join(TEST_DIR, "extracted_studies_backup.xlsx")
        
        # Backup existing file if present
        if os.path.exists(test_xlsx):
            shutil.copy2(test_xlsx, backup_xlsx)
        
        # Create test data with one correct and one wrong row
        test_data = pd.DataFrame([
            {
                "Source File": "BKS Sanjay 1993.pdf",
                "First Author (Year)": "Sanjay (1993)",
                "Study Design": "Case Series",
                "Sample Size (Total)": "24",
            },
            {
                "Source File": "HQ Ouyang 2017.pdf",
                "First Author (Year)": "Ouyang (2017)",
                "Study Design": "WRONG_VALUE_FOR_TESTING",  # Deliberate error
                "Sample Size (Total)": "9999",               # Deliberate error  
            }
        ])
        test_data.to_excel(test_xlsx, index=False)
        log_result("Pipeline", "Test xlsx created with planted errors", True,
                   f"{len(test_data)} rows, 1 correct + 1 wrong")
        
        # Run the healing pipeline via API
        sys.path.insert(0, TEST_DIR)
        from healing_pipeline import setup_api, validate_row_api, resolve_pdf_path
        
        genai_module, model = setup_api(API_KEY, MODEL)
        log_result("Pipeline", "API setup successful", True, f"Model: {MODEL}")
        
        # Validate both rows
        df = pd.read_excel(test_xlsx)
        validation_results = []
        
        for idx, row in df.iterrows():
            source = row['Source File']
            pdf_path = resolve_pdf_path(source)
            if not pdf_path:
                log_result("Pipeline", f"PDF found for {source}", False)
                continue
            
            print(f"  Validating: {source}...")
            result = validate_row_api(genai_module, model, pdf_path, row.to_dict())
            result['Source File'] = source
            validation_results.append(result)
            
            status = result.get('status', 'ERROR')
            print(f"    Status: {status}")
            time.sleep(3)
        
        # Check results
        statuses = [r.get('status') for r in validation_results]
        log_result("Pipeline", "Both rows validated", len(validation_results) == 2,
                   f"Statuses: {statuses}")
        
        # The wrong row (Ouyang with WRONG values) should FAIL
        ouyang_result = next((r for r in validation_results if 'Ouyang' in str(r.get('Source File', ''))), None)
        if ouyang_result:
            log_result("Pipeline", "Planted error detected in Ouyang row",
                       ouyang_result.get('status') == 'FAIL',
                       f"Status: {ouyang_result.get('status')}, "
                       f"Discrepancies: {len(ouyang_result.get('discrepancies', []))}")
        
        # Check pipeline summary generation
        from healing_pipeline import _save_summary
        summary = _save_summary(
            validation_results,
            initial_failed=[r['Source File'] for r in validation_results if r.get('status') == 'FAIL'],
            unresolved=[],
            heal_attempts=0
        )
        
        log_result("Pipeline", "Pipeline summary generated", 
                   os.path.exists('pipeline_summary.json'),
                   f"Status: {summary.get('status')}")
        log_result("Pipeline", "Summary has correct structure",
                   all(k in summary for k in ['total_validated', 'passed', 'failed', 'critical_errors']),
                   f"Keys: {list(summary.keys())[:6]}...")
        
        sys.path.pop(0)
        
        # Restore backup
        if os.path.exists(backup_xlsx):
            shutil.move(backup_xlsx, test_xlsx)
        elif os.path.exists(test_xlsx):
            os.remove(test_xlsx)
        
        # Clean up generated files
        for f in ['pipeline_summary.json', 'validation_discrepancies.xlsx', 'pipeline_run.log']:
            if os.path.exists(os.path.join(TEST_DIR, f)):
                pass  # Keep for audit
                
    except Exception as e:
        log_result("Pipeline", "Pipeline integration test", False, str(e))
        traceback.print_exc()
        # Restore backup on failure
        backup_xlsx = os.path.join(TEST_DIR, "extracted_studies_backup.xlsx")
        test_xlsx = os.path.join(TEST_DIR, "extracted_studies.xlsx")
        if os.path.exists(backup_xlsx):
            shutil.move(backup_xlsx, test_xlsx)


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  SYSTEMATIC REVIEW PIPELINE -- COMPREHENSIVE E2E TEST")
    print(f"  Model: {MODEL} | API Key: ...{API_KEY[-4:]}")
    print(f"{'='*60}")
    
    # Local-only tests (no API calls)
    test_deduplication()
    test_screening()
    test_post_processing()
    test_post_processing_edge_cases()
    
    # API tests — both PDFs
    test_extraction_api()       # Sanjay 1993 (original)
    time.sleep(3)
    test_extraction_both_pdfs() # Both PDFs
    time.sleep(3)
    
    # Validation — both PASS and FAIL cases
    test_validation_api()        # FAIL case (planted error)
    time.sleep(3)
    test_validation_pass_case()  # PASS case (correct data)
    time.sleep(3)
    
    # Full pipeline integration
    test_healing_pipeline_integration()
    
    success = print_summary()
    sys.exit(0 if success else 1)

