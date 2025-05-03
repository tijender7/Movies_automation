# generate_video_prompts.py v1
# Generates video prompts via ChatGPT by analyzing approved images pasted via clipboard.

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import time
import json
import os
import sys
import platform # To check OS
import re
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import logging
import argparse
from dotenv import load_dotenv
import glob # For finding approved images

# --- Platform Specific Clipboard Handling ---
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    try:
        import win32clipboard
        from PIL import Image
        import io # Needed for BytesIO
        print("[*] Successfully imported Windows-specific libraries (pywin32, Pillow). Image pasting enabled.")
    except ImportError:
        print("[!] Warning: Could not import 'pywin32' or 'Pillow'. Image pasting will FAIL.")
        print("    Install them: pip install pywin32 Pillow")
        IS_WINDOWS = False # Disable if imports fail
else:
    print("[*] Info: Not running on Windows. Clipboard image pasting might require different libraries (e.g., pyperclip + image library) and code adjustments.")
    # Placeholder for other OS - this script currently only supports Windows pasting
    IS_WINDOWS = False

# --- Import project config ---
try:
    import config_v2 as config
except ImportError:
    print(f"ERROR: Failed to import config_v2.py. Ensure it exists.")
    exit(1)

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
DOTENV_PATH = Path('.env')
LOG_FILE = Path("logs") / f"generate_video_prompts_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects"))
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME', "characters") # To save prompts
APPROVED_IMAGES_FOLDER_NAME = getattr(config, 'APPROVED_IMAGES_FOLDER_NAME', "Approved_images_for_videos") # Source of images
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json" # Filename suffix for output
COOKIE_PATH = Path(r"H:\projects\Movie_trailer\movie_vignette_generator\chatgpt_cookies.json")

# --- ChatGPT Settings ---
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o" # Ensure GPT-4o is used
CHATGPT_LOGIN_TIMEOUT = 120
CHATGPT_RESPONSE_TIMEOUT = 300
CHATGPT_JSON_RETRIES = 3
IMAGE_PASTE_WAIT_TIMEOUT = 45 # Max seconds to wait for send button re-enable after paste
IMAGE_PASTE_CHECK_INTERVAL = 0.5 # Seconds between checks

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING); logging.getLogger('werkzeug').setLevel(logging.WARNING)
    tf_logger = logging.getLogger('tensorflow');
    if tf_logger: tf_logger.setLevel(logging.ERROR) # Further suppress TF
except Exception as e: print(f"ERROR setting up logging: {e}"); logging.basicConfig( level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}";
    if important: final_msg = f"--- {final_msg} ---"
    if level == "error": log.error(final_msg)
    elif level == "warning": log.warning(final_msg)
    elif level == "debug": log.debug(final_msg)
    else: log.info(final_msg)

# --- Helper Function: Sanitize Name ---
def sanitize_name(name):
    if not isinstance(name, str): name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name); sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized); sanitized = re.sub(r'[-]+', '-', sanitized); sanitized = sanitized.strip('_-'); max_len = 60
    if len(sanitized) > max_len: sanitized = sanitized[:max_len].strip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Helper Function: Sanitize for ChromeDriver ---
def sanitize_for_chromedriver(text):
    if not isinstance(text, str): return text
    return re.sub(r'[^\u0000-\uffff]', '', text)

# --- Project Path Helpers ---
def find_latest_project_dir(base_dir):
    log_step(f"Searching for latest project in: {base_dir}")
    try:
        base = Path(base_dir);
        if not base.is_dir(): log_step(f"Base project dir not found: {base_dir}", level="error"); return None
        subdirs = [d for d in base.iterdir() if d.is_dir()]
        if not subdirs: log_step(f"No project subdirs found: {base_dir}", level="error"); return None
        subdirs_sorted = sorted(subdirs, key=lambda d: d.stat().st_mtime, reverse=True)
        latest_dir = subdirs_sorted[0]; log_step(f"Found latest project: {latest_dir.name}", level="success"); return latest_dir
    except Exception as e: log_step(f"Error finding latest project: {e}", level="error"); return None

def extract_theme_from_project_path(project_path: Path):
    folder_name = project_path.name; match = re.match(r'^.+?_(.+?)_\d{8}_\d{6}$', folder_name)
    if match: theme_underscores = match.group(1); theme_name = theme_underscores.replace('_', ' '); log_step(f"Extracted theme: '{theme_name}'"); return theme_name
    else: log_step(f"Cannot extract theme from: {folder_name}", level="warning"); return "Default Futuristic Theme"

# --- Clipboard Helper ---
def copy_image_to_clipboard(image_path):
    """Copies the specified image file to the clipboard (Windows only currently)."""
    if not IS_WINDOWS:
        log_step("Clipboard image pasting is only supported on Windows with pywin32/Pillow.", level="error")
        return False
    if not Path(image_path).is_file():
        log_step(f"Image file not found: {image_path}", level="error")
        return False

    log_step(f"Attempting to copy image to clipboard: {Path(image_path).name}", level="debug")
    try:
        image = Image.open(image_path)
        # Convert to RGB (necessary for BMP format)
        image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, "BMP")
        data = output.getvalue()[14:]  # The BMP header must be removed for CF_DIB
        output.close()

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        win32clipboard.CloseClipboard()
        log_step(" Image successfully copied to clipboard.", level="debug")
        return True
    except NameError:
         log_step("Error: 'win32clipboard' or 'PIL.Image' not available (should have been checked earlier).", level="error")
         return False
    except Exception as e:
        log_step(f"Failed to copy image '{Path(image_path).name}' to clipboard: {e}", level="error")
        # Ensure clipboard is closed if opened
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return False


# --- Cookie Management Functions ---
def save_cookies(driver, path):
    log_step(f"Saving cookies to: {path}")
    try:
        cookies = driver.get_cookies()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        log_step("Cookies saved.", level="success")
    except WebDriverException as e:
        log_step(f"WebDriver error saving cookies: {e}", level="error")
    except Exception as e:
        log_step(f"Error saving cookies: {e}", level="error")

def load_cookies(driver, path):
    if path.exists():
        log_step(f"Cookie file found: {path}. Loading...")
        try:
            with open(path, "r", encoding="utf-8") as f: cookies = json.load(f)
            if not isinstance(cookies, list): log_step("Invalid cookie format.", level="error"); return False
            target_domain = None
            primary_domains = ['openai.com', 'chatgpt.com']
            for cookie in cookies:
                domain_in_cookie = cookie.get("domain", "").lstrip('.')
                if any(d in domain_in_cookie for d in primary_domains):
                    target_domain = f"https://{domain_in_cookie.lstrip('.')}"
                    break
            if target_domain:
                log_step(f"Navigating to {target_domain}...")
                try:
                    driver.get(target_domain)
                    time.sleep(2)
                except Exception as e:
                    log_step(f"Warn: Nav failed {target_domain}: {e}", level="warning")
            else:
                log_step("No primary domain in cookies.", level="warning")
            loaded_count = 0; skipped_count = 0
            for cookie in cookies:
                if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie: skipped_count += 1; continue
                if 'sameSite' in cookie and cookie['sameSite'] not in ['Lax', 'Strict', 'None']: del cookie['sameSite']
                if 'expiry' in cookie and isinstance(cookie['expiry'], float): cookie['expiry'] = int(cookie['expiry'])
                try: driver.add_cookie(cookie); loaded_count += 1
                except Exception as e: skipped_count += 1
            log_step(f"Cookies loaded: {loaded_count}, Skipped: {skipped_count}", level="success"); return True
        except Exception as e: log_step(f"Error loading cookies: {e}", level="error"); return False
    else: log_step("Cookie file not found.", level="info"); return False

# --- Selenium Interaction Helpers ---
def wait_for_element(driver, by, value, timeout=30, condition=EC.presence_of_element_located):
    try: return WebDriverWait(driver, timeout).until(condition((by, value)))
    except TimeoutException: log_step(f"Timeout waiting element ({by}={value})", level="error"); raise TimeoutException(f"Element not found")
    except Exception as e: log_step(f"Error waiting element: {e}", level="error"); raise

def wait_for_input_box(driver, timeout=60):
    log_step("Waiting for input box..."); start_time = time.time()
    while time.time() - start_time < timeout:
        try: input_box = driver.find_element(By.CSS_SELECTOR, "textarea[data-id='root'], textarea#prompt-textarea");
        except NoSuchElementException: pass
        except Exception as e: log_step(f"Error searching textarea: {e}", level="warning")
        else:
            if input_box.is_displayed() and input_box.is_enabled(): log_step("Input (textarea) OK.", level="success"); return input_box
        try: input_box_div = driver.find_element(By.CSS_SELECTOR, "div[contenteditable='true']");
        except NoSuchElementException: pass
        except Exception as e: log_step(f"Error searching contenteditable: {e}", level="warning")
        else:
            if input_box_div.is_displayed(): log_step("Input (div) OK.", level="success"); return input_box_div
        time.sleep(1)
    log_step("Input box not found.", level="error"); raise TimeoutException("Input box not found")

def wait_for_response_completion(driver, timeout=CHATGPT_RESPONSE_TIMEOUT):
    start_time = time.time(); generation_started = False; stop_button_selector = "button[data-testid='stop-button']"
    while time.time() - start_time < timeout:
        stop_button_visible = False
        try: stop_buttons = driver.find_elements(By.CSS_SELECTOR, stop_button_selector);
        except: stop_buttons = []
        if stop_buttons and stop_buttons[0].is_displayed(): stop_button_visible = True;
        if not generation_started and stop_button_visible: generation_started = True
        elif generation_started and not stop_button_visible: time.sleep(1.5); return True
        if stop_button_visible: time.sleep(1)
        else:
            if not generation_started and time.time() - start_time > 10: return True
            time.sleep(0.5)
    log_step(f"Timeout waiting for response.", level="error"); raise TimeoutException("Timeout waiting for response")

def send_message(driver, message):
    """Sends text message ONLY. Does not handle image paste."""
    try:
        input_box = wait_for_input_box(driver); sanitized_message = sanitize_for_chromedriver(message)
        log_step(f"Sending prompt:\n{sanitized_message[:100]}{'...' if len(sanitized_message)>100 else ''}")
        try: driver.execute_script("arguments[0].value = ''; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));");
        except Exception: input_box.click(); time.sleep(0.1); input_box.send_keys(Keys.CONTROL + "a"); time.sleep(0.1); input_box.send_keys(Keys.DELETE); time.sleep(0.2)
        lines = sanitized_message.split('\n')
        for i, line in enumerate(lines):
            input_box.send_keys(line); time.sleep(0.05)
            if i < len(lines) - 1: input_box.send_keys(Keys.SHIFT, Keys.ENTER); time.sleep(0.1)
        time.sleep(0.3)
        send_button_selector = "button[data-testid='send-button']"
        # Wait for button to be clickable *after* typing text
        try: send_button = wait_for_element(driver, By.CSS_SELECTOR, send_button_selector, timeout=15, condition=EC.element_to_be_clickable); send_button.click()
        except TimeoutException: log_step("Send button fail after text, trying ENTER.", level="warning"); input_box.send_keys(Keys.ENTER)
        wait_for_response_completion(driver);
    except Exception as e: log_step(f"Error sending message: {e}", level="error"); raise RuntimeError(f"Failed send: {e}") from e

def wait_for_image_paste(driver, timeout=IMAGE_PASTE_WAIT_TIMEOUT):
    """Waits for the Send button to become enabled after a paste action."""
    log_step("Waiting for image paste to complete (checking Send button state)...")
    start_time = time.time()
    send_button_selector = "button[data-testid='send-button']"
    upload_started = False # Flag to ensure we see the disabled state first

    while time.time() - start_time < timeout:
        try:
            send_button = driver.find_element(By.CSS_SELECTOR, send_button_selector)
            is_enabled = send_button.is_enabled()

            if not is_enabled:
                if not upload_started:
                    log_step(" Send button disabled, upload likely started...", level="debug")
                    upload_started = True
            elif is_enabled and upload_started:
                log_step(" Send button re-enabled, assuming paste complete.", level="success")
                time.sleep(0.5) # Small buffer after enabling
                return True # Success! Saw disabled -> enabled transition
            elif is_enabled and not upload_started and (time.time() - start_time > 5):
                 # If still enabled after 5s and we never saw it disabled, paste might have failed or was instant
                 log_step(" Send button remained enabled, paste might have failed or was instant. Proceeding cautiously.", level="warning")
                 return True # Proceed, but maybe add checks later

        except NoSuchElementException:
            log_step(" Send button not found while waiting for paste.", level="warning")
            # Keep waiting, maybe it reappears
        except Exception as e:
            log_step(f" Error checking send button state: {e}", level="warning")
            # Keep waiting

        time.sleep(IMAGE_PASTE_CHECK_INTERVAL)

    log_step("Timeout waiting for image paste completion (Send button never re-enabled).", level="error")
    return False


def get_last_response_text(driver):
    try:
        response_selectors = [ "div[data-message-author-role='assistant'] .markdown", "div[data-message-author-role='assistant'] > div > div > div" ]
        response_elements = []; last_response_text = ""
        for selector in response_selectors:
            try: elements = driver.find_elements(By.CSS_SELECTOR, selector)
            except: continue
            if elements: response_elements = elements; break
        if response_elements: last_response_text = response_elements[-1].text.strip(); return last_response_text if last_response_text else ""
        else: return ""
    except Exception: return ""

# --- JSON Parsing Helpers ---
def strip_markdown_fences(text):
    if not isinstance(text, str) or not text: return ""
    pattern = r"```(?:[a-zA-Z0-9]*\n)?(.*?)```"; stripped_text = re.sub(pattern, r"\1", text, flags=re.DOTALL | re.MULTILINE)
    lines = stripped_text.split('\n'); cleaned_lines = [line for line in lines if line.strip() != '```']; return '\n'.join(cleaned_lines).strip()

def try_parse_json(response_text):
    if not response_text: log_step("Empty response for JSON parse.", level="warning"); return (False, "Empty response")
    try: parsed = json.loads(response_text); log_step("JSON parsed directly.", level="success", important=True); return (True, parsed)
    except json.JSONDecodeError: pass
    clean_text = strip_markdown_fences(response_text)
    if not clean_text: log_step("Empty response after cleaning.", level="warning"); return (False, "Empty after cleaning")
    first_bracket = -1; first_curly = -1;
    try: first_bracket = clean_text.index('[')
    except ValueError: pass
    try: first_curly = clean_text.index('{')
    except ValueError: pass
    start_idx = -1
    if first_curly != -1 and (first_curly < first_bracket or first_bracket == -1): start_idx = first_curly; end_char = '}'
    elif first_bracket != -1: start_idx = first_bracket; end_char = ']'
    else: log_step("No JSON start found.", level="warning"); return (False, "No JSON start found")
    open_count = 0; end_idx = -1
    for i in range(start_idx, len(clean_text)):
        char = clean_text[i];
        if char == clean_text[start_idx]: open_count += 1
        elif char == end_char: open_count -= 1;
        if open_count == 0: end_idx = i + 1; break
    if start_idx != -1 and end_idx != -1:
        json_substring = clean_text[start_idx:end_idx].strip()
        try: parsed = json.loads(json_substring); log_step("JSON parsed from substring.", level="success", important=True); return (True, parsed)
        except json.JSONDecodeError as e: log_step(f"JSON substring parse error: {e}", level="error"); context_len=30; error_context = json_substring[max(0, e.pos - context_len) : min(len(json_substring), e.pos + context_len)]; log_step(f"Near: ...{error_context}...", level="error"); return (False, f"JSON error: {e}")
    else: log_step("No matching JSON end found.", level="warning"); return (False, "No matching JSON end")

def request_json_data(driver, prompt, max_retries=CHATGPT_JSON_RETRIES):
    """Sends text prompt AFTER image assumed pasted, expects JSON, handles retries."""
    log_step(f"Requesting JSON data (Retries={max_retries})...")
    # Send the text prompt using the regular send_message function
    send_message(driver, prompt)
    for attempt in range(max_retries + 1):
        log_step(f"Parse Attempt {attempt + 1}/{max_retries + 1}...")
        response_text = get_last_response_text(driver)
        if not response_text:
            if attempt < max_retries: log_step("Empty response, sending retry prompt.", level="warning"); send_message(driver, "No response text received. Please provide the JSON data again."); continue
            else: log_step("Empty response on final attempt.", level="error"); break
        success, data_or_error = try_parse_json(response_text)
        if success: return data_or_error # Success!
        log_step(f"JSON parse failed: {data_or_error}", level="warning")
        if attempt < max_retries: log_step("Sending JSON fix prompt..."); fix_prompt = (f"Invalid JSON response. Error: {data_or_error}\nPlease regenerate ONLY the valid raw JSON object with 'positive_prompt' and 'negative_prompt' keys."); send_message(driver, fix_prompt)
        else: log_step("Max retries for JSON parse.", level="error"); log_step(f"Final failed response:\n---\n{response_text}\n---", level="error"); break
    log_step("Failed to get valid JSON.", level="error", important=True); return None

# --- Main Prompt Generation Logic ---
def run_video_prompt_generation(project_path: Path):
    """Generates video prompts by analyzing approved images via ChatGPT."""
    log_step(f"--- Step 1: Setup & Validation ---", important=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", level="error"); return False
    selected_theme = extract_theme_from_project_path(project_path); log_step(f"Theme: '{selected_theme}'")
    approved_images_dir = project_path / APPROVED_IMAGES_FOLDER_NAME
    if not approved_images_dir.is_dir(): log_step(f"Approved images folder not found: {approved_images_dir}", level="error"); return False
    approved_image_paths = sorted(list(approved_images_dir.glob('*.[jp][pn]g')))
    if not approved_image_paths: log_step(f"No approved images found in {approved_images_dir}", level="error"); return False
    log_step(f"Found {len(approved_image_paths)} approved images to process.")
    movie_name = project_path.name.split('_')[0] # Extract movie name

    # --- Step 2: Initialize Selenium & Login ---
    driver = None
    try:
        log_step("Initializing WebDriver..."); options = uc.ChromeOptions(); options.add_argument("--start-maximized"); driver = uc.Chrome(options=options); log_step("WebDriver OK.", level="success")
        log_step(f"Navigating to {CHATGPT_BASE_URL}..."); driver.get(CHATGPT_BASE_URL); time.sleep(4)
        log_step("Loading cookies..."); cookies_loaded = load_cookies(driver, COOKIE_PATH)
        if not cookies_loaded: log_step("Manual login needed.", important=True); print(f"\n>>> Log in to ChatGPT manually ({CHATGPT_LOGIN_TIMEOUT}s). <<<"); input(f">>> Press Enter ONLY after logged in. <<<"); log_step("User logged in."); save_cookies(driver, COOKIE_PATH)
        log_step(f"Navigating to {CHATGPT_TARGET_MODEL_URL}"); driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(3); wait_for_input_box(driver, timeout=45); log_step("ChatGPT ready.", level="success")
    except Exception as e:
        log_step(f"Failed WebDriver init/login: {e}", level="error", important=True)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False

    # --- Step 3: Process Each Approved Image ---
    log_step(f"--- Step 3: Processing {len(approved_image_paths)} Approved Images ---", important=True)
    success_count = 0; fail_count = 0; processed_count = 0

    for image_path in approved_image_paths:
        processed_count += 1
        log_step(f"--- Processing Image {processed_count}/{len(approved_image_paths)}: {image_path.name} ---", important=True)

        # 3.1: Extract Context from Filename
        filename_stem = image_path.stem # e.g., Veeru__Dharmendra__swapped_medium_00001_
        parts = filename_stem.split("__")
        if len(parts) < 3:
            log_step(f" Skipping image - Unexpected filename format: {filename_stem}", level="warning")
            fail_count += 1
            continue
        char_name_san = parts[0]
        actor_name_san = parts[1]
        original_suffix = "__".join(parts[2:])
        display_char_name = char_name_san.replace('_', ' ')
        display_actor_name = actor_name_san.replace('_', ' ')
        log_step(f" Context: Character='{display_char_name}', Actor='{display_actor_name}', Theme='{selected_theme}'")

        # 3.2: Define Output Path (Use the correctly derived actor_name_san)
        character_folder_path = project_path / CHARACTERS_FOLDER_NAME / actor_name_san # Use sanitized actor name for folder

        # Rest of the code remains the same
        character_folder_path.mkdir(parents=True, exist_ok=True) # Ensure character folder exists
        prompt_output_filename = filename_stem + VIDEO_PROMPT_OUTPUT_SUFFIX # e.g., Veeru__Dharmendra__swapped_medium_00001_.video_prompts.json
        prompt_output_path = character_folder_path / prompt_output_filename

        # 3.3: Copy Image to Clipboard
        if not copy_image_to_clipboard(image_path): fail_count += 1; continue # Skip if copy fails

        # 3.4: Paste and Wait
        try:
            input_box = wait_for_input_box(driver)
            # Clear input box before pasting (important!)
            log_step(" Clearing input box before paste...")
            try:
                input_box.click()
                time.sleep(0.1)
                input_box.send_keys(Keys.CONTROL, 'a')
                time.sleep(0.1)
                input_box.send_keys(Keys.DELETE)
                time.sleep(0.2)
            except Exception as clear_err:
                log_step(f" Error clearing input box: {clear_err}", level="warning")
            time.sleep(0.2) # Small delay after clearing
            log_step(" Pasting image...")
            input_box.send_keys(Keys.CONTROL, 'v') # Send paste command
            # Wait for upload confirmation using Send button state
            upload_ok = wait_for_image_paste(driver)
        except Exception as paste_err:
             log_step(f" Error during paste/wait process: {paste_err}", level="error"); fail_count += 1; continue

        if not upload_ok:
             log_step(" Image paste/upload failed or timed out.", level="error"); fail_count += 1; continue

        # 3.5: Send Prompts & Process (If Upload OK)
        try:
            # Prompt 1: Analysis & Movement Ideas
            prompt_1_text = f"""Analyze the freshly pasted image. Context: Movie '{movie_name}', Character '{display_char_name}' ({display_actor_name}), Theme '{selected_theme}'. Identify ALL visual elements (person, pose, clothes, background, objects, lighting). Generate ideas for MAXIMUM DYNAMIC MOVEMENT for a 5-second video clip based ONLY on this image. Focus on significant motion (body, clothes, hair, background, objects, camera, lighting). Suggest detailed POSITIVE and NEGATIVE prompt components for video generation (e.g., WanVideo2.1/AnimateDiff). Commentary welcome here."""
            # Use send_message (waits for completion) but we don't strictly need the text response yet
            log_step(" Sending Prompt 1 (Analysis)...")
            send_message(driver, prompt_1_text)
            time.sleep(1) # Small buffer before next prompt

            # Prompt 2: Strict JSON Output
            prompt_2_text = f"""Based ONLY on the dynamic movement ideas discussed for the pasted image in the previous turn, provide the final POSITIVE and NEGATIVE video generation prompts. Focus on significant animation. Output ONLY the raw JSON object below. No extra text/comments/markdown: {{ "positive_prompt": "...", "negative_prompt": "..." }}"""
            log_step(" Sending Prompt 2 (Requesting JSON)...")
            prompt_data = request_json_data(driver, prompt_2_text)

            # 3.6: Validate and Save
            if prompt_data and isinstance(prompt_data, dict) and "positive_prompt" in prompt_data and "negative_prompt" in prompt_data:
                try:
                    with open(prompt_output_path, 'w', encoding='utf-8') as f: json.dump(prompt_data, f, indent=2, ensure_ascii=False)
                    log_step(f" Successfully generated and saved video prompts to {prompt_output_path.name}", level="success"); success_count += 1
                except IOError as e: log_step(f" Failed to save video prompts: {e}", level="error"); fail_count += 1
            else: log_step(f" Failed to get valid video prompts JSON after retries.", level="error"); fail_count += 1

        except Exception as prompt_err:
            log_step(f" Error during ChatGPT prompting stage for {image_path.name}: {prompt_err}", level="error")
            fail_count += 1
            # Try to recover by clearing input?
            try: driver.execute_script("arguments[0].value = ''; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));")
            except: pass


    # --- Step 4: Cleanup and Summary ---
    log_step(f"--- Step 4: Cleanup & Summary ---", important=True)
    if driver:
        log_step("Closing WebDriver...")
        try:
            driver.quit()
            log_step("WebDriver closed.", level="success")
        except Exception as e:
            log_step(f"Error closing WebDriver: {e}", level="warning")
    log_step(f"Video Prompt Generation Summary:")
    log_step(f"- Total Approved Images Processed: {processed_count}")
    log_step(f"- Prompt Sets Successfully Generated: {success_count}")
    log_step(f"- Prompt Sets Failed: {fail_count}")
    return fail_count == 0

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate VIDEO prompts via ChatGPT by analyzing approved images."); parser.add_argument("-p", "--project_path", help="Path to project folder. Omit for latest.")
    args = parser.parse_args(); log_step("===== Generate Video Prompts Script Started =====", important=True)
    try: load_dotenv(dotenv_path=DOTENV_PATH)
    except Exception as e: log_step(f"Note: .env load error: {e}", level="debug")

    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not Path(project_to_process).parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", level="error", important=True); sys.exit(1)

    log_step(f"Using project: {project_to_process.name}")
    success = run_video_prompt_generation(project_to_process)

    if success: log_step("Script finished successfully.", level="success", important=True); sys.exit(0)
    else: log_step("Script finished with errors.", level="error", important=True); sys.exit(1)