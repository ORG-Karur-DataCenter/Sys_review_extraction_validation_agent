
import os
import time
import json
import pandas as pd
import argparse
import re
from playwright.sync_api import sync_playwright
from tqdm import tqdm
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# Configuration
ARTICLES_DIR = 'Articles'
OUTPUT_FILE = 'extracted_studies.xlsx'
GEMINI_URL = "https://gemini.google.com/app"

# Column Definitions
STUDY_CHARACTERISTICS = [
    ("Study ID", "First author + year (e.g., Barkyoumb 2025)"),
    ("Journal", "Source of publication"),
    ("Country/Region", "Study location(s)"),
    ("Study Design", "Retrospective cohort, RCT, meta-analysis, etc."),
    ("Database/Setting", "National claims, single-center, multicenter, etc."),
    ("Sample Size (Total)", "Number of patients included"),
    ("GLP-1 RA Cohort Size", "Number of patients exposed"),
    ("Control Cohort Size", "Number of patients not exposed"),
    ("Age (mean ± SD)", "Baseline age"),
    ("Sex (% male/female)", "Gender distribution"),
    ("BMI (mean ± SD)", "Baseline BMI"),
    ("Diabetes Status (%)", "% with T2DM"),
    ("Other Comorbidities", "Hypertension, CAD, CKD, smoking, etc."),
    ("GLP-1 Agent(s)", "Semaglutide, liraglutide, tirzepatide, etc."),
    ("Exposure Definition", "Pre-op, peri-op, post-op; duration window"),
    ("Dosing Regimen", "Weekly vs daily, dose escalation"),
    ("Surgical Procedure", "ACDF, PCF, TLIF, PLIF, lumbar fusion, decompression"),
    ("Levels Fused", "Single vs multilevel"),
    ("Follow-up Duration", "90 days, 6 months, 1 year, 2 years, etc."),
    ("Matching/Adjustment", "Propensity score, covariates controlled"),
    ("Risk of Bias", "ROBINS-I, NOS, etc.")
]

OUTCOMES = [
    ("Surgical Site Infection (SSI)", "Yes/No, % incidence"),
    ("Wound Complications", "Dehiscence, delayed healing"),
    ("Venous Thromboembolism (VTE)", "DVT/PE incidence"),
    ("Mortality", "30-day, 90-day, 1-year"),
    ("Readmission", "30-day, 90-day, 1-year"),
    ("Reoperation", "Same-level vs adjacent-level"),
    ("Pseudarthrosis", "Radiographic or clinical nonunion"),
    ("Fusion Success", "Solid fusion rates"),
    ("Implant/Hardware Failure", "Breakage, loosening"),
    ("Operative Time", "Mean ± SD"),
    ("Blood Loss", "Mean ± SD"),
    ("Length of Stay (LOS)", "Median/mean days"),
    ("Emergency Department Visits", "Within 90 days"),
    ("Medical Complications", "Anemia, AKI, renal failure, pneumonia"),
    ("Glycemic Control", "HbA1c change, peri-op glucose variability"),
    ("Cardiovascular Events", "MI, stroke"),
    ("Neurological Outcomes", "Dysphagia, mobility deficits"),
    ("Nutritional/Muscle Outcomes", "Lean mass loss, sarcopenia"),
    ("Adverse Drug Events", "Pancreatitis, thyroid cancer, GI symptoms"),
    ("Other Notes", "Any unique findings (e.g., SEL regression, neuroprotection)")
]

ALL_COLUMNS = [c[0] for c in STUDY_CHARACTERISTICS] + [c[0] for c in OUTCOMES if c[0] != "Study ID"]

def create_prompt():
    prompt = "Extract the following information from the attached PDF. Return the result as a valid JSON object where keys are the 'Column Label' and values are the extracted text. If information is missing, use null.\n\n"
    prompt += "--- Study Characteristics ---\n"
    for label, desc in STUDY_CHARACTERISTICS:
        prompt += f"- {label}: {desc}\n"
    
    prompt += "\n--- Outcomes ---\n"
    for label, desc in OUTCOMES:
        if label == "Study ID": continue 
        prompt += f"- {label}: {desc}\n"
    
    # Deterministic percentage-to-count conversion rules (as described in manuscript)
    prompt += "\n\n--- CONVERSION RULES ---\n"
    prompt += "Apply these deterministic rules for data conversion:\n"
    prompt += "1. When a percentage and sample size are available, convert to count: count = round(percentage × sample_size / 100)\n"
    prompt += "2. Report continuous outcomes as 'mean ± SD' format\n"
    prompt += "3. Report binary outcomes as 'events/total (%)' format\n"
    prompt += "4. If only a percentage is given without a denominator, report as-is with '%'\n"

    # Missing-data justification (as described in manuscript)
    prompt += "\n\n--- MISSING DATA ---\n"
    prompt += "For any field where the value is null, add a key '_null_reasons' to the JSON containing a dict mapping each null field name to a brief reason:\n"
    prompt += "e.g., {'_null_reasons': {'BMI (mean ± SD)': 'Not reported in study', 'Fusion Success': 'Outcome not assessed'}}\n"

    prompt += "\n\nCRUCIAL: Verify the extracted data against the PDF one more time before outputting to ensure accuracy. Return ONLY the JSON object, no markdown formatting."
    return prompt

def extract_data_from_page(page, pdf_path, prompt_text):
    print(f"[{os.path.basename(pdf_path)}] Navigating to Gemini...")
    try:
        page.goto(GEMINI_URL, timeout=90000, wait_until="domcontentloaded")
    except:
        print("Page load slow, continuing...")

    # Upload Logic
    print(f"[{os.path.basename(pdf_path)}] Attempting upload...")
    try:
        # Give the UI a moment to settle
        time.sleep(2)
        
        with page.expect_file_chooser(timeout=60000) as fc_info:
            plus_selectors = [
                 "button[aria-label*='Upload']",
                 "button[aria-label*='Add files']",
                 "button[aria-label*='file menu']",
                 "mat-icon:has-text('add')",
                 "span:has-text('add')"
            ]
            
            plus_found = False
            for selector in plus_selectors:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible():
                    print(f"[{os.path.basename(pdf_path)}] Found button with selector: {selector}")
                    btn.click(force=True, timeout=10000)
                    plus_found = True
                    break
            
            if not plus_found:
                print(f"[{os.path.basename(pdf_path)}] Plus button not found.")
                return None
            
            time.sleep(2)
            
            # Clcik the 'Upload' item with retries
            upload_selectors = [
                 "div[role='menuitem']:has-text('Upload')",
                 "span:has-text('Upload')",
                 "li:has-text('Upload')",
                 "[aria-label*='Upload']",
                 ".mat-mdc-menu-item:has-text('Upload')"
            ]

            upload_found = False
            for target in upload_selectors:
                try:
                     upload_item = page.locator(target).first
                     if upload_item.count() > 0 and upload_item.is_visible():
                         upload_item.click(force=True, timeout=5000)
                         upload_found = True
                         break
                except:
                     continue
            
            if not upload_found:
                 # Fallback
                 try:
                    upload_item = page.get_by_text("Upload", exact=False).first
                    upload_item.click(force=True, timeout=5000)
                    upload_found = True
                 except:
                    pass

            if not upload_found:
                 print(f"[{os.path.basename(pdf_path)}] Upload menu item not found.")
                 return None
        
        file_chooser = fc_info.value
        file_chooser.set_files(pdf_path)
        
        # SMART WAIT FOR UPLOAD
        print(f"[{os.path.basename(pdf_path)}] Uploading...")
        try:
            page.locator("file-chip, .file-name, [aria-label*='file']").first.wait_for(state="visible", timeout=60000)
            time.sleep(2)
        except:
            time.sleep(15)
        
    except Exception as e:
        print(f"[{os.path.basename(pdf_path)}] Upload failed: {e}")
        return None

    # Prompting
    try:
        text_area = page.locator("div[contenteditable='true'], textarea").first
        text_area.fill(prompt_text)
        time.sleep(1)
        text_area.press("Enter")
        print(f"[{os.path.basename(pdf_path)}] Prompt sent. Waiting for response...")
        
        # SMART WAIT FOR RESPONSE
        stop_btn = page.locator("button[aria-label*='Stop'], button[aria-label*='Interrupt']")
        time.sleep(5)
        for _ in range(120):
            if stop_btn.count() == 0 or not stop_btn.is_visible():
                response_elements = page.locator("model-response, .model-response-text")
                if response_elements.count() > 0:
                    time.sleep(2)
                    break
            time.sleep(1)
        
        # Extract Response
        response_elements = page.locator("model-response, .model-response-text") 
        if response_elements.count() > 0:
            last_response = response_elements.all()[-1].inner_text()
        else:
            time.sleep(10)
            last_response = page.content()

        # Parse JSON
        start = last_response.find('{')
        end = last_response.rfind('}') + 1
        if start != -1 and end != -1:
            json_str = last_response[start:end]
            try:
                data = json.loads(json_str)
                data['Source File'] = os.path.basename(pdf_path)
                return data
            except:
                print(f"[{os.path.basename(pdf_path)}] JSON parsing failed.")
                return None
        else:
            return None

    except Exception as e:
        print(f"[{os.path.basename(pdf_path)}] Interaction failed: {e}")
        return None

def deterministic_pct_to_count(data):
    """
    Deterministic rules to convert reported percentages into counts
    when sample size is available (as described in manuscript).
    """
    import re as _re
    sample_size = None

    # Try to get total sample size from the data
    for key in ['Sample Size (Total)', 'sample_size', 'Total Sample']:
        val = data.get(key)
        if val:
            try:
                sample_size = int(str(val).replace(',', '').strip())
                break
            except (ValueError, TypeError):
                pass

    if not sample_size:
        return data  # Cannot convert without denominator

    for key, val in list(data.items()):
        if key.startswith('_') or not isinstance(val, str):
            continue
        # Match patterns like "45%" or "45.2%" standalone
        pct_match = _re.match(r'^(\d+\.?\d*)\s*%$', val.strip())
        if pct_match:
            pct = float(pct_match.group(1))
            count = round(pct * sample_size / 100)
            data[key] = f"{count}/{sample_size} ({val.strip()})"

    return data


def save_null_reasons_log(data, pdf_name, log_path='missing_data_justifications.json'):
    """
    Save justification log for missing data fields (as described in manuscript):
    'For entries with missing data, justification logs were generated, 
    documenting the rationale for incomplete extraction.'
    """
    null_reasons = data.pop('_null_reasons', None)
    if not null_reasons:
        return

    # Load existing log
    existing = {}
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    existing[pdf_name] = null_reasons

    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def process_study_single_pass(context, pdf_path, prompt_text):
    print(f"\n--- Processing {os.path.basename(pdf_path)} ---")
    page = context.new_page()
    try:
        data = extract_data_from_page(page, pdf_path, prompt_text)
        if data:
            # Post-processing: save justification log for null fields
            save_null_reasons_log(data, os.path.basename(pdf_path))
            # Post-processing: deterministic percentage-to-count conversion
            data = deterministic_pct_to_count(data)
            return [data]
        return []
    finally:
        # User requested "new tab" originally, but usually we close to save resources.
        # "Extract response for each article only once"
        # I will close it to keep browser clean, unless debugging.
        # Given 20 files, keeping 20 tabs might crash.
        page.close()

def get_pdf_files():
    files = [f for f in os.listdir(ARTICLES_DIR) if f.lower().endswith('.pdf')]
    return [os.path.join(ARTICLES_DIR, f) for f in files]

def main(limit=None, browser_channel="chrome", files_to_process=None):
    if not os.path.exists(ARTICLES_DIR):
        print(f"Error: Directory {ARTICLES_DIR} does not exist.")
        return

    if files_to_process:
        print(f"Targeted mode: Processing {len(files_to_process)} specific files.")
        pdf_files = [os.path.join(ARTICLES_DIR, f) for f in files_to_process if os.path.exists(os.path.join(ARTICLES_DIR, f))]
    else:
        pdf_files = get_pdf_files()
        # Resume Skip Logic
        if os.path.exists(OUTPUT_FILE):
            try:
                existing_df = pd.read_excel(OUTPUT_FILE)
                if 'Source File' in existing_df.columns:
                    processed_files = set(existing_df['Source File'].dropna().astype(str).tolist())
                    files_to_skip = []
                    for pf in pdf_files:
                        basename = os.path.basename(pf)
                        if any(basename in str(recorded) for recorded in processed_files):
                            files_to_skip.append(pf)
                    pdf_files = [f for f in pdf_files if f not in files_to_skip]
                    print(f"Skipping {len(files_to_skip)} already processed files. {len(pdf_files)} remaining.")
            except Exception as e:
                print(f"Warning: Could not read existing output file for resume logic: {e}")

        if limit:
            pdf_files = pdf_files[:int(limit)]
    
    print(f"Total PDFs to process: {len(pdf_files)}")

    prompt_text = create_prompt()
    all_results = []

    with sync_playwright() as p:
        profile_name = f"{browser_channel}_profile"
        # Move profile out of OneDrive to prevent locking/sync issues
        user_data_dir = os.path.join("C:\\Users\\HP", f"gemini_{profile_name}")
        print(f"Launching {browser_channel} with profile: {user_data_dir}")
        
        try:
            print(f"Launching {browser_channel}...")
            browser = p.chromium.launch_persistent_context(
                user_data_dir, 
                headless=False, 
                channel=browser_channel, 
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-extensions",
                    "--disable-notifications",
                    "--no-first-run",
                    "--no-default-browser-check"
                ],
                ignore_default_args=["--enable-automation"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                slow_mo=50
            )
            
            # Setup first page
            if browser.pages:
                page = browser.pages[0]
            else:
                page = browser.new_page()
                
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # Navigate with retry
            print(f"Navigating to {GEMINI_URL}...")
            for attempt in range(3):
                try:
                    page.goto(GEMINI_URL, timeout=60000, wait_until="load")
                    break
                except Exception as e:
                    if attempt == 2: raise
                    print(f"Retry {attempt+1} due to: {e}")
                    time.sleep(2)

        except Exception as e:
            print(f"Failed to launch or navigate: {e}")
            return
        
        # Automated polling for login
        login_detected = False
        print("\n" + "="*50)
        print("AUTOMATED LOGIN WAIT (Extractor)")
        print("Please log into Gemini in the opened browser window.")
        print("The agent will automatically start extraction when logged in...")
        print("="*50 + "\n")
        
        try:
            print(f"Navigating to {GEMINI_URL}...")
            page.goto(GEMINI_URL, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"Warning: Initial page load slow/failed: {e}")
            page.screenshot(path="extractor_load_error.png")
            print("Took 'extractor_load_error.png' for diagnosis.")

        for i in range(120): # Wait up to 10 minutes
            try:
                # Polling for elements
                prompt_area = page.locator("div[contenteditable='true'], textarea").first
                new_chat = page.locator("button:has-text('New chat'), a:has-text('New chat')").first
                
                if prompt_area.is_visible() or new_chat.is_visible():
                    print("\n[SUCCESS] Login detected! Starting extraction...")
                    time.sleep(2)
                    login_detected = True
                    break
            except:
                pass
            
            if i % 6 == 0:
                print(f"Still waiting for login... ({i*5}s elapsed)")
            time.sleep(5)
            
        if not login_detected:
            print("Login detection timed out. Exiting.")
            return

        print(f"\n{Fore.CYAN}{'='*60}")
        print(f"{Fore.CYAN}🚀 STARTING DATA EXTRACTION")
        print(f"{Fore.CYAN}{'='*60}\n")

        # Process Files with Progress Bar
        pbar = tqdm(pdf_files, desc=f"{Fore.YELLOW}Total Progress", unit="study")
        for pdf_path in pbar:
            filename = os.path.basename(pdf_path)
            pbar.set_postfix_str(f"{Fore.CYAN}Processing: {filename[:20]}...")
            
            study_results = process_study_single_pass(browser, pdf_path, prompt_text)
            
            if study_results:
                all_results.extend(study_results)
                
                # Save Incremental
                df = pd.DataFrame(study_results)
                for c in ALL_COLUMNS:
                    if c not in df.columns: df[c] = None
                
                cols = ['Source File'] + [c for c in ALL_COLUMNS if c in df.columns]
                df = df[cols]

                if os.path.exists(OUTPUT_FILE):
                    existing = pd.read_excel(OUTPUT_FILE)
                    df = pd.concat([existing, df], ignore_index=True)
                
                df.to_excel(OUTPUT_FILE, index=False)
                # No longer printing every save line to keep UI clean
            else:
                print(f"\n{Fore.RED}✘ Failed to extract data for {filename}")

        print(f"\n{Fore.GREEN}{'='*60}")
        print(f"{Fore.GREEN}✨ EXTRACTION COMPLETE! Browser remains open.")
        print(f"{Fore.GREEN}{'='*60}")
        time.sleep(5)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", help="Limit number of files to process", default=None)
    parser.add_argument("--browser", help="Browser channel (chrome, msedge)", default="chrome")
    parser.add_argument("--files", help="Specific files to process", nargs="+", default=None)
    args = parser.parse_args()
    main(limit=args.limit, browser_channel=args.browser, files_to_process=args.files)
