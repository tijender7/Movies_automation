# generate_video_prompts.py v3.4 (No Names, Stronger Movement, Syntax Fixed, Progress Log)
# Generates simple positive video prompts via ChatGPT, MANDATING significant dynamic action.
# Uses generic subject descriptions (man/woman) instead of names.
# Adds a fixed, comprehensive negative prompt before saving.
# Includes progress logging.

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
import glob
import random

# --- Platform Specific Clipboard Handling ---
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    try:
        import win32clipboard
        from PIL import Image
        import io
        print("[*] Successfully imported Windows-specific libraries (pywin32, Pillow). Image pasting enabled.")
    except ImportError:
        print("[!] Warning: Could not import 'pywin32' or 'Pillow'. Image pasting will FAIL.")
        print("    Install them: pip install pywin32 Pillow")
        IS_WINDOWS = False
else:
    print("[*] Info: Not running on Windows. Clipboard image pasting might require different libraries.")
    IS_WINDOWS = False

# --- Import project config ---
try:
    import config_v2 as config
except ImportError:
    print(f"ERROR: Failed to import config_v2.py. Ensure it exists.")
    sys.exit(1)

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
DOTENV_PATH = Path('.env')
LOG_FILE = Path("logs") / f"generate_video_prompts_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects"))
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME', "characters") # Still used for folder structure
APPROVED_IMAGES_FOLDER_NAME = getattr(config, 'APPROVED_IMAGES_FOLDER_NAME', "Approved_images_for_videos")
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json"
COOKIE_PATH = getattr(config, 'COOKIE_PATH', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\chatgpt_cookies.json"))

# --- FIXED COMPREHENSIVE NEGATIVE PROMPT --- (Remains the same)
FIXED_NEGATIVE_PROMPT = "static image, motionless, frozen pose, no movement, stiff character, rigid clothes, stuck hair, inactive background, boring, plain, bad anatomy, deformed hands, extra fingers, missing fingers, fused fingers, badly drawn hands, deformed face, badly drawn face, ugly face, deformed limbs, extra limbs, three legs, blurry, low quality, worst quality, jpeg artifacts, noisy, grain, watermark, text, signature, words, letters, subtitles, jerky motion, stuttering animation, low frame rate, weird camera angle, ugly, disfigured, mutated, plastic look, cartoonish, illustration, drawing, painting, sketch, artwork, 3D render, MMD, SFM, Blender, Unity, Unreal, CGI, video game look, gaudy colors, oversaturated colors, overexposed, underexposed, washed out colors, overall greyish, messy background, cluttered background, too many people in background, walking backwards"

# --- ChatGPT Settings --- (Remains the same)
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o"
CHATGPT_LOGIN_TIMEOUT = 120
CHATGPT_RESPONSE_TIMEOUT = 300
CHATGPT_JSON_RETRIES = 3
IMAGE_PASTE_WAIT_TIMEOUT = 45
IMAGE_PASTE_CHECK_INTERVAL = 0.5

# --- Logging Setup --- (Remains the same)
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING); logging.getLogger('selenium').setLevel(logging.WARNING); logging.getLogger('undetected_chromedriver').setLevel(logging.WARNING)
    tf_logger = logging.getLogger('tensorflow');
    if tf_logger: tf_logger.setLevel(logging.ERROR)
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
    # Keep this function as it's used for folder/file naming based on original image filename
    if not isinstance(name, str): name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name); sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized); sanitized = re.sub(r'[-]+', '-', sanitized); sanitized = sanitized.strip('_-'); max_len = 60
    if len(sanitized) > max_len: sanitized = sanitized[:max_len].strip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Helper Function: Sanitize for ChromeDriver ---
def sanitize_for_chromedriver(text):
    if not isinstance(text, str): return text
    return re.sub(r'[^\u0000-\uFFFF]', '', text)

# --- Project Path Helpers ---
def find_latest_project_dir(base_dir):
    log_step(f"Searching for latest project in: {base_dir}")
    try:
        base = Path(base_dir);
        if not base.is_dir(): log_step(f"Base project dir not found: {base_dir}", level="error"); return None
        subdirs = [d for d in base.iterdir() if d.is_dir()]
        if not subdirs: log_step(f"No project subdirs found in {base_dir}", level="error"); return None
        subdirs_sorted = sorted(subdirs, key=lambda d: d.stat().st_mtime, reverse=True)
        latest_dir = subdirs_sorted[0]; log_step(f"Found latest project: {latest_dir.name}", level="success"); return latest_dir
    except Exception as e: log_step(f"Error finding latest project: {e}", level="error"); return None

def extract_theme_from_project_path(project_path: Path):
    folder_name = project_path.name
    match = re.match(r'^.+?_(.+?)_\d{8}_\d{6}$', folder_name)
    if match:
        theme_underscores = match.group(1); theme_name = theme_underscores.replace('_', ' ')
        log_step(f"Extracted theme: '{theme_name}' from folder name.")
        return theme_name
    else:
        log_step(f"Cannot extract theme from folder name format: {folder_name}. Using default.", level="warning")
        return "Default Generic Theme"

# --- Clipboard Helper ---
def copy_image_to_clipboard(image_path):
    if not IS_WINDOWS: log_step("Clipboard image pasting only supported on Windows.", level="error"); return False
    if not Path(image_path).is_file(): log_step(f"Image file not found: {image_path}", level="error"); return False
    log_step(f"Copying image to clipboard: {Path(image_path).name}", level="debug")
    try:
        image = Image.open(image_path)
        if image.mode != "RGB": image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, "BMP"); data = output.getvalue()[14:]; output.close()
        win32clipboard.OpenClipboard(); win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data); win32clipboard.CloseClipboard()
        log_step(" Image successfully copied.", level="debug"); return True
    except NameError: log_step("Error: pywin32/Pillow not available.", level="error"); return False
    except Exception as e:
        log_step(f"Failed copy image '{Path(image_path).name}': {e}", level="error")
        try: win32clipboard.CloseClipboard()
        except Exception: pass
        return False

# --- Cookie Management Functions ---
def save_cookies(driver, path):
    log_step(f"Saving cookies to: {path}")
    try:
        cookies = driver.get_cookies(); path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f: json.dump(cookies, f, indent=2)
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
                domain = cookie.get("domain", "").lstrip('.')
                if any(d in domain for d in primary_domains): target_domain = f"https://{domain}"; break
            base_url_nav = False
            if target_domain:
                log_step(f"Navigating to {target_domain} for cookie context...")
                try: driver.get(target_domain); time.sleep(2)
                except Exception as e: log_step(f"Warn: Nav to {target_domain} failed: {e}. Trying base URL.", level="warning"); base_url_nav = True
            else: log_step("No primary domain cookie. Navigating base URL.", level="warning"); base_url_nav = True
            if base_url_nav:
                try: driver.get(CHATGPT_BASE_URL); time.sleep(2)
                except Exception as e: log_step(f"Error navigating base URL: {e}", level="error"); return False
            loaded = 0; skipped = 0
            for cookie in cookies:
                if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie: skipped += 1; continue
                if 'sameSite' in cookie and cookie['sameSite'] not in ['Lax', 'Strict', 'None']: del cookie['sameSite']
                if 'expiry' in cookie and isinstance(cookie['expiry'], float): cookie['expiry'] = int(cookie['expiry'])
                try: driver.add_cookie(cookie); loaded += 1
                except Exception: skipped += 1
            log_step(f"Cookies loaded: {loaded}, Skipped: {skipped}", level="success")
            log_step(f"Navigating to target model URL: {CHATGPT_TARGET_MODEL_URL}"); driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(3); return True
        except Exception as e: log_step(f"Error loading cookies: {e}", level="error"); return False
    else: log_step("Cookie file not found.", level="info"); return False

# --- Selenium Interaction Helpers ---
def wait_for_element(driver, by, value, timeout=30, condition=EC.presence_of_element_located):
    try: return WebDriverWait(driver, timeout).until(condition((by, value)))
    except TimeoutException: log_step(f"Timeout element ({by}={value}) {timeout}s.", level="error"); raise TimeoutException(f"Element ({by}={value}) timeout")
    except Exception as e: log_step(f"Error waiting element ({by}={value}): {e}", level="error"); raise

def wait_for_input_box(driver, timeout=60):
    log_step("Waiting ChatGPT input box..."); start = time.time()
    selectors = ["textarea[data-id='root']", "textarea#prompt-textarea", "div[contenteditable='true']"]
    while time.time() - start < timeout:
        for sel in selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    ready = (el.tag_name=='textarea' and el.is_enabled()) or (el.tag_name=='div')
                    if ready: log_step(f"Input box ({el.tag_name}) OK.", level="success"); return el
            except: pass
        time.sleep(1)
    log_step("Input box timeout.", level="error"); raise TimeoutException("Input box timeout")

def wait_for_response_completion(driver, timeout=CHATGPT_RESPONSE_TIMEOUT):
    log_step("Waiting response completion..."); start = time.time()
    started = False; stop_sel = "button[aria-label*='Stop generating']"
    while time.time() - start < timeout:
        visible = False
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, stop_sel)
            for btn in btns:
                if btn.is_displayed(): visible = True; break
        except: pass
        if not started and visible: started = True; log_step(" Generation detected.", level="debug")
        elif started and not visible: log_step(" Stop button gone, assuming complete.", level="debug"); time.sleep(1.5); log_step("Response OK.", level="success"); return True
        if not started and (time.time() - start > 15): log_step("No generation detected, assuming complete.", level="warning"); return True
        time.sleep(1 if visible else 0.5)
    log_step(f"Response timeout ({timeout}s).", level="error"); raise TimeoutException("Response timeout")

# ** CORRECTED send_text_message **
def send_text_message(driver, message):
    try:
        box = wait_for_input_box(driver); msg = sanitize_for_chromedriver(message)
        log_step(f"Sending text (len {len(msg)}):\n{msg[:150]}{'...' if len(msg)>150 else ''}")
        try: driver.execute_script("arguments[0].value = ''; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", box); time.sleep(0.2)
        except Exception:
            try: box.click(); time.sleep(0.1); key = Keys.COMMAND if platform.system() == "Darwin" else Keys.CONTROL; box.send_keys(key + "a"); time.sleep(0.1); box.send_keys(Keys.DELETE); time.sleep(0.2)
            except Exception as clear_e: log_step(f"Warn: Clear failed: {clear_e}", level="warning")

        lines = msg.split('\n')
        for i, line in enumerate(lines):
            box.send_keys(line)
            time.sleep(0.05) # Small delay helps
            # ** CORRECTED INDENTATION **
            if i < len(lines) - 1:
                box.send_keys(Keys.SHIFT, Keys.ENTER)
                time.sleep(0.1)

        time.sleep(0.5)
        send_sel = "button[data-testid='send-button']"
        try: btn = wait_for_element(driver, By.CSS_SELECTOR, send_sel, 20, EC.element_to_be_clickable); btn.click(); log_step("Send clicked.", level="debug")
        except TimeoutException:
            log_step("Send timeout, trying ENTER.", level="warning")
            try: box.send_keys(Keys.ENTER); log_step("ENTER sent.", level="debug")
            except Exception as enter_e: log_step(f"ENTER failed: {enter_e}", level="error"); raise RuntimeError("Send fail (btn/ENTER)") from enter_e
        except Exception as send_e: log_step(f"Send click error: {send_e}", level="error"); raise RuntimeError("Send fail (click)") from send_e
        wait_for_response_completion(driver)
    except (TimeoutException, NoSuchElementException) as e: log_step(f"Element error sending: {e}", level="error"); raise RuntimeError(f"Send fail (element): {e}") from e
    except Exception as e:
        log_step(f"General error sending: {e}", level="error")
        try: ts=datetime.now().strftime(TIMESTAMP_FORMAT); p=log_file_path.parent/f"error_screenshot_{ts}.png"; driver.save_screenshot(str(p)); log_step(f"Screenshot: {p}",level="info")
        except Exception as scr_e: log_step(f"Screenshot fail: {scr_e}", level="warning")
        raise RuntimeError(f"Send fail: {e}") from e

def wait_for_image_paste(driver, timeout=IMAGE_PASTE_WAIT_TIMEOUT):
    log_step("Waiting image paste..."); start = time.time()
    sel = "button[data-testid='send-button']"; started = False
    while time.time() - start < timeout:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel); enabled = btn.is_enabled()
            if not enabled:
                if not started: log_step(" Send disabled, upload started...", level="debug"); started = True
            elif enabled and started: log_step(" Send enabled, paste OK.", level="success"); time.sleep(0.5); return True
            elif enabled and not started and (time.time() - start > 5): log_step(" Send stayed enabled, proceed cautiously.", level="warning"); return True
        except: pass
        time.sleep(IMAGE_PASTE_CHECK_INTERVAL)
    log_step("Timeout waiting paste.", level="error"); return False

def get_last_response_text(driver):
    log_step("Retrieving last response...", level="debug")
    try:
        blocks = driver.find_elements(By.CSS_SELECTOR, "div[data-message-author-role='assistant']")
        if not blocks: log_step("No assistant blocks found.", level="warning"); return ""
        text = blocks[-1].text.strip()
        if text: log_step(f"Response text OK (len {len(text)}).", level="success"); return text
        else: log_step("Last block text empty.", level="warning"); return ""
    except Exception as e: log_step(f"Error getting response text: {e}", level="error"); return ""

# --- JSON Parsing Helpers ---
def strip_markdown_fences(text):
    if not isinstance(text, str) or not text: return ""
    pattern = r"```(?:[a-zA-Z0-9]*\n)?(.*?)```"; stripped = re.sub(pattern, r"\1", text, flags=re.DOTALL | re.IGNORECASE | re.MULTILINE)
    lines = stripped.split('\n'); cleaned = [line for line in lines if line.strip() != '```']
    return '\n'.join(cleaned).strip()

def try_parse_json(response_text):
    if not response_text: log_step("Parse fail: Response empty.", level="warning"); return (False, "Empty response")
    try: return (True, json.loads(response_text))
    except json.JSONDecodeError: pass
    clean = strip_markdown_fences(response_text)
    if not clean: log_step("Parse fail: Empty after cleaning.", level="warning"); return (False, "Empty after cleaning")
    try: return (True, json.loads(clean))
    except json.JSONDecodeError as e:
        log_step(f"Parse fail after cleaning: {e}", level="warning")
        if '{' in clean and '}' in clean:
            try: start = clean.index('{'); end = clean.rindex('}') + 1; return (True, json.loads(clean[start:end]))
            except (ValueError, json.JSONDecodeError) as sub_e: log_step(f"Substring parse failed: {sub_e}", level="error"); return (False, f"Substring fail: {sub_e}")
        return (False, f"Clean parse error: {e}")

# --- Updated JSON Request Function ---
def request_json_data(driver, prompt, max_retries=CHATGPT_JSON_RETRIES):
    log_step(f"Requesting JSON ('positive_prompt' only)...")
    send_text_message(driver, prompt) # Send initial request

    for attempt in range(max_retries + 1):
        log_step(f"Parse Attempt {attempt + 1}/{max_retries + 1}...")
        response_text = get_last_response_text(driver)
        if not response_text:
            if attempt < max_retries: log_step("Empty response. Retrying.", level="warning"); retry_prompt = "No response. Provide JSON: `{\"positive_prompt\": \"...\"}` with SIGNIFICANT MOVEMENT per instructions."; send_text_message(driver, retry_prompt); continue
            else: log_step("Empty response final.", level="error"); break
        success, data = try_parse_json(response_text)
        if success:
            if isinstance(data, dict) and "positive_prompt" in data and isinstance(data["positive_prompt"], str) and data["positive_prompt"].strip():
                log_step("Valid JSON structure OK.", level="success"); return data
            else: data = "Invalid structure/value (positive_prompt)"
            success = False
        if not success:
            log_step(f"JSON parse/validation fail: {data}", level="warning")
            if attempt < max_retries:
                log_step("Sending JSON fix prompt...")
                fix = (f"Invalid JSON ({data}).\nRegen ONLY raw JSON: `{{\"positive_prompt\": \"...\"}}` with SIGNIFICANT MOVEMENT prompt. Follow sys instructions.")
                send_text_message(driver, fix)
            else: log_step(f"Max retries reached.", level="error"); log_step(f"Final failed response:\n---\n{response_text}\n---", level="error"); break
    log_step("Failed get valid JSON.", level="error", important=True); return None


# --- Main Prompt Generation Logic ---
def run_video_prompt_generation(project_path: Path):
    log_step(f"--- Step 1: Setup & Validation ---", important=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", level="error"); return False
    selected_theme = extract_theme_from_project_path(project_path); log_step(f"Using Theme: '{selected_theme}'")
    approved_images_dir = project_path / APPROVED_IMAGES_FOLDER_NAME
    if not approved_images_dir.is_dir(): log_step(f"Approved images folder not found: {approved_images_dir}", level="error"); return False
    approved_image_paths = sorted( list(approved_images_dir.glob('*.[jJ][pP][gG]')) + list(approved_images_dir.glob('*.[jJ][pP][eE][gG]')) + list(approved_images_dir.glob('*.[pP][nN][gG]')) )
    if not approved_image_paths: log_step(f"No approved images found in {approved_images_dir}", level="error"); return False
    total_images = len(approved_image_paths)
    log_step(f"Found {total_images} approved images.")
    movie_name = project_path.name.split('_')[0]; log_step(f"Extracted Movie Name: '{movie_name}'")

    # --- Step 2: Initialize Selenium & Login ---
    driver = None
    try:
        log_step("Initializing WebDriver...")
        options = uc.ChromeOptions(); options.add_argument("--start-maximized"); driver = uc.Chrome(options=options)
        log_step("WebDriver OK.", level="success")
        log_step(f"Navigating base: {CHATGPT_BASE_URL}..."); driver.get(CHATGPT_BASE_URL); time.sleep(4)
        log_step("Loading cookies..."); cookies_loaded = load_cookies(driver, COOKIE_PATH)
        if not cookies_loaded:
            log_step("Manual login required.", important=True, level="warning")
            print(f"\n>>> Log in to ChatGPT ({CHATGPT_BASE_URL}) manually ({CHATGPT_LOGIN_TIMEOUT}s). <<<"); input(f">>> Press Enter ONLY after logged in. <<<")
            log_step("User logged in. Saving cookies..."); save_cookies(driver, COOKIE_PATH)
            log_step(f"Navigating target: {CHATGPT_TARGET_MODEL_URL}"); driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(3)
        else:
            log_step("Cookies loaded.", level="success")
            current_url = driver.current_url
            if "chatgpt.com" not in current_url or "model=gpt-4o" not in current_url:
                 log_step(f"Not target URL. Navigating {CHATGPT_TARGET_MODEL_URL}"); driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(3)
        wait_for_input_box(driver, timeout=60); log_step("ChatGPT ready.", level="success")

        # --- Send REVISED System Prompt (v3.4 - Force SIGNIFICANT Movement, NO NAMES) ---
        system_prompt_text = f"""You are an AI assistant generating CONCISE video prompts for text-to-video (like Wan2.1).
Goal: DYNAMIC 5-SECOND video clips for a movie trailer. Theme: '{selected_theme}'.

**CRITICAL INSTRUCTION:** The video MUST feature **clear, SIGNIFICANT physical character movement**. Do NOT describe static poses (standing, posing, glancing). **Invent significant movement** (walking briskly, running, jumping, spinning <360, dodging, attacking, using objects actively, strong gestures). Use strong action verbs ONLY for the main action. The image gives visual context for the subject and scene, but the ACTION in the prompt must be **dynamic movement**.

**Subject Description:** Use generic terms like 'man', 'woman', 'person' followed by 1-2 key visual details from the image (e.g., 'woman in red dress', 'man with robotic arm'). **DO NOT use character names (like Basanti, Samba) or actor names.**

For EACH image I paste (I provide context like movie name, character/actor for MY reference only):
1. Analyze image: Identify subject (man/woman + key visuals), setting, potential action.
2. Determine the **CORE SIGNIFICANT DYNAMIC ACTION** (physical movement) for the subject. *No static verbs allowed here.*
3. Generate ONLY a concise POSITIVE prompt: Subject (generic desc + visuals), CORE DYNAMIC ACTION, Scene (brief), Style.
4. Provide output STRICTLY as raw JSON: `{{"positive_prompt": "..."}}`. NO other text, NO negative prompt.

**Positive Prompt Example:** `Subject (woman with high ponytail and colorful outfit), Action (running excitedly across the street, waving), Scene (futuristic bazaar), Style (cinematic, {selected_theme} style)`

Acknowledge with: "Understood. Ready for image. I will generate prompts with SIGNIFICANT character MOVEMENT using generic descriptions (man/woman) and provide only the positive prompt JSON." """

        log_step("Sending System Prompt (v3.4 - Strong Movement, Generic Subject)...")
        send_text_message(driver, system_prompt_text)
        log_step("System Prompt sent. Assuming acknowledged.", level="success")
        time.sleep(2)

    # ** CORRECTED try/except syntax **
    except (WebDriverException, TimeoutException, RuntimeError) as e:
        log_step(f"FATAL: WebDriver/Setup Error: {e}", level="error", important=True)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False
    except Exception as e:
        log_step(f"FATAL: Unexpected Setup Error: {e}", level="error", important=True)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False

    # --- Step 3: Process Each Approved Image ---
    log_step(f"--- Step 3: Processing {total_images} Images ---", important=True)
    success_count = 0; fail_count = 0; processed_count = 0; skipped_count = 0

    for image_path in approved_image_paths:
        processed_count += 1
        images_left = total_images - processed_count

        log_step(f"--- Processing Image {processed_count}/{total_images} ({images_left} left): {image_path.name} ---", important=True)

        # Extract Context (Still needed for folder structure, but not for prompt subject)
        filename_stem = image_path.stem; parts = filename_stem.split("__")
        if len(parts) < 2: log_step(f" Skip - Bad filename: {filename_stem}", level="warning"); skipped_count += 1; continue # Fail count not incremented for skip
        char_name_san = sanitize_name(parts[0]) if parts[0] else "UnknownChar"; actor_name_san = sanitize_name(parts[1]) if len(parts) > 1 and parts[1] else "UnknownActor"
        # display_char_name = char_name_san.replace('_', ' '); display_actor_name = actor_name_san.replace('_', ' ') # Not needed for prompt anymore
        log_step(f" Context for file naming: Character='{char_name_san}', Actor='{actor_name_san}'") # Log for context

        # Define Output Path & Check Existence
        character_folder_path = project_path / CHARACTERS_FOLDER_NAME / actor_name_san # Use actor name for folder
        character_folder_path.mkdir(parents=True, exist_ok=True)
        prompt_output_filename = filename_stem + VIDEO_PROMPT_OUTPUT_SUFFIX
        prompt_output_path = character_folder_path / prompt_output_filename
        if prompt_output_path.exists(): log_step(f" Skip - Output exists: {prompt_output_path.name}", level="warning"); skipped_count += 1; continue

        current_image_failed = False

        # Copy Image
        if not copy_image_to_clipboard(image_path): fail_count += 1; current_image_failed = True;

        # Paste and Wait
        if not current_image_failed:
            try:
                input_box = wait_for_input_box(driver); log_step(" Clearing input...")
                try: driver.execute_script("arguments[0].value = ''; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", input_box); time.sleep(0.2)
                except Exception:
                     try: input_box.click(); time.sleep(0.1); key = Keys.COMMAND if platform.system() == "Darwin" else Keys.CONTROL; input_box.send_keys(key, 'a'); time.sleep(0.1); input_box.send_keys(Keys.DELETE); time.sleep(0.2)
                     except Exception as cl_e: log_step(f" Warn: Clear fail: {cl_e}", level="warning")
                time.sleep(0.3); log_step(" Pasting image...")
                paste_key = Keys.COMMAND if platform.system() == "Darwin" else Keys.CONTROL
                input_box.send_keys(paste_key, 'v'); upload_ok = wait_for_image_paste(driver)
                if not upload_ok: log_step(f" Paste/upload fail/timeout.", level="error"); fail_count += 1; current_image_failed = True;
            except Exception as paste_err: log_step(f" Error paste/wait: {paste_err}", level="error"); fail_count += 1; current_image_failed = True;

        # Get Simple Positive Prompt from ChatGPT
        if not current_image_failed:
            try:
                # REVISED per-image prompt (v3.4)
                per_image_prompt_text = f"""Image pasted. Analyze it. Generate the JSON (`{{"positive_prompt": "..."}}`) for a 9-second trailer clip. Ensure the positive prompt uses a generic subject (man/woman + key visual detail) and describes **SIGNIFICANT physical character MOVEMENT**. Strictly follow all system instructions (NO names, NO static verbs)."""

                log_step("Sending Per-Image Prompt (Requesting simple positive prompt with SIGNIFICANT MOVEMENT)...")
                prompt_data = request_json_data(driver, per_image_prompt_text)

                # Combine with Fixed Negative and Save
                if prompt_data:
                    final_prompt_dict = {
                        "positive_prompt": prompt_data["positive_prompt"],
                        "negative_prompt": FIXED_NEGATIVE_PROMPT
                    }
                    try:
                        with open(prompt_output_path, 'w', encoding='utf-8') as f: json.dump(final_prompt_dict, f, indent=2, ensure_ascii=False)
                        log_step(f" OK - Saved prompts: {prompt_output_path.name}", level="success"); success_count += 1
                    except IOError as e: log_step(f" FAIL - Save prompts file: {e}", level="error"); fail_count += 1
                    except Exception as e: log_step(f" FAIL - Unexpected save error: {e}", level="error"); fail_count += 1
                else: log_step(f" FAIL - No valid prompt JSON received for {image_path.name}.", level="error"); fail_count += 1

            except RuntimeError as e: log_step(f" FAIL - Prompting runtime error: {e}", level="error"); fail_count += 1; log_step("Attempting continue...", level="warning")
            except Exception as prompt_err:
                log_step(f" FAIL - Unexpected prompting error: {prompt_err}", level="error"); fail_count += 1
                try: ts = datetime.now().strftime(TIMESTAMP_FORMAT); p = log_file_path.parent / f"prompt_error_{ts}_{image_path.stem}.png"; driver.save_screenshot(str(p)); log_step(f"Screenshot: {p}",level="info")
                except Exception as scr_e: log_step(f"Screenshot fail: {scr_e}", level="warning")

        time.sleep(random.uniform(2.5, 4.5)) # Random delay

    # --- Step 4: Cleanup and Summary ---
    log_step(f"--- Step 4: Cleanup & Summary ---", important=True)
    if driver:
        log_step("Closing WebDriver...");
        try: driver.quit(); log_step("WebDriver closed.", level="success")
        except Exception as e: log_step(f"Error closing WebDriver: {e}", level="warning")
    log_step(f"Prompt Generation Final Summary:")
    log_step(f"- Total Approved Images Found: {total_images}")
    log_step(f"- Images Attempted Processing: {processed_count - skipped_count}")
    log_step(f"- Prompts Successfully Saved: {success_count}")
    log_step(f"- Failures During Processing: {fail_count}")
    log_step(f"- Images Skipped (Exists/Bad Name): {skipped_count}")
    processing_successful = fail_count == 0
    return processing_successful

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser( description="Generate simple POSITIVE video prompts (forcing SIGNIFICANT MOVEMENT, no names) via ChatGPT and combine with FIXED negative prompt.", formatter_class=argparse.ArgumentDefaultsHelpFormatter )
    parser.add_argument( "-p", "--project_path", help="Path to project folder. Omit for latest.", type=str, default=None )
    args = parser.parse_args()
    log_step("===== Generate Video Prompts Script Started (v3.4 - Strong Movement, No Names) =====", important=True)
    try:
        if DOTENV_PATH.exists(): load_dotenv(dotenv_path=DOTENV_PATH); log_step("Loaded .env.", level="info")
        else: log_step("No .env found.", level="debug")
    except Exception as e: log_step(f"Note: .env load error: {e}", level="debug")

    project_to_process = None
    if args.project_path:
        log_step(f"Project path from arg: {args.project_path}"); project_to_process = Path(args.project_path)
        if not project_to_process.is_dir(): log_step(f"Provided path invalid: {project_to_process}", level="error", important=True); sys.exit(1)
        # ** CORRECTED try/except syntax **
        try:
            base_dir_abs = PROJECTS_BASE_DIR.resolve()
            proj_parent_abs = Path(project_to_process).parent.resolve()
            if not proj_parent_abs == base_dir_abs:
                 log_step(f"Warn: Path parent '{proj_parent_abs}' != base dir '{base_dir_abs}'.", level="warning")
        except Exception as path_err:
             log_step(f"Warn: Path check failed: {path_err}", level="warning")

    if not project_to_process or not project_to_process.is_dir():
        log_step(f"Finding latest project in '{PROJECTS_BASE_DIR}'..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found in '{PROJECTS_BASE_DIR}'. Exiting.", level="error", important=True); sys.exit(1)

    log_step(f"Processing Project: {project_to_process.name}")
    log_step(f"Full Project Path: {project_to_process.resolve()}")
    success = run_video_prompt_generation(project_to_process)

    if success: log_step("Script finished successfully (No processing errors).", level="success", important=True); sys.exit(0)
    else: log_step("Script finished with processing errors.", level="error", important=True); sys.exit(1)