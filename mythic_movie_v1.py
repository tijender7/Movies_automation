# -*- coding: utf-8 -*-
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    StaleElementReferenceException, ElementClickInterceptedException,
    ElementNotInteractableException
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import time
import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime
import logging
from dotenv import load_dotenv
import traceback

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
PROJECT_BASE_FOLDER_NAME = "mythic_movie_project"
PROJECT_PREFIX = "mythic_movie"
SCENES_PER_MINUTE = 12
SCENES_PER_BATCH = 12

# --- Dynamic Paths ---
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECTS_BASE_DIR = SCRIPT_DIR / PROJECT_BASE_FOLDER_NAME
COOKIE_PATH = SCRIPT_DIR / "chatgpt_cookies.json"
DOTENV_PATH = SCRIPT_DIR / ".env"
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE_NAME = f"mythic_movie_log_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"
LOG_FILE = LOG_DIR / LOG_FILE_NAME

# --- Output Filenames ---
STORY_FILENAME = "story_approved.txt"
CHARACTERS_FILENAME = "characters_detailed.json"
SCENES_FILENAME = "scenes_batched.json"
IMAGE_PROMPTS_FILENAME = "image_prompts_detailed.json"

# --- ChatGPT Settings ---
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o"
CHATGPT_LOGIN_TIMEOUT = 120
CHATGPT_RESPONSE_TIMEOUT = 360
CHATGPT_JSON_RETRIES = 2
MAX_STORY_ATTEMPTS = 3
POST_LOGIN_INPUT_BOX_TIMEOUT = 15  # Reduced from 90 for faster detection

# --- Logging Setup ---
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('selenium').setLevel(logging.WARNING)
logging.getLogger('undetected_chromedriver').setLevel(logging.WARNING)
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
logging.getLogger('tensorflow').setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# --- Helper: log_step ---
def log_step(message, level="info", important=False):
    level_map = { "debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL }
    prefix_map = {"debug":"[D]","info":"[*]","warning":"[!]","error":"[X]","success":"[+]","critical":"[!!!]"}
    prefix = prefix_map.get(level, "[?]")
    final = f"{prefix} {message}"
    if important: final = f"\n--- {final} ---\n"
    log.log(level_map.get(level, logging.INFO), final)

# --- Load Environment Variables ---
if DOTENV_PATH.is_file():
    load_dotenv(dotenv_path=DOTENV_PATH)
    log_step(f"Loaded environment variables from {DOTENV_PATH}")
else:
    print(f"[!] .env file not found at {DOTENV_PATH}. Continuing without it.")

# --- Other Helpers ---
def sanitize_name(name):
    if not isinstance(name, str): name = str(name)
    s = re.sub(r'[<>:\"/\\|?*\']', '_', name)
    s = ''.join(c for c in s if c.isalnum() or c in (' ', '_', '-')).strip()
    s = s.replace(' ', '_'); s = re.sub(r'[_]+', '_', s); s = s.strip('_-')
    return s or 'invalid_name'

def sanitize_for_chromedriver(text):
    return re.sub(r"[^\u0000-\uFFFF]", "", text) if isinstance(text, str) else text

# --- Cookie Management ---
def save_cookies(driver, path):
    log_step(f"Attempting to save cookies to: {path}")
    try:
        cookies = driver.get_cookies()
        if not cookies: log_step("No cookies to save.", level="warning"); return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f: json.dump(cookies, f, indent=2)
        log_step(f"Cookies saved ({len(cookies)} cookies).", level="success")
    except WebDriverException as e: log_step(f"WebDriver error saving cookies: {e}", level="error")
    except Exception as e: log_step(f"Error saving cookies: {e}", level="error"); log.debug(traceback.format_exc())

def load_cookies(driver, path):
    if not path.exists(): log_step("Cookie file not found.", level="warning"); return False
    log_step(f"Loading cookies from: {path}")
    try:
        with open(path, 'r', encoding='utf-8') as f: cookies = json.load(f)
        if not cookies: log_step("Cookie file empty.", level="warning"); return False
    except Exception as e: log_step(f"Error reading/parsing cookie file: {e}.", level="error"); return False

    log_step(f"Navigating to base URL ({CHATGPT_BASE_URL}) before adding cookies.")
    try:
        if driver.current_url == 'data:,': log_step("Driver on initial blank page.", level="debug")
        driver.get(CHATGPT_BASE_URL); time.sleep(5)
    except WebDriverException as e: log_step(f"Error navigating to base URL: {e}", level="warning")

    added = 0; skipped = 0; current_domain = None
    try: current_domain = driver.current_url.split('/')[2].split(':')[0]
    except Exception: log_step("Could not determine current domain.", level="warning")
    log_step(f"Current browser domain for adding cookies: {current_domain}", level="debug")

    for i, c in enumerate(cookies):
        log_step(f"Processing cookie {i+1}: Name='{c.get('name')}', Domain='{c.get('domain')}'", level="debug")
        if not isinstance(c, dict) or 'name' not in c or 'value' not in c:
            log_step(f"  -> Skipping cookie {i+1}: Invalid structure (missing name/value).", level="debug"); skipped += 1; continue

        cookie_domain = c.get('domain', '').lstrip('.')
        # **More lenient domain check:** only skip if cookie domain is present AND clearly different base domain
        domain_mismatch = False
        if current_domain and cookie_domain:
             # Simple check: allow if browser domain ends with cookie domain (e.g., chat.openai.com matches openai.com)
             # or if they are identical. This might be too lenient or too strict depending on exact needs.
             if not current_domain.endswith(cookie_domain):
                  domain_mismatch = True
                  log_step(f"  -> Skipping cookie {i+1} ('{c['name']}'): Domain mismatch (Cookie:'{cookie_domain}', Browser:'{current_domain}')", level="debug")
                  skipped += 1; continue

        # Clean attributes
        try:
            if 'sameSite' in c and c['sameSite'] not in ['Lax', 'Strict', 'None']: c['sameSite'] = 'Lax'
            if c.get('sameSite') == 'None': c['secure'] = True
            elif 'secure' in c and not isinstance(c['secure'], bool): c['secure'] = bool(c['secure'])
            if 'httpOnly' in c and not isinstance(c['httpOnly'], bool): c['httpOnly'] = bool(c['httpOnly'])
            if 'expiry' in c:
                if isinstance(c['expiry'], (float, int)):
                    if c['expiry'] < time.time() - 60: log_step(f"  -> Skipping cookie {i+1} ('{c['name']}'): Expired.", level="debug"); skipped += 1; continue
                    c['expiry'] = int(c['expiry'])
                else: del c['expiry']
        except Exception as clean_e:
            log_step(f"  -> Skipping cookie {i+1} ('{c['name']}'): Error cleaning attributes: {clean_e}", level="warning"); skipped += 1; continue

        # Add cookie
        try:
            driver.add_cookie(c); added += 1
            log_step(f"  -> Added cookie {i+1} ('{c['name']}').", level="debug")
        except Exception as e:
            # Log specific webdriver errors if possible
            err_str = str(e).lower()
            common_errs = ["invalid domain", "unable to set cookie", "invalid argument"]
            level = "debug" if any(ce in err_str for ce in common_errs) else "warning"
            log_step(f"  -> Failed add cookie {i+1} ('{c.get('name', 'N/A')}'): {e}", level=level)
            skipped += 1

    level = "success" if added > 0 else "warning"
    log_step(f"Cookie loading finished. Added: {added}, Skipped: {skipped}.", level=level)
    return added > 0

# --- Selenium Helpers ---
def dismiss_overlays(driver, timeout=15):
    """Attempts to find and close common overlays/dialogs using standard CSS/XPath."""
    log_step("Checking for overlays...", level="debug")
    # ** FIXED SELECTORS: Removed :contains(), using standard attributes/XPath **
    # XPath is needed for reliable text matching.
    # Prioritize more specific selectors first.
    selectors = {
        # Specific based on observation (might need updates)
        "button[aria-label='Close dialog']": "CSS aria-label",
        "button[aria-label='Dismiss']": "CSS aria-label",
        "div[role='dialog'] button[id*='cancel']": "CSS Dialog Cancel ID",
        "div[role='dialog'] button[id*='close']": "CSS Dialog Close ID", # Added
        # XPath for text matching (slower but necessary)
        "//div[@role='dialog']//button[normalize-space(.)='Okay']": "XPath Okay",
        "//div[@role='dialog']//button[normalize-space(.)='Ok']": "XPath Ok",
        "//div[@role='dialog']//button[normalize-space(.)='Got it']": "XPath Got it",
        "//div[@role='dialog']//button[normalize-space(.)='Next']": "XPath Next",
        "//div[@role='dialog']//button[normalize-space(.)='Done']": "XPath Done",
        # Generic structural selectors (less reliable)
        "div[class*='modal'] button[class*='close']": "CSS Modal Close Class",
        "div[role='dialog'] button:has(svg[data-icon='close'])": "CSS Close SVG", # If :has is supported by browser/selenium version
    }
    start = time.time(); closed = False
    while time.time() - start < timeout:
        clicked_this_round = False
        driver.switch_to.default_content()
        for selector, name in selectors.items():
            try:
                # Determine method based on selector type
                by_method = By.XPATH if selector.startswith("//") else By.CSS_SELECTOR
                elements = driver.find_elements(by_method, selector)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        log_step(f"Found '{name}' ({selector}). Clicking.", level="info")
                        try: element.click(); closed = True; clicked_this_round = True; time.sleep(0.8); break # Longer sleep after click
                        except Exception as e: log_step(f"Could not click '{name}': {type(e).__name__}", level="debug")
                if clicked_this_round: break # Restart search after click
            except Exception as e: log_step(f"Err searching overlay '{selector}': {e}", level="warning")
        if not clicked_this_round: break # Stop if nothing found this iteration
        time.sleep(0.2)
    if closed: log_step("Finished overlay check.", level="info"); time.sleep(2)
    else: log_step("No obvious overlays found/clicked.", level="debug")
    driver.switch_to.default_content()

def wait_for_element_in_frames(driver, by, value, timeout=10):
    """Waits for an element, checking default content and IFrames."""
    log_step(f"Searching for element ({by}={value}) in default/frames...", level="debug")
    start = time.time(); element = None
    try: # Check default content
        driver.switch_to.default_content()
        element = WebDriverWait(driver, timeout * 0.6).until(EC.element_to_be_clickable((by, value))) # More time default
        if element: log_step("Element found in default content.", level="debug"); return element
    except Exception: log_step("Not in default content, checking frames...", level="debug")

    remaining_time = timeout - (time.time() - start)
    if remaining_time <= 0: return None
    try: # Check IFrames
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if iframes:
            time_per = max(1, remaining_time / len(iframes))
            log_step(f"Checking {len(iframes)} IFrame(s) (max {time_per:.1f}s/frame)...", level="debug")
            for i, frame in enumerate(iframes):
                try:
                    driver.switch_to.frame(frame)
                    element = WebDriverWait(driver, time_per).until(EC.element_to_be_clickable((by, value)))
                    if element: log_step(f"Element found in IFrame {i}.", level="success"); return element
                except Exception: pass
                finally: driver.switch_to.default_content() # Switch back after *each* frame check
    except Exception as e: log_step(f"Error finding/iterating frames: {e}", level="warning")
    log_step(f"Element ({by}={value}) not found in default/frames.", level="debug"); return None

def wait_for_input_box(driver, timeout=10):
    """
    Waits for the ChatGPT input textarea or contenteditable div, handling overlays/frames and robust to UI changes.
    Tries multiple selectors and falls back to any visible textarea/input/div[contenteditable='true'] with 'message' or 'chat' in placeholder/aria-label/text.
    """
    log_step(f"Waiting for input box (timeout: {timeout}s)...")
    start = time.time()
    dismiss_overlays(driver, timeout=8)
    selectors = [
        "textarea#prompt-textarea",
        "textarea[data-testid='prompt-textarea']",
        "textarea[placeholder^='Message']",
        "textarea[tabindex='0']",
        "textarea[placeholder*='anything']",
        "textarea[placeholder*='message']",
        "textarea[aria-label*='message']",
        "textarea[aria-label*='chat']",
        "textarea",
        "input[aria-label*='message']",
        "input[placeholder*='message']",
        "input[aria-label*='chat']",
        "input[placeholder*='chat']",
        "div[contenteditable='true']",  # Added for custom chat UIs
    ]
    while time.time() - start < timeout:
        for sel in selectors:
            try:
                element = wait_for_element_in_frames(driver, By.CSS_SELECTOR, sel, timeout=2)
                if element and element.is_displayed() and element.is_enabled():
                    log_step(f"Input box found via selector '{sel}'.", level="success")
                    try: driver.switch_to.default_content()
                    except: pass
                    return element
            except Exception:
                continue
        # Fallback: any visible textarea/input/div[contenteditable='true'] with 'message' or 'chat' in placeholder/aria-label/text
        try:
            driver.switch_to.default_content()
            candidates = driver.find_elements(By.CSS_SELECTOR, "textarea, input, div[contenteditable='true']")
            for c in candidates:
                try:
                    if not (c.is_displayed() and c.is_enabled()): continue
                    ph = (c.get_attribute('placeholder') or '').lower()
                    al = (c.get_attribute('aria-label') or '').lower()
                    txt = (c.text or '').lower() if c.tag_name == "div" else ""
                    if any(word in ph or word in al or word in txt for word in ['message', 'chat', 'anything']):
                        log_step("Input box found via fallback candidate.", level="success")
                        return c
                except Exception:
                    continue
        except Exception:
            pass
        log_step("Input box not found this iteration, retrying...", level="debug"); time.sleep(0.5)
    raise TimeoutException(f"ChatGPT input box not found after {timeout}s.")

def wait_for_response_completion(driver, timeout=CHATGPT_RESPONSE_TIMEOUT):
    """Waits for ChatGPT response generation to finish."""
    log_step(f"Wait response completion (timeout: {timeout}s)...", level="debug")
    start = time.time(); started = False; last_state = "unknown"; grace=12
    stop_sel = "button[aria-label*='Stop generating'], button[data-testid='stop-button']"
    regen_sel = "button[aria-label*='Regenerate'], button[data-testid*='regenerate']"
    while time.time() - start < timeout:
        try:
            driver.switch_to.default_content()
            stops = driver.find_elements(By.CSS_SELECTOR, stop_sel)
            generating = any(b.is_displayed() for b in stops)
            if generating: started = True; last_state = "generating"; time.sleep(0.7); continue
            if started: log_step("Response complete (stop btn gone).", level="success"); time.sleep(0.5); return True
            if time.time()-start > grace:
                regens = driver.find_elements(By.CSS_SELECTOR, regen_sel)
                if any(b.is_displayed() for b in regens): log_step("Idle (regen found).", level="info"); return True
                else: log_step(f"No stop/regen after {grace}s. Assuming complete.", level="warning"); return True
            last_state = "wait_start"
        except StaleElementReferenceException: log_step("Stale element check status.", level="debug"); time.sleep(0.3)
        except Exception as e: log_step(f"Err check status: {e}", level="warning"); last_state=f"err:{type(e).__name__}"; time.sleep(1)
        time.sleep(0.4)
    raise TimeoutException(f"Response timeout after {timeout}s. State: {last_state}")

def send_message(driver, message):
    """Sends a message to ChatGPT, handling clearing, sending, and waiting."""
    try:
        input_box = wait_for_input_box(driver, timeout=90)
        clean_message = sanitize_for_chromedriver(message)
        log_step(f"Sending message ({len(clean_message)} chars)...", level="info")
        # --- Reliable Clearing ---
        attempts = 0; cleared = False
        while attempts < 3 and not cleared:
            try:
                driver.switch_to.default_content()
                input_box.clear(); time.sleep(0.1)
                if input_box.get_attribute('value'): driver.execute_script("arguments[0].value = ''; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", input_box); time.sleep(0.1)
                if input_box.get_attribute('value'): input_box.send_keys(Keys.CONTROL + "a", Keys.BACK_SPACE); time.sleep(0.1)
                if not input_box.get_attribute('value'): cleared = True; break
                else: log_step(f"Clear attempt {attempts+1} failed.", level="warning")
            except StaleElementReferenceException:
                 log_step("Input box stale during clear, re-finding...", level="warning")
                 try: input_box = wait_for_input_box(driver, timeout=30)
                 except TimeoutException: raise RuntimeError("Cannot clear input box - failed re-find.")
            except Exception as e: log_step(f"Err clear attempt {attempts+1}: {e}", level="warning")
            attempts += 1; time.sleep(0.3)
        if not cleared: log_step("Failed final clear.", level="error")
        # --- Sending Keys ---
        lines = clean_message.split('\n')
        for i, line in enumerate(lines):
            try:
                driver.switch_to.default_content(); input_box.send_keys(line)
                if i < len(lines) - 1: input_box.send_keys(Keys.SHIFT, Keys.ENTER); time.sleep(0.05)
            except StaleElementReferenceException:
                log_step("Input box stale while sending, re-finding & retrying line.", level="warning")
                input_box = wait_for_input_box(driver, timeout=30); input_box.send_keys(line)
                if i < len(lines) - 1: input_box.send_keys(Keys.SHIFT, Keys.ENTER)
            except Exception as e: log_step(f"Error sending line {i+1}: {e}", level="error")
        time.sleep(0.3)
        # --- Clicking Send Button ---
        driver.switch_to.default_content(); send_button = None
        selectors = ["button[data-testid='send-button']", "button:has(svg[data-icon='arrow-up'])", "button[aria-label*='Send']", "button:enabled > svg > path[d*='M15.1']"]
        for sel in selectors:
            try:
                 candidates = driver.find_elements(By.CSS_SELECTOR, sel)
                 vis_enabled = [b for b in candidates if b.is_displayed() and b.is_enabled()]
                 if vis_enabled: send_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable(vis_enabled[0])); break
            except Exception: pass
        # Click or fallback
        if send_button:
            try: send_button.click(); log_step("Sent.", level="success")
            except ElementClickInterceptedException:
                 log_step("Click intercepted, trying JS.", level="debug")
                 try: driver.execute_script("arguments[0].click();", send_button); log_step("Sent via JS.", level="success")
                 except Exception as js_e: log_step(f"JS click failed: {js_e}. Fallback ENTER.", level="warning"); input_box.send_keys(Keys.ENTER)
            except Exception as e: log_step(f"Err click send: {e}. Fallback ENTER.", level="warning"); input_box.send_keys(Keys.ENTER)
        else: log_step("No send button found/clickable. Fallback ENTER.", level="warning"); input_box.send_keys(Keys.ENTER)
        wait_for_response_completion(driver)
    except Exception as e: log_step(f"Error in send_message: {e}", level="error", important=True); log.exception("Send Message Error"); raise

def get_last_response_text(driver):
    """Retrieves text content of the last assistant message block."""
    log_step("Getting last response text...", level="debug"); attempts=0
    while attempts < 2:
        try:
            driver.switch_to.default_content()
            blocks = driver.find_elements(By.CSS_SELECTOR, "div[data-message-author-role='assistant']")
            if not blocks: log_step("No assistant blocks found.", level="warning"); return ""
            last = blocks[-1]; text = ""; selectors = [".markdown", "div.prose", "div[class*='result-streaming']", "div[class*='message-content']"]
            for sel in selectors:
                try:
                     elems = last.find_elements(By.CSS_SELECTOR, sel)
                     if elems: curr = '\n'.join(e.text.strip() for e in elems if e.text).strip()
                     if curr and len(curr) > len(text): text = curr
                except Exception: pass
            if not text: text = last.text.strip(); log_step("Used fallback block.text", level="debug")
            log_step(f"Got response text (len: {len(text)}).", level="debug"); return text
        except StaleElementReferenceException: attempts += 1; log_step(f"Stale element retry {attempts}/2", level="warning"); time.sleep(1)
        except Exception as e: log_step(f"Error getting response text: {e}", level="error"); return ""
    log_step("Failed get response text after retries.", level="error"); return ""

def strip_markdown_fences(text):
    """Removes ```json ... ``` or ``` ... ```."""
    if not isinstance(text, str): return text
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"; match = re.search(pattern, text, re.DOTALL)
    if match: return match.group(1).strip()
    return text

def try_parse_json(response_text):
    """Tries to parse JSON, handling fences."""
    if not response_text or not isinstance(response_text, str): return False, "Empty/invalid response text."
    last_err = "Unknown parse error."
    try: return True, json.loads(response_text) # 1. Direct
    except json.JSONDecodeError as e: last_err = f"Direct parse: {e}"
    cleaned = strip_markdown_fences(response_text)
    if cleaned != response_text: # Only try if stripping changed text
        try: return True, json.loads(cleaned) # 2. Stripped
        except json.JSONDecodeError as e: last_err = f"Stripped parse: {e}"
    msg = f"JSON parse failed. Err: {last_err}. Preview: {response_text[:150]}..."
    log_step(msg, level="warning"); return False, msg # Return error message

def request_json_data(driver, prompt):
    """Requests and validates JSON data, with retries."""
    if "Output ONLY the raw JSON object" not in prompt: prompt += "\n\nIMPORTANT: Output ONLY raw JSON..."
    orig_summary = "\n".join(prompt.splitlines()[:6]) + "..."; last_err_msg = "Initial request."
    resp_txt = "" # Initialize resp_txt
    success = False # Initialize success flag
    for attempt in range(CHATGPT_JSON_RETRIES + 1):
        curr_att = attempt + 1; log_step(f"Requesting JSON (Attempt {curr_att}/{CHATGPT_JSON_RETRIES + 1})", level="info" if attempt == 0 else "warning")
        current_prompt = prompt
        if attempt > 0:
            current_prompt = (f"MODE:JSON\nYour previous response wasn't valid JSON.\nError: {last_err_msg}\n"
                              f"Retry based on original request:\n---\n{orig_summary}\n---\n"
                              f"Output ONLY the raw JSON object. No extra text or ```.")
            log_step("Sending retry prompt.", level="debug")
        try:
            send_message(driver, current_prompt); resp_txt = get_last_response_text(driver)
            if not resp_txt: last_err_msg = "Received empty response."; log_step(last_err_msg, level="warning"); time.sleep(3); continue
            success, result_or_error = try_parse_json(resp_txt) # Update success flag here
            if success: log_step(f"Successfully parsed JSON on attempt {curr_att}.", level="success"); return result_or_error
            else: last_err_msg = result_or_error # Store error message
        except Exception as e: last_err_msg = f"Comm/Script Err:{e}"; log_step(last_err_msg, level="error"); log.exception("JSON Request Error"); time.sleep(3)
        # Save raw response on failure (check success flag)
        if not success:
             raw_path = LOG_DIR / f"failed_json_attempt_{curr_att}_{datetime.now().strftime('%H%M%S')}.txt"
             try:
                 with open(raw_path, 'w', encoding='utf-8') as f: f.write(f"--PROMPT--\n{current_prompt}\n--RESPONSE--\n{resp_txt}")
                 log_step(f"Saved raw fail JSON resp {curr_att} to {raw_path.name}", level="debug")
             except Exception as save_e: log_step(f"Could not save raw fail JSON resp: {save_e}", level="warning")
        # Wait before retry
        if attempt < CHATGPT_JSON_RETRIES: time.sleep(3 + attempt * 2)
    log_step("Failed JSON after all retries.", level="error", important=True); return None

# --- Main ---
def main():
    start_time = time.time(); driver = None; project_path = None
    try:
        log_step("===== Mythic Movie Generator Started =====", important=True)
        # --- User Input ---
        topic = input("[?] Enter the mythic story topic: ").strip()
        while not topic:
            topic = input("[!] Topic cannot be empty. Please enter topic: ").strip()

        duration_minutes = 0
        while duration_minutes <= 0:
            try:
                dur_str = input(f"[?] Duration in minutes (e.g., 1, 2) [{SCENES_PER_MINUTE} scenes/min]: ").strip()
                duration_minutes = int(dur_str)
                if duration_minutes <= 0: print("[!] Duration must be positive.")
            except ValueError: print("[!] Invalid number.")
        total_scenes = duration_minutes * SCENES_PER_MINUTE
        log_step(f"Targeting {total_scenes} scenes for {duration_minutes} min.", level="info")

        # --- Project Setup ---
        timestamp = datetime.now().strftime(TIMESTAMP_FORMAT); sanitized_topic = sanitize_name(topic)
        project_folder_name = f"{PROJECT_PREFIX}_{sanitized_topic}_{timestamp}"; project_path = PROJECTS_BASE_DIR / project_folder_name
        project_path.mkdir(parents=True, exist_ok=True); log_step(f"Project folder: {project_path}", level="success")

        # --- Browser Setup ---
        log_step("Initializing WebDriver..."); options = uc.ChromeOptions(); options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled"); options.add_argument("--log-level=3")
        # options.add_argument("--no-sandbox"); options.add_argument("--disable-dev-shm-usage") # Uncomment if necessary
        driver = uc.Chrome(options=options); log_step(f"WebDriver OK. PID: {driver.service.process.pid}", level="success")

        # --- ChatGPT Login/Access ---
        driver.get(CHATGPT_BASE_URL); time.sleep(5)
        cookies_loaded_ok = load_cookies(driver, COOKIE_PATH)
        if cookies_loaded_ok: log_step("Cookies loaded. Refreshing page.", level="info"); driver.refresh(); time.sleep(7) # Longer wait after refresh
        else: log_step("Cookies failed/missing. Manual login required.", level="warning")
        # Verify login state
        try:
            log_step("Checking login status via input box..."); wait_for_input_box(driver, timeout=10)
            log_step("Input box found -> Logged in.", level="success"); save_cookies(driver, COOKIE_PATH)
        except TimeoutException:
            log_step(f"Manual login required.", level="warning", important=True)
            print(f"\n[!!!] Please log in to ChatGPT in the browser window."); print(f"[!!!] Press Enter here AFTER login.")
            input(f"[?] Press Enter to continue...")
            log_step("Continuing after manual login prompt. Saving cookies...")
            save_cookies(driver, COOKIE_PATH)
            log_step("Refreshing..."); driver.refresh(); time.sleep(6) # Wait after refresh
            try: wait_for_input_box(driver, timeout=POST_LOGIN_INPUT_BOX_TIMEOUT) # Shorter wait, robust selectors
            except TimeoutException as e: raise RuntimeError(f"Failed login confirmation after {POST_LOGIN_INPUT_BOX_TIMEOUT}s.") from e
            log_step("Input box found after manual login.", level="success"); save_cookies(driver, COOKIE_PATH)

        # --- Set Initial Context ---
        driver.switch_to.default_content()
        system_prompt = "MODE:COMMENTARY\nYou are an AI assistant collaborating... Follow MODE tags... Output ONLY raw JSON..."
        log_step("Setting context...", level="info"); send_message(driver, system_prompt); time.sleep(3)

        # --- Story Generation & Approval ---
        log_step("Starting Story Gen...", level="info", important=True)
        approved_text = None; attempts = 0; current_story_draft = ""; feedback = ""
        while not approved_text and attempts < MAX_STORY_ATTEMPTS:
            attempts += 1; log_step(f"Story attempt {attempts}/{MAX_STORY_ATTEMPTS}...", level="info")
            if attempts == 1: story_prompt = f"MODE:COMMENTARY\nWrite short cinematic story (~{duration_minutes*150} words) on topic: '{topic}'. Focus imagery. ONLY story text."
            elif feedback: story_prompt = f"MODE:COMMENTARY\nRevise based on feedback: '{feedback}'.\nPREVIOUS:\n{current_story_draft}\n\nONLY revised story text."
            else: story_prompt = f"MODE:COMMENTARY\nPrevious rejected. Write DIFFERENT story on topic: '{topic}'. ONLY new story text."
            try: send_message(driver, story_prompt); current_story_draft = get_last_response_text(driver)
            except Exception as e: log_step(f"Error gen story attempt {attempts}: {e}", level="error"); time.sleep(5); continue
            if not current_story_draft: log_step(f"Attempt {attempts} empty draft.", level="warning"); time.sleep(5); continue
            print("\n" + f"=== STORY DRAFT {attempts} ===" + "\n" + current_story_draft + "\n" + "="*25)
            while True:
                 act = input(f"[?] Approve? (yes/edit/reject) [{attempts}/{MAX_STORY_ATTEMPTS}]: ").lower().strip()
                 if act in ('yes', 'y'): approved_text = current_story_draft; log_step("Story approved.", level="success"); break
                 elif act in ('edit', 'e'):
                     fb_in = input("[?] Feedback: ").strip()
                     if fb_in: feedback = fb_in; log_step("Edit requested.", level="info"); break
                     else: print("[!] Feedback needed for 'edit'.")
                 elif act in ('reject', 'r'): log_step("Story rejected.", level="info"); feedback = ""; break
                 else: print("[!] Invalid input.")
            if approved_text:
                 try: send_message(driver, "MODE:COMMENTARY\nUser approved story.")
                 except: pass; break
            else:
                 try: msg = f"MODE:COMMENTARY\nUser: {'Revise' if feedback else 'Reject'}." ; send_message(driver, msg)
                 except: pass
            time.sleep(2)
        if not approved_text: raise RuntimeError(f"Failed story approval after {MAX_STORY_ATTEMPTS} attempts.")
        story_file_path = project_path / STORY_FILENAME
        with open(story_file_path, 'w', encoding='utf-8') as f: f.write(approved_text)
        log_step(f"Story saved: {story_file_path}", level="success")

        # --- Detailed Character Extraction ---
        log_step("Starting Character Extraction...", level="info", important=True)
        char_prompt = (
    f"MODE:JSON\nBased ONLY on story:\n{approved_text}\n"
    f"Identify key characters. For each: 'name', 'overall_description', 'visual_details' (object with keys: "
    f"gender, age_appearance, body_type, height_estimate, skin_tone, hair_color, hair_style, eye_color, "
    f"clothing_style, clothing_colors (list), accessories, notable_features).\n"
    f"IMPORTANT: For EVERY character, ALL visual_details fields MUST be fully specified. Never use 'Unspecified', 'None', or leave blank. If any field is missing, unclear, or not in the story, invent a creative, visually striking, and genre-consistent value. Be consistent and imaginative.\n"
    f"For male characters, physique MUST be described as extremely muscular, bodybuilder or fitness athlete physique, large, strong, and dominant. For female characters, physique MUST be beautiful, athletic, fit, feminine, with visible but not excessive muscle. For demons/monsters, default to huge, grotesquely muscular, and menacing unless otherwise described.\n"
    f"Output ONLY raw JSON: {{'characters': [{{...}}]}}"
)

        chars_data = request_json_data(driver, char_prompt)

        # --- Validation & Auto-correction for Character Details ---
        if 'is_incomplete_or_unspecified' not in globals():
            def is_incomplete_or_unspecified(char_obj):
                visual = char_obj.get('visual_details', {})
                required_fields = [
                    'gender', 'age_appearance', 'body_type', 'height_estimate', 'skin_tone',
                    'hair_color', 'hair_style', 'eye_color', 'clothing_style', 'clothing_colors',
                    'accessories', 'notable_features'
                ]
                for k in required_fields:
                    v = visual.get(k, None)
                    if v is None or v == '' or v == [] or (isinstance(v, str) and v.strip().lower() in ['unspecified', 'none', '']):
                        return True
                return False

        # If any character is incomplete, re-prompt ChatGPT to fill in ALL missing details
        if isinstance(chars_data, dict) and 'characters' in chars_data and isinstance(chars_data['characters'], list):
            incomplete_chars = [c for c in chars_data['characters'] if is_incomplete_or_unspecified(c)]
            if incomplete_chars:
                log_step(f"Detected {len(incomplete_chars)} characters with missing/unspecified details. Auto-correcting...", level="warning")
                reprompt = (
                    f"MODE:JSON\nYou previously returned some characters with missing or 'Unspecified' details. Here is your previous output:\n"
                    f"{json.dumps(chars_data, indent=2, ensure_ascii=False)}\n"
                    f"IMPORTANT: For EVERY character, ALL visual_details fields MUST be fully specified. Never use 'Unspecified', 'None', or leave blank. If any field is missing, unclear, or not in the story, invent a creative, visually striking, and genre-consistent value. Be consistent and imaginative.\n"
                    f"For male characters, physique MUST be described as extremely muscular, bodybuilder or fitness athlete physique, large, strong, and dominant. For female characters, physique MUST be beautiful, athletic, fit, feminine, with visible but not excessive muscle. For demons/monsters, default to huge, grotesquely muscular, and menacing unless otherwise described.\n"
                    f"Output ONLY raw JSON: {{'characters': [{{...}}]}}"
                )
                chars_data = request_json_data(driver, reprompt)

        if not isinstance(chars_data, dict) or 'characters' not in chars_data or not isinstance(chars_data['characters'], list):
            raw_resp = get_last_response_text(driver); raw_path = project_path / f"{CHARACTERS_FILENAME}.raw_error.txt"
            with open(raw_path, 'w', encoding='utf-8') as f: f.write(raw_resp)
            raise RuntimeError(f"Failed character data structure. See {raw_path.name}")
        elif not chars_data['characters']: log_step("Character list empty.", level="warning")
        char_file_path = project_path / CHARACTERS_FILENAME
        with open(char_file_path, 'w', encoding='utf-8') as f: json.dump(chars_data, f, indent=2, ensure_ascii=False)
        log_step(f"Characters saved: {char_file_path} ({len(chars_data.get('characters',[]))} found)", level="success")

        # --- Scene Breakdown (Batched) ---
        log_step(f"Starting Scene Breakdown ({total_scenes} scenes)...", level="info", important=True)
        all_scenes_list = []; num_batches = (total_scenes + SCENES_PER_BATCH - 1) // SCENES_PER_BATCH
        last_scene_desc = ""; characters_context_str = "No character details."
        try: # Create concise context
            if chars_data and chars_data.get('characters'):
                summaries = [f"- {c['name']}: {c.get('overall_description', '')[:50]}..." for c in chars_data['characters'] if c.get('name')]
                if summaries: characters_context_str = "CHAR REF:\n" + "\n".join(summaries)
        except Exception as e: log_step(f"Warn: Err creating char context: {e}", level="warning")
        for i in range(num_batches):
            b_start = i * SCENES_PER_BATCH + 1; b_end = min((i + 1) * SCENES_PER_BATCH, total_scenes)
            log_step(f"Requesting scenes {b_start}-{b_end} (Batch {i+1}/{num_batches})", level="info")
            scene_prompt = (f"MODE:JSON\nBreak story ({total_scenes} total) into scenes {b_start}-{b_end}.\n"
                            f"STORY (Excerpt):\n{approved_text[:600]}...\n{characters_context_str}\n" # Slightly longer excerpt
                            f"{('PREV SCENE DESC: '+last_scene_desc) if last_scene_desc else ''}\n"
                            f"TASK: Gen scenes {b_start}-{b_end}. Fields: scene_number (int {b_start}-{b_end}), "
                            f"scene_description, focus_character (name or 'None'), background, action.\n"
                            f"Output ONLY raw JSON: {{'scenes': [{{...}}]}}")
            batch_data = request_json_data(driver, scene_prompt); valid_batch = False
            if isinstance(batch_data, dict) and 'scenes' in batch_data and isinstance(batch_data['scenes'], list) and batch_data['scenes']:
                first_s = batch_data['scenes'][0]; last_s = batch_data['scenes'][-1]
                if isinstance(first_s, dict) and isinstance(first_s.get('scene_number'), int) and isinstance(last_s.get('scene_number'), int) and b_start <= first_s['scene_number'] <= last_s['scene_number'] <= b_end:
                     log_step(f"Valid batch {i+1}.", level="success"); all_scenes_list.extend(batch_data['scenes'])
                     last_scene_desc = last_s.get('scene_description', ''); valid_batch = True
                else: log_step(f"Warn: Invalid scene numbers/structure batch {i+1}.", level="warning")
            else: log_step(f"Error: Invalid batch structure {i+1}.", level="error")
            if not valid_batch: last_scene_desc = "" # Reset context
            time.sleep(4) # Wait between batches
        if not all_scenes_list: raise RuntimeError("Failed scene generation.")
        # Re-sequence scenes
        final_ordered_scenes = []; current_num = 1
        for scene in all_scenes_list:
            if isinstance(scene, dict): scene['scene_number'] = current_num; final_ordered_scenes.append(scene); current_num += 1
        total_collected = len(final_ordered_scenes)
        if total_collected != total_scenes: log_step(f"Warn: Collected {total_collected} scenes != target {total_scenes}.", level="warning")
        final_scenes_output = {"scenes": final_ordered_scenes}
        scenes_file_path = project_path / SCENES_FILENAME
        with open(scenes_file_path, 'w', encoding='utf-8') as f: json.dump(final_scenes_output, f, indent=2, ensure_ascii=False)
        log_step(f"Scenes saved: {scenes_file_path} ({total_collected} scenes)", level="success")

        # --- Detailed Image Prompt Generation ---
        log_step("Starting Image Prompt Gen...", level="info", important=True)
        img_prompts = []; char_details_map = {}
        try: # Build map
            if chars_data and chars_data.get('characters'):
                char_details_map = {c.get('name'): c.get('visual_details', {}) for c in chars_data['characters'] if isinstance(c, dict) and c.get('name')}
        except Exception as e: log_step(f"Warn: Err building char map: {e}", level="warning")
        scenes_to_process = final_scenes_output.get('scenes', []); total_prompts = len(scenes_to_process)
        log_step(f"Generating prompts for {total_prompts} scenes...", level="info")
        for idx, scene in enumerate(scenes_to_process):
            if not isinstance(scene, dict): continue
            num = scene.get('scene_number', idx+1); desc = scene.get('scene_description',''); focus = scene.get('focus_character'); bg = scene.get('background',''); act = scene.get('action','')
            log_step(f"Gen prompt {idx+1}/{total_prompts} for Scene {num}", level="debug")
            # Build image gen prompt
            img_gen_prompt = f"MODE:JSON\nTASK: Gen detailed image prompt for Scene {num}.\nCONTEXT: D='{desc}', BG='{bg}', A='{act}'\n"
            focus_instr = "Focus on scene context."; char_part = "CHAR: None/Env focus.\n"
            if isinstance(focus, str) and focus != "None" and focus in char_details_map:
                vis = char_details_map.get(focus)
                if vis and isinstance(vis, dict):
                    details = "; ".join([f"{k.replace('_',' ')}:{v}" for k,v in vis.items() if v and v not in ['None','Unspecified']])
                    if details: char_part = f"CHAR: {focus}\n- VISUALS: {details}\n"; focus_instr = f"MUST feature {focus} w/ visuals."
                    else: char_part = f"CHAR: {focus} (no details).\n"; focus_instr = f"Feature {focus} from context."
                else: char_part = f"CHAR: {focus} (details error).\n"; focus_instr = f"Focus on {focus} based on context."
            img_gen_prompt += char_part
            img_gen_prompt += f"STYLE: Cinematic, detailed, mythic. LIGHTING: (e.g., dramatic). CAM: (e.g., low angle). MOOD: (e.g., tense).\n"
            img_gen_prompt += f"INSTRUCTIONS: Combine details. {focus_instr}\n"
            try: num_json = int(num)
            except: num_json = f'"{num}"'
            img_gen_prompt += f"Output ONLY raw JSON: {{\"scene_number\": {num_json}, \"prompt_text\": \"<prompt>\"}}"
            # Request and process
            img_data = request_json_data(driver, img_gen_prompt); current_out = {"scene_number": num, "prompt_text": None}
            if isinstance(img_data, dict) and 'prompt_text' in img_data and isinstance(img_data['prompt_text'], str) and str(img_data.get('scene_number')) == str(num):
                txt = img_data['prompt_text'].strip()
                if txt: current_out['prompt_text'] = txt; log_step(f"OK prompt {num}.", level="debug")
                else: current_out['prompt_text'] = f"Placeholder: Empty prompt scene {num}."; log_step(f"Warn: Empty prompt {num}.", level="warning")
            else: current_out['prompt_text'] = f"Error: Failed structure scene {num}."; log_step(f"Error: Failed prompt {num}.", level="warning")
            img_prompts.append(current_out); time.sleep(2.5) # Slightly longer wait between image prompts
        img_file_path = project_path / IMAGE_PROMPTS_FILENAME
        with open(img_file_path, 'w', encoding='utf-8') as f: json.dump(img_prompts, f, indent=2, ensure_ascii=False)
        log_step(f"Image prompts saved: {img_file_path} ({len(img_prompts)} total)", level="success")

        # --- Finalization ---
        log_step("All generation steps completed.", level="success", important=True)
        final_message = f"MODE:COMMENTARY\nScript finished for '{topic}'. Assets in '{project_path.name}'."
        try: send_message(driver, final_message)
        except Exception as e: log_step(f"Warn: Could not send final message: {e}", level="warning")
        log_step(f"Total execution time: {time.time() - start_time:.2f} seconds.", level="info")

    except KeyboardInterrupt: log_step("\n[!] Script interrupted by user.", level="warning", important=True);
    except RuntimeError as e: log_step(f"[X] Critical runtime error: {e}", level="error", important=True); log.exception("Runtime Error Traceback")
    except WebDriverException as e: log_step(f"[X] WebDriver error: {e}", level="error", important=True); log.exception("WebDriver Error Traceback"); print("\n[!!!] WebDriver Error!")
    except Exception as e: log_step(f"[X] Unexpected error: {e}", level="error", important=True); log.exception("Unexpected Error Traceback")
    finally:
        # Save cookies if progress was made
        if driver and COOKIE_PATH and project_path and project_path.exists():
             log_step("Attempting final cookie save...", level="debug")
             save_cookies(driver, COOKIE_PATH)
        # Quit driver
        if driver:
            log_step("Attempting to close WebDriver...", level="info")
            try: driver.quit(); log_step("WebDriver closed.", level="success")
            except OSError as e: log_step(f"Warn: OS Error closing WebDriver (already closed?): {e}", level="warning")
            except Exception as e: log_step(f"Warn: Error closing WebDriver: {e}", level="warning")
        log_step("===== Mythic Movie Generator Finished =====", level="info", important=True)
        logging.shutdown()

if __name__ == '__main__':
    main()