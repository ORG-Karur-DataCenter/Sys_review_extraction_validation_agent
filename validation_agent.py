
import os
import time
import json
import logging
import pandas as pd
import argparse
import re
from datetime import datetime
from playwright.sync_api import sync_playwright
from tqdm import tqdm
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("validation_run.log", mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
ARTICLES_DIR = 'Articles'
INPUT_FILE = 'extracted_studies.xlsx'
VALIDATION_LOG = 'validation_discrepancies.xlsx'
VALIDATION_SUMMARY = 'validation_summary.json'
GEMINI_URL = "https://gemini.google.com/app"

def create_validation_prompt(row_data):
    """
    Creates a prompt for Gemini to validate the extracted data.
    """
    # Remove 'Source File' and other meta columns from validation prompt
    meta_cols = ['Source File', 'Unnamed: 0']
    clean_data = {k: v for k, v in row_data.items() if k not in meta_cols and pd.notnull(v)}
    
    prompt = "I have extracted the following data from the attached PDF study. Please verify the accuracy of each field against the PDF content.\n\n"
    prompt += "### DATA TO VERIFY ###\n"
    prompt += json.dumps(clean_data, indent=2)
    prompt += "\n\n### INSTRUCTIONS ###\n"
    prompt += "1. Review the attached PDF carefully.\n"
    prompt += "2. For each field in the provided JSON, check if the value is correct.\n"
    prompt += "3. If a value is incorrect or incomplete, provide the correct information found in the PDF.\n"
    prompt += "4. If you find any discrepancies, return your findings in the following JSON format:\n"
    prompt += '{\n  "discrepancies": [\n    {\n      "field": "Field Name",\n      "extracted_value": "Value provided in prompt",\n      "correct_value": "Correct value from PDF",\n      "severity": "CRITICAL", // or "MINOR"\n      "description": "Explanation of the discrepancy"\n    }\n  ],\n  "status": "FAIL"\n}\n'
    prompt += "\n### SEVERITY CRITERIA ###\n"
    prompt += "- MINOR: Formatting issues (e.g. '50 %' vs '50%'), synonyms (e.g. 'Male' vs 'Men'), or rounding differences less than 1%.\n"
    prompt += "- CRITICAL: Different numbers (>1% variance), swapped data, missing data that exists in text, or hallucinations.\n"
    prompt += "5. CRITICAL: If all information is 100% correct AND no additions/corrections are needed, return:\n"
    prompt += '{\n  "status": "PASS",\n  "discrepancies": []\n}\n'
    prompt += "6. CRITICAL: If there is even a MINOR discrepancy, set status to 'FAIL' and list it with appropriate severity.\n"
    prompt += "\nReturn ONLY the JSON object."
    
    return prompt

def interact_with_gemini(page, pdf_path, prompt_text):
    """
    Uploads PDF and sends the validation prompt to Gemini with dynamic waiting for speed.
    """
    print(f"[{os.path.basename(pdf_path)}] Navigating to Gemini...")
    try:
        page.goto(GEMINI_URL, timeout=90000, wait_until="domcontentloaded")
    except:
        print("Page load taking longer than expected, proceeds anyway...")

    # Upload Logic
    print(f"[{os.path.basename(pdf_path)}] Attempting upload...")
    try:
        # Give the UI a moment to settle
        time.sleep(2)
        
        with page.expect_file_chooser(timeout=60000) as fc_info:
            # Try multiple common labels for the 'Plus' button in Gemini
            plus_selectors = [
                "button[aria-label*='Upload']",
                "button[aria-label*='Add files']",
                "button[aria-label*='Upload files']",
                "button[aria-label*='file menu']",
                "mat-icon:has-text('add')",
                "span:has-text('add')",
                "button:has(mat-icon)",
                "div[role='button']:has-text('add')"
            ]
            
            plus_found = False
            
            # TRY CLICKING TEXT AREA FIRST TO REVEAL BUTTONS
            try:
                page.locator("div[contenteditable='true'], textarea").first.click(timeout=5000)
                time.sleep(1)
            except:
                pass

            for selector in plus_selectors:
                try:
                    btn = page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        print(f"[{os.path.basename(pdf_path)}] Found button with selector: {selector}")
                        btn.click(force=True, timeout=10000)
                        plus_found = True
                        break
                except:
                    continue
            
            if not plus_found:
                print(f"[{os.path.basename(pdf_path)}] Plus button not found. Attempting generic click near input area...")
                try:
                    # Generic fallback: look for ANY button in the bottom input bar
                    btn = page.locator("div.input-area button, .input-area-container button").first
                    if btn.count() > 0:
                         btn.click(force=True, timeout=5000)
                         plus_found = True
                except:
                    pass

            if not plus_found:
                print(f"[{os.path.basename(pdf_path)}] Plus button still not found. Taking diagnostic 'no_plus.png'")
                page.screenshot(path="no_plus.png")
                return None
            
            time.sleep(2) # Wait for menu
            
            # Click the 'Upload' item with retries and more selectors
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
                 # Fallback: try finding any element with Upload text that is likely a menu item
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
        
        # SMART WAIT FOR UPLOAD: Wait for the "chip" to appear or the upload indicator to finish
        print(f"[{os.path.basename(pdf_path)}] Uploading... (Waiting for file to process)")
        # We look for a file chip or wait up to 60s for slow internet
        try:
            page.locator("file-chip, .file-name, [aria-label*='file']").first.wait_for(state="visible", timeout=60000)
            time.sleep(2) # Tiny buffer for Gemini internal state
        except:
            print("Slow upload/UI detection. Continuing after 15s fallback.")
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
        
        # SMART WAIT FOR RESPONSE: Instead of 40s, monitor the "Stop" button or response text
        # Gemini shows a 'stop' button (interrupt) while generating.
        stop_btn = page.locator("button[aria-label*='Stop'], button[aria-label*='Interrupt']")
        
        # Initial wait for generation to start
        time.sleep(5)
        
        # Polling for "generation completion"
        for _ in range(120): # Up to 120 seconds for very long responses/slow internet
            if stop_btn.count() == 0 or not stop_btn.is_visible():
                # Double check to see if the text is there
                response_elements = page.locator("model-response, .model-response-text")
                if response_elements.count() > 0:
                    time.sleep(2) # Final settle
                    break
            time.sleep(1)
        
        # Extract Response
        response_elements = page.locator("model-response, .model-response-text") 
        if response_elements.count() > 0:
            last_response = response_elements.all()[-1].inner_text()
        else:
            print("No response text found. Waiting 10 more seconds and grabbing page content.")
            time.sleep(10)
            last_response = page.content()

        # Parse JSON
        start = last_response.find('{')
        end = last_response.rfind('}') + 1
        if start != -1 and end != -1:
            json_str = last_response[start:end]
            try:
                data = json.loads(json_str)
                
                # Check for severity and potentially downgrade FAIL to PASS
                if data.get('status') == 'FAIL' and data.get('discrepancies'):
                    critical_errors = [d for d in data['discrepancies'] if d.get('severity') == 'CRITICAL']
                    if not critical_errors:
                         print(f"[{os.path.basename(pdf_path)}] Downgrading FAIL to PASS (Only MINOR discrepancies found).")
                         data['status'] = 'PASS'
                
                # Code-level Override: If Gemini hallucinates PASS but lists discrepancies, force FAIL
                # (Only force FAIL if there are CRITICAL errors, otherwise let it PASS)
                if data.get('status') == 'PASS' and data.get('discrepancies'):
                    critical_errors = [d for d in data['discrepancies'] if d.get('severity', 'CRITICAL') == 'CRITICAL']
                    if critical_errors:
                        print(f"[{os.path.basename(pdf_path)}] Overriding PASS to FAIL due to CRITICAL discrepancies.")
                        data['status'] = 'FAIL'

                return data
            except:
                print(f"[{os.path.basename(pdf_path)}] JSON parsing failed.")
                return {"status": "ERROR", "message": "JSON Parse Error", "raw_response": last_response}
        else:
            return {"status": "ERROR", "message": "No JSON found", "raw_response": last_response}

    except Exception as e:
        print(f"[{os.path.basename(pdf_path)}] Interaction failed: {e}")
        return None

def main(limit=None, browser_channel="chrome", files_to_validate=None):
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found.")
        return

    df = pd.read_excel(INPUT_FILE)
    if 'Source File' not in df.columns:
        print("Error: 'Source File' column missing in Excel.")
        return
    
    # Filter by specific files if provided (Priority over limit)
    if files_to_validate:
        print(f"Targeted Validation: Checking {len(files_to_validate)} specific files.")
        # Create a mask for matches (exact or partial string match if cleaner)
        # Using exact match for robustness as Source File should be the full filename
        df = df[df['Source File'].isin(files_to_validate)]
    
    # Apply limit if provided AND no specific files (OR apply limit to the specific files?)
    elif limit:
        print(f"Applying limit of {limit} rows for validation.")
        df = df.head(int(limit))

    # Ensure Source File is string to avoid type warnings during matching
    df['Source File'] = df['Source File'].astype(object)

    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"{Fore.CYAN}⚖️ STARTING DATA VALIDATION")
    print(f"{Fore.CYAN}{'='*60}\n")

    validation_results = []
    
    # Store the full original dataframe to avoid truncation when saving
    full_df = pd.read_excel(INPUT_FILE)
    # We no longer add 'Result' or 'Validation Feedback' columns to the main file
    # to keep it clean, but we still perform validation for the logs/healing.

    with sync_playwright() as p:
        profile_name = f"{browser_channel}_profile"
        user_data_dir = os.path.join("C:\\Users\\HP", f"gemini_{profile_name}")
        
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
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            print(f"Navigating to {GEMINI_URL}...")
            for attempt in range(3):
                try:
                    page.goto(GEMINI_URL, timeout=60000, wait_until="load")
                    break
                except Exception as e:
                    if attempt == 2: raise
                    print(f"Retry {attempt+1} due to: {e}")
                    time.sleep(2)
            
            print("\n" + "="*50)
            print("AUTOMATED LOGIN WAIT")
            print("Please log into Gemini in the opened browser window.")
            print("The agent will automatically detect when you are logged in...")
            print("="*50 + "\n")
            
            # Automated polling for login
            login_detected = False
            diagnostic_taken = False
            print("Monitoring browser for login... (Will auto-start when prompt area appears)")
            
            for i in range(120): # Wait up to 10 minutes
                try:
                    # Check for several indicators of being logged in:
                    # 1. The main prompt area
                    # 2. The user profile button
                    # 3. The "New chat" button
                    prompt_area = page.locator("div[contenteditable='true'], textarea").first
                    new_chat = page.locator("button:has-text('New chat'), a:has-text('New chat')").first
                    user_profile = page.locator("button[aria-label*='Google Account']").first
                    
                    if prompt_area.is_visible() or new_chat.is_visible() or user_profile.is_visible():
                        print("\n[SUCCESS] Login detected! Initialization starting in 3 seconds...")
                        time.sleep(3)
                        login_detected = True
                        break
                except Exception as e:
                    pass
                
                if i > 0 and i % 12 == 0: # Every 60 seconds
                     print(f"Still waiting... ({i*5}s elapsed). Please ensure you are logged in and looking at the Gemini home screen.")
                     if not diagnostic_taken:
                         page.screenshot(path="login_debug.png")
                         print("Took 'login_debug.png' for diagnostic purposes.")
                         diagnostic_taken = True
                
                time.sleep(5)
            
            if not login_detected:
                print("Detection timed out. Taking final screenshot and exiting.")
                page.screenshot(path="login_timeout.png")
                return

        except Exception as e:
            print(f"Failed to launch browser: {e}")
            return

        pbar = tqdm(df.iterrows(), total=len(df), desc=f"{Fore.YELLOW}Total Progress", unit="row")
        for index, row in pbar:
            source_file = row['Source File']
            author_year = str(row.get('First Author (Year)', ''))
            
            pbar.set_postfix_str(f"{Fore.CYAN}Checking Row {index+1}")
            
            # --- SMART MATCHING LOGIC ---
            if (not isinstance(source_file, str) or pd.isna(source_file)) and author_year:
                import re
                pbar.set_postfix_str(f"{Fore.BLUE}Matching PDF...")
                
                match = re.search(r'(\w+).*?(\d{4})', author_year)
                if match:
                    author_name = match.group(1).lower()
                    year = match.group(2)
                    
                    for f in os.listdir(ARTICLES_DIR):
                        f_lower = f.lower()
                        if author_name in f_lower and year in f_lower and f_lower.endswith('.pdf'):
                            source_file = f
                            tqdm.write(f"{Fore.GREEN}✔ Smart Matched '{author_year}' to: {source_file}")
                            df.at[index, 'Source File'] = source_file
                            break
            
            # Handle NaN or non-string values (if still NaN after matching)
            if not isinstance(source_file, str) or pd.isna(source_file):
                if index < 50: 
                    print(f"Skipping row {index+1}: 'Source File' is empty or invalid.")
                continue

            pdf_path = os.path.join(ARTICLES_DIR, source_file)
            
            if not os.path.exists(pdf_path):
                # Try finding it in Articles/ (just in case path is slightly different)
                if os.path.exists(os.path.join(ARTICLES_DIR, os.path.basename(source_file))):
                    pdf_path = os.path.join(ARTICLES_DIR, os.path.basename(source_file))
                else:
                    print(f"Warning: PDF not found at {pdf_path}. Skipping.")
                    continue

            # Check for data in the row (excluding metadata)
            meta_cols = ['Source File', 'Sl.no', 'Unnamed: 0']
            clean_data = {k: v for k, v in row.to_dict().items() if k not in meta_cols and pd.notnull(v)}
            
            if not clean_data:
                print(f"INFO: No data found for {source_file} (Row {index+1}). Logging as NO DATA.")
                validation_results.append({
                    'Source File': source_file,
                    'status': 'NO DATA',
                    'discrepancies': []
                })
                continue

            print(f"\n--- Validating {source_file} (Row {index+1}) ---")
            
            prompt_text = create_validation_prompt(row.to_dict())
            
            # Use a new tab for each validation to avoid context leakage
            new_page = browser.new_page()
            new_page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            try:
                result = interact_with_gemini(new_page, pdf_path, prompt_text)
                if result:
                    result['Source File'] = source_file
                    status = result.get('status', 'FAIL')
                    validation_results.append(result)
                    
                    # Logic for console output and logging (DataFrame is NOT modified)
                    if status == 'PASS':
                        tqdm.write(f"{Fore.GREEN}✔ {author_year[:30]} - PASS")
                    else:
                        # Aggregate discrepancy descriptions
                        for disc in result.get('discrepancies', []):
                            field = disc.get('field', 'Unknown')
                            sev = disc.get('severity', 'UNKNOWN')
                            desc = disc.get('description', 'No description')
                            color = Fore.RED if sev == 'CRITICAL' else Fore.YELLOW
                            tqdm.write(f"  {color}→ [{sev}] {field}: {desc[:50]}...")
                        
                        tqdm.write(f"{Fore.RED}✘ Discrepancy Found: {author_year[:30]}")

                else:
                    tqdm.write(f"{Fore.RED}✘ Interaction failed for {author_year}")
            finally:
                new_page.close()

            # Save the full dataframe back to ensure no data loss
            full_df.to_excel(INPUT_FILE, index=False)

            # Save incrementally
            if validation_results:
                # Flatten discrepancies for Excel output
                flattened = []
                for res in validation_results:
                    sf = res.get('Source File')
                    status = res.get('status')
                    if not res.get('discrepancies'):
                        desc = 'None' if status != 'NO DATA' else 'Row has no extracted data points to verify'
                        flattened.append({
                            'Source File': sf,
                            'Status': status,
                            'Field': None,
                            'Extracted Value': None,
                            'Correct Value': None,
                            'Description': desc
                        })
                    else:
                        for d in res['discrepancies']:
                            flattened.append({
                                'Source File': sf,
                                'Status': status,
                                'Severity': d.get('severity', 'UNKNOWN'),
                                'Field': d.get('field'),
                                'Extracted Value': d.get('extracted_value'),
                                'Correct Value': d.get('correct_value'),
                                'Description': d.get('description')
                            })
                
                res_df = pd.DataFrame(flattened)
                res_df.to_excel(VALIDATION_LOG, index=False)
                logger.info(f"Incremental log saved to {VALIDATION_LOG}")

        # Save validation summary as JSON for audit
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_articles': len(df),
            'passed': sum(1 for r in validation_results if r.get('status') == 'PASS'),
            'failed': sum(1 for r in validation_results if r.get('status') == 'FAIL'),
            'errors': sum(1 for r in validation_results if r.get('status') == 'ERROR'),
            'no_data': sum(1 for r in validation_results if r.get('status') == 'NO DATA'),
            'critical_errors': sum(
                len([d for d in r.get('discrepancies', []) if d.get('severity') == 'CRITICAL'])
                for r in validation_results
            ),
            'minor_errors': sum(
                len([d for d in r.get('discrepancies', []) if d.get('severity') == 'MINOR'])
                for r in validation_results
            ),
        }
        with open(VALIDATION_SUMMARY, 'w', encoding='utf-8') as jf:
            json.dump(summary, jf, indent=2)
        logger.info(f"Validation summary saved: {VALIDATION_SUMMARY}")

        logger.info("Validation complete. Browser remains open.")
        time.sleep(5)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", help="Limit number of rows to validate", default=None)
    parser.add_argument("--browser", help="Browser channel (chrome, msedge)", default="chrome")
    parser.add_argument("--files", help="Specific files to validate", nargs="+", default=None)
    args = parser.parse_args()
    main(limit=args.limit, browser_channel=args.browser, files_to_validate=args.files)
