# generate_prompts_chatgpt.py v9
# Rigorous syntax check applied to ensure all blocks are correctly indented.

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
import platform
import re
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import logging
import argparse
from dotenv import load_dotenv

# --- Web Server & Image Processing Imports ---
# NOTE: Not strictly needed, but kept for potential future use / copied helpers
try:
    from flask import Flask, request, render_template_string, send_from_directory, abort
    import cv2
    from mtcnn.mtcnn import MTCNN
    from PIL import Image
except ImportError:
    print("INFO: Flask/CV2/MTCNN/Pillow not installed (not required for prompt generation).")
    pass # Okay if not installed for this script

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
DOTENV_PATH = Path('.env')
LOG_FILE = Path("logs") / f"generate_prompts_chatgpt_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"
PROJECTS_BASE_DIR = Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects")
CHARACTERS_FOLDER_NAME = "characters"
SOURCE_ACTORS_FOLDER_NAME = "source_actors"
INFO_JSON_FILENAME = "chatgpt_movie_info.json"
PROMPT_OUTPUT_FILENAME = "image_prompts.json"
COOKIE_PATH = Path(r"H:\projects\Movie_trailer\movie_vignette_generator\chatgpt_cookies.json")

# --- API Key Names ---
TAVILY_API_KEY_NAME = "TAVILY_API_KEY"

# --- ChatGPT Settings ---
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o"
CHATGPT_LOGIN_TIMEOUT = 120
CHATGPT_RESPONSE_TIMEOUT = 300
CHATGPT_JSON_RETRIES = 3

# --- Logging Setup ---
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    tf_logger = logging.getLogger('tensorflow')
    if tf_logger: tf_logger.setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
except Exception as e:
    print(f"ERROR setting up logging: {e}")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}"
    if important: final_msg = f"--- {final_msg} ---"
    if level == "error": log.error(final_msg)
    elif level == "warning": log.warning(final_msg)
    elif level == "debug": log.debug(final_msg)
    else: log.info(final_msg)

# --- Helper Function: Sanitize Name ---
def sanitize_name(name):
    if not isinstance(name, str): name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name)
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized); sanitized = re.sub(r'[-]+', '-', sanitized)
    sanitized = sanitized.strip('_-'); max_len = 60
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
        if not base.is_dir(): log_step(f"Base dir not found: {base_dir}", level="error"); return None
        subdirs = [d for d in base.iterdir() if d.is_dir()]
        if not subdirs: log_step(f"No subdirs found in: {base_dir}", level="error"); return None
        subdirs_sorted = sorted(subdirs, key=lambda d: d.stat().st_mtime, reverse=True)
        latest_dir = subdirs_sorted[0]; log_step(f"Found latest: {latest_dir.name}", level="success"); return latest_dir
    except Exception as e: log_step(f"Error finding latest project: {e}", level="error"); return None

def extract_theme_from_project_path(project_path: Path):
    folder_name = project_path.name; match = re.match(r'^.+?_(.+?)_\d{8}_\d{6}$', folder_name)
    if match: theme_underscores = match.group(1); theme_name = theme_underscores.replace('_', ' '); log_step(f"Extracted theme: '{theme_name}'"); return theme_name
    else: log_step(f"Cannot extract theme from: {folder_name}", level="warning"); return "Default Futuristic Theme"

# --- Cookie Management Functions ---
def save_cookies(driver, path):
    log_step(f"Saving cookies to: {path}")
    try:
        cookies = driver.get_cookies()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use with statement for safer file handling
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        log_step("Cookies saved.", level="success")
    except WebDriverException as e: log_step(f"WebDriver error saving cookies: {e}", level="error")
    except Exception as e: log_step(f"Error saving cookies: {e}", level="error")

def load_cookies(driver, path):
    if path.exists():
        log_step(f"Cookie file found: {path}. Loading...")
        try:
            with open(path, "r", encoding="utf-8") as f: cookies = json.load(f)
            if not isinstance(cookies, list): log_step("Invalid cookie format.", level="error"); return False
            target_domain = None; primary_domains = ['openai.com', 'chatgpt.com']
            for cookie in cookies:
                domain_in_cookie = cookie.get("domain", "").lstrip('.')
                if any(d in domain_in_cookie for d in primary_domains):
                    target_domain = f"https://{domain_in_cookie.lstrip('.')}/" # Ensure https and remove leading dot
                    break
            if target_domain:
                 log_step(f"Navigating to {target_domain}...")
                 try:
                     driver.get(target_domain)
                     time.sleep(2)
                 except Exception as e:
                     log_step(f"Warn: Nav failed {target_domain}: {e}", level="warning")
            else: log_step("No primary domain in cookies.", level="warning")
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
    log_step("Waiting for ChatGPT input box..."); start_time = time.time()
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
        try: send_button = wait_for_element(driver, By.CSS_SELECTOR, send_button_selector, timeout=10, condition=EC.element_to_be_clickable); send_button.click()
        except TimeoutException: log_step("Send button fail, trying ENTER.", level="warning"); input_box.send_keys(Keys.ENTER)
        wait_for_response_completion(driver);
    except Exception as e: log_step(f"Error sending message: {e}", level="error"); raise RuntimeError(f"Failed send: {e}") from e

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
    if not text: return ""; pattern = r"```(?:[a-zA-Z0-9]*\n)?(.*?)```"; stripped_text = re.sub(pattern, r"\1", text, flags=re.DOTALL | re.MULTILINE)
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
    log_step(f"Requesting JSON data (Retries={max_retries})...")
    send_message(driver, prompt)
    for attempt in range(max_retries + 1):
        log_step(f"Parse Attempt {attempt + 1}/{max_retries + 1}...")
        response_text = get_last_response_text(driver)
        if not response_text:
            if attempt < max_retries: log_step("Empty response, retrying prompt.", level="warning"); send_message(driver, "No response. Please provide JSON again."); continue
            else: log_step("Empty response on final attempt.", level="error"); break
        success, data_or_error = try_parse_json(response_text)
        if success: return data_or_error
        log_step(f"JSON parse failed: {data_or_error}", level="warning")
        if attempt < max_retries: log_step("Sending JSON fix prompt..."); fix_prompt = (f"Invalid JSON. Error: {data_or_error}\nRegenerate ONLY valid raw JSON."); send_message(driver, fix_prompt)
        else: log_step("Max retries for JSON parse.", level="error"); log_step(f"Final failed response:\n---\n{response_text}\n---", level="error"); break
    log_step("Failed to get valid JSON.", level="error", important=True); return None

# --- Main Prompt Generation Logic ---
def run_prompt_generation(project_path: Path):
    log_step(f"--- Step 1: Setup & Validation ---", important=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", level="error"); return False
    selected_theme = extract_theme_from_project_path(project_path)
    if not selected_theme or selected_theme == "Default Futuristic Theme": log_step(f"Cannot determine theme for {project_path.name}.", level="error"); return False
    log_step(f"Theme: '{selected_theme}'")
    info_json_path = project_path / INFO_JSON_FILENAME
    if not info_json_path.is_file(): log_step(f"Info JSON not found: {info_json_path}", level="error"); return False
    actor_details_map = {}
    try:
        with open(info_json_path, 'r', encoding='utf-8') as f: movie_data = json.load(f)
        if "movie_characters" not in movie_data: raise ValueError("Missing 'movie_characters' key")
        for item in movie_data["movie_characters"]:
            actor_name = item.get("actor_name");
            if actor_name: actor_details_map[actor_name] = item # Map by original actor name
        log_step(f"Loaded details for {len(actor_details_map)} actors from {INFO_JSON_FILENAME}")
    except Exception as e: log_step(f"Error loading/parsing {info_json_path}: {e}", level="error"); return False

    source_actors_dir = project_path / SOURCE_ACTORS_FOLDER_NAME
    if not source_actors_dir.is_dir(): log_step(f"Source actors directory not found: {source_actors_dir}", level="error"); return False
    approved_actor_files = list(source_actors_dir.glob('*.[jp][pn]g')); approved_actors_set = set()
    log_step(f"Checking {len(approved_actor_files)} files in {source_actors_dir}...")
    for f in approved_actor_files:
        stem = f.stem; last_underscore_index = stem.rfind('_')
        if last_underscore_index > 0 and stem[last_underscore_index+1:].isdigit(): base_name_part = stem[:last_underscore_index]
        else: base_name_part = stem
        actor_name_to_match = base_name_part.replace('_', ' ')
        if actor_name_to_match in actor_details_map: approved_actors_set.add(actor_name_to_match); log_step(f" Match: '{f.name}' -> Actor '{actor_name_to_match}'", level="debug")
        else: log_step(f"Warning: Cannot match '{f.name}' (parsed as '{actor_name_to_match}')", level="warning")
    approved_actor_list = sorted(list(approved_actors_set))
    if not approved_actor_list: log_step(f"No approved actors found matching info JSON.", level="error"); return False
    log_step(f"Found {len(approved_actor_list)} unique approved actors: {', '.join(approved_actor_list)}", level="success")

    driver = None
    try:
        log_step("Initializing WebDriver..."); options = uc.ChromeOptions(); options.add_argument("--start-maximized"); driver = uc.Chrome(options=options); log_step("WebDriver OK.", level="success")
        log_step(f"Navigating to {CHATGPT_BASE_URL}..."); driver.get(CHATGPT_BASE_URL); time.sleep(4)
        log_step("Loading cookies..."); cookies_loaded = load_cookies(driver, COOKIE_PATH)
        if not cookies_loaded: log_step("Manual login needed.", important=True); print(f"\n>>> Log in to ChatGPT manually ({CHATGPT_LOGIN_TIMEOUT}s). <<<"); input(f">>> Press Enter ONLY after logged in. <<<"); log_step("User logged in."); save_cookies(driver, COOKIE_PATH)
        log_step(f"Navigating to {CHATGPT_TARGET_MODEL_URL}"); driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(3); wait_for_input_box(driver, timeout=45); log_step("ChatGPT ready.", level="success")
    except Exception as e:
        log_step(f"Failed WebDriver init/login: {e}", level="error", important=True);
        # Ensure driver quit happens even if init fails partially
        if driver:
            try: driver.quit()
            except: pass # Ignore errors during quit after failure
        return False # Exit if we cannot initialize browser/login

    log_step(f"--- Step 2: Generating Prompts ---", important=True); success_count = 0; fail_count = 0; processed_count = 0
    for actor_name in approved_actor_list:
        processed_count += 1; log_step(f"-- Actor {processed_count}/{len(approved_actor_list)}: {actor_name} --")
        if actor_name not in actor_details_map: log_step(f"Cannot find details. Skipping.", level="warning"); fail_count += 1; continue
        actor_details = actor_details_map[actor_name]; character_name = actor_details.get("character_name", "Unknown"); description = actor_details.get("description", "N/A"); release_year = actor_details.get("release_year", "")
        movie_name = project_path.name.split('_')[0] # Simple movie name extraction
        actor_name_sanitized = sanitize_name(actor_name); character_folder_path = project_path / CHARACTERS_FOLDER_NAME / actor_name_sanitized; character_folder_path.mkdir(parents=True, exist_ok=True)
        prompt_output_path = character_folder_path / PROMPT_OUTPUT_FILENAME
        prompt_text = f"""Generate POSITIVE prompts for Flux image generator. NO negative prompts. Character Context: Movie: {movie_name}, Character: {character_name}, Desc: {description}, Theme: {selected_theme}. Instructions: 1. Create 'medium shot' (upper body) & 'full body shot' prompts. 2. IMPORTANT: Use generic terms ('a man', 'a woman', etc.) based on description. DO NOT use actor name '{actor_name}' or char name '{character_name}'. 3. Theme Integration: Deeply weave '{selected_theme}' aesthetic (clothing, background, atmosphere, objects, lighting). 4. Character Essence: Visually represent traits: "{description}". 5. Detail: Describe Appearance (generic face/hair), Clothing (themed), Pose/Action, Expression, Environment (themed), Lighting. 6. Style Keywords: Include 'photorealistic', 'cinematic', 'masterpiece', 'high detail', '8k', 'sharp focus', plus theme-specific keywords. Output Format: ONLY raw JSON: {{ "medium_prompt": "...", "full_body_prompt": "..." }}. No extra text/markdown."""
        log_step(f"Requesting prompts for {actor_name}...")
        prompt_data = request_json_data(driver, prompt_text)
        if prompt_data and isinstance(prompt_data, dict) and "medium_prompt" in prompt_data and "full_body_prompt" in prompt_data:
            try:
                with open(prompt_output_path, 'w', encoding='utf-8') as f:
                    json.dump(prompt_data, f, indent=2, ensure_ascii=False)
                log_step(f"Saved prompts for {actor_name}", level="success")
                success_count += 1
            except IOError as e: log_step(f"Failed save prompts {actor_name}: {e}", level="error"); fail_count += 1
        else: log_step(f"Failed get valid prompts for {actor_name}.", level="error"); fail_count += 1

    log_step(f"--- Step 3: Cleanup & Summary ---", important=True)
    if driver:
        log_step("Closing WebDriver...");
        try: driver.quit(); log_step("WebDriver closed.", level="success")
        except Exception as e: log_step(f"Error closing WebDriver: {e}", level="warning")
    log_step(f"Summary: Processed={processed_count}, Success={success_count}, Fail={fail_count}")
    return fail_count == 0

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate image prompts via ChatGPT for approved actors."); parser.add_argument("-p", "--project_path", help="Project folder path. Omit for latest.")
    args = parser.parse_args(); log_step("===== Generate Prompts Script Started =====", important=True)
    # --- SYNTAX FIX V9 ---
    try:
        load_dotenv(dotenv_path=DOTENV_PATH)
    except Exception as e:
        log_step(f"Note: .env load error: {e}", level="debug")
    # --- END SYNTAX FIX V9 ---
    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir(): log_step("Finding latest project..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
    if not project_to_process: log_step(f"No project found.", level="error", important=True); sys.exit(1)
    log_step(f"Processing project: {project_to_process.name}"); success = run_prompt_generation(project_to_process)
    if success: log_step("Script finished successfully.", level="success", important=True); sys.exit(0)
    else: log_step("Script finished with errors.", level="error", important=True); sys.exit(1)