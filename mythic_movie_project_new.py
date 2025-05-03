# -*- coding: utf-8 -*-
# mythic_movie_project.py - MODIFIED FOR CHARACTER PROMPTS FIRST

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException
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

# --- Load Configuration ---
try:
    # Assuming you have config_mythic_v1.py as created before
    import config_mythic_v1 as config
except ImportError:
    print("ERROR: config_mythic_v1.py not found. Please create it.")
    sys.exit(1)

# --- Use Constants from Config ---
TIMESTAMP_FORMAT = config.TIMESTAMP_FORMAT
PROJECTS_BASE_DIR = config.PROJECTS_BASE_DIR
PROJECT_PREFIX = config.PROJECT_PREFIX
COOKIE_PATH = config.COOKIE_PATH
DOTENV_PATH = config.DOTENV_PATH
LOG_DIR = config.LOG_DIR

# --- Output Filenames from Config ---
STORY_FILENAME = config.STORY_FILENAME
CHARACTERS_FILENAME = config.CHARACTERS_FILENAME
SCENES_FILENAME = config.SCENES_FILENAME # Still defined, but generation commented out
IMAGE_PROMPTS_FILENAME = config.IMAGE_PROMPTS_FILENAME # Still defined, but generation commented out
# --- NEW: Filename for Character-Specific Prompts ---
CHARACTER_IMAGE_PROMPTS_FILENAME = "character_image_prompts.json"

# --- ChatGPT Settings from Config ---
CHATGPT_BASE_URL = config.CHATGPT_BASE_URL
CHATGPT_TARGET_MODEL_URL = config.CHATGPT_TARGET_MODEL_URL
CHATGPT_LOGIN_TIMEOUT = config.CHATGPT_LOGIN_TIMEOUT
CHATGPT_RESPONSE_TIMEOUT = config.CHATGPT_RESPONSE_TIMEOUT
CHATGPT_JSON_RETRIES = config.CHATGPT_JSON_RETRIES
MAX_STORY_ATTEMPTS = config.MAX_STORY_ATTEMPTS

# --- Load Environment Variables ---
if DOTENV_PATH.is_file():
    load_dotenv(dotenv_path=DOTENV_PATH)
    print(f"[*] Loaded environment variables from {DOTENV_PATH}")
else:
    print(f"[!] .env not found at {DOTENV_PATH}. Continuing without it.")

# --- Logging Setup ---
LOG_DIR.mkdir(parents=True, exist_ok=True)
current_log_filename = f"{config.LOG_FILE_BASENAME}_story_char_prompts_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"
CURRENT_LOG_FILE = LOG_DIR / current_log_filename

logging.basicConfig(
    level=config.LOG_LEVEL,
    format=config.LOG_FORMAT,
    handlers=[
        logging.FileHandler(CURRENT_LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
# Suppress noisy logs
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('selenium').setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# --- Helper: log_step ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    prefix = prefix_map.get(level, "[?]")
    final = f"{prefix} {message}"
    if important:
        final = f"--- {final} ---"
    getattr(log, level, log.info)(final)

# --- Helpers ---
def sanitize_name(name):
    # Keep implementation from previous version
    s = re.sub(r'[<>:"/\\|?*\']', '_', str(name))
    s = ''.join(c for c in s if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    s = re.sub(r'[_]+', '_', s).strip('_-')
    return s or 'invalid_name'

def sanitize_for_chromedriver(text):
    # Keep implementation from previous version
    return re.sub(r"[^\u0000-\uffff]", "", text) if isinstance(text, str) else text

# --- Cookie Management ---
def save_cookies(driver, path):
    # Keep implementation from previous version
    log_step(f"Saving cookies to: {path}")
    try:
        cookies = driver.get_cookies()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, indent=2)
        log_step("Cookies saved", level="success")
    except Exception as e:
        log_step(f"Error saving cookies: {e}", level="error")

def load_cookies(driver, path):
    if not path.exists():
        log_step(f"Cookie file not found at {path}", level="info")
        return False

    log_step(f"Loading cookies from: {path}")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
    except Exception as e:
        log_step(f"Error reading cookie file {path}: {e}", level="error")
        return False

    # --- Get Primary Domain and Navigate ---
    # Find the most likely primary domain (prefer chatgpt.com)
    primary_domain = None
    for c in cookies:
        dom = c.get('domain', '').lstrip('.')
        if 'chatgpt.com' in dom:
            primary_domain = 'chatgpt.com'
            break
        elif 'openai.com' in dom and not primary_domain:
            primary_domain = 'openai.com' # Fallback

    if not primary_domain:
        log_step("Could not determine primary domain from cookies. Cannot load.", level="warning")
        # Optionally try navigating to a default like chatgpt.com anyway
        # primary_domain = 'chatgpt.com'
        return False # Or handle differently

    target_url = f"https://{primary_domain}"
    log_step(f"Navigating to primary domain: {target_url} before adding cookies...")
    try:
        driver.get(target_url)
        # Wait a bit for the domain context to be set properly
        time.sleep(3)
        # Optional: Verify current URL if needed
        # current_domain = urlparse(driver.current_url).netloc
        # log_step(f"Browser currently on domain: {current_domain}", level="debug")
    except WebDriverException as e:
        log_step(f"Warning: Could not load domain {target_url}. Error: {e}", level="warning")
        # Proceeding to add cookies anyway, might fail more often

    # --- Add Cookies, Catching Domain Errors Gracefully ---
    added_count = 0
    skipped_count = 0
    error_count = 0

    for c in cookies:
        # Basic validation
        if not isinstance(c, dict) or 'name' not in c or 'value' not in c:
            skipped_count += 1
            continue

        # Clean up common issues
        if 'sameSite' in c and c['sameSite'] not in ['Lax', 'Strict', 'None']:
            log_step(f"Removing invalid sameSite value '{c['sameSite']}' for cookie '{c['name']}'", level="debug")
            del c['sameSite']
        if 'expiry' in c and isinstance(c['expiry'], float):
            c['expiry'] = int(c['expiry'])

        # Ensure domain field exists (sometimes missing in exports)
        if 'domain' not in c or not c['domain']:
             log_step(f"Skipping cookie '{c['name']}' because it's missing a domain.", level="debug")
             skipped_count += 1
             continue

        # Attempt to add the cookie
        try:
            # Selenium's add_cookie should handle standard domain matching (e.g., allow .example.com on www.example.com)
            driver.add_cookie(c)
            added_count += 1
            # log_step(f"Added cookie: {c.get('name')} for domain {c.get('domain')}", level="debug")
        except WebDriverException as e:
            # Check if it's the expected domain mismatch error
            if "invalid cookie domain" in str(e) or "Cookie 'domain' mismatch" in str(e):
                # This is expected for cookies not matching the *current* browser domain
                log_step(f"Skipping cookie '{c.get('name')}' due to domain mismatch (Expected for some cookies). Domain: {c.get('domain')}", level="debug")
                skipped_count += 1
            else:
                # Log other errors more seriously
                log_step(f"Warning: Could not add cookie '{c.get('name')}' for domain {c.get('domain')}. Error: {e}", level="warning")
                error_count += 1
        except Exception as e: # Catch any other unexpected errors
            log_step(f"ERROR adding cookie '{c.get('name')}': {e}", level="error")
            error_count += 1


    log_step(f"Cookie loading finished. Added: {added_count}, Skipped (domain mismatch/invalid): {skipped_count}, Errors: {error_count}", level="success" if added_count > 0 else "warning")

    # Return True if we managed to add at least *some* cookies, False otherwise
    # A more robust check might be needed if specific essential cookies failed.
    return added_count > 0

# --- Selenium Helpers ---
def wait_for_input_box(driver, timeout=90):
    # Keep implementation from previous version (with robustness checks)
    effective_timeout = timeout
    log_step(f"Waiting for ChatGPT input box (timeout: {effective_timeout}s)...")
    selectors = [
        "textarea#prompt-textarea", "textarea[data-testid='prompt-textarea']",
        "textarea[placeholder*='Message']", "textarea[placeholder*='Send a message']",
        "div[contenteditable='true']", "textarea[data-id='root']"
    ]
    deadline = time.time() + effective_timeout
    last_exc = None
    while time.time() < deadline:
        for sel in selectors:
            try:
                el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                log_step(f"Found input box with selector: {sel}", level="success")
                return el
            except TimeoutException:
                continue
            except Exception as e:
                last_exc = e
        time.sleep(0.5)
    log_step(f"Input box not found after {effective_timeout}s. Last error: {last_exc}", level="error")
    raise TimeoutException(f"ChatGPT input box not found after {effective_timeout} seconds.")

def wait_for_response_completion(driver, timeout=config.CHATGPT_RESPONSE_TIMEOUT):
    # Keep implementation from previous version (with robustness checks)
    log_step(f"Waiting for response completion (timeout: {timeout}s)...")
    start_time = time.time()
    seen_generating_button = False
    stop_button_selector = "button[data-testid='stop-button'],button[aria-label*='Stop generating']"

    while time.time() - start_time < timeout:
        try:
            stop_buttons = driver.find_elements(By.CSS_SELECTOR, stop_button_selector)
            is_generating = any(button.is_displayed() and button.is_enabled() for button in stop_buttons)

            if is_generating:
                seen_generating_button = True
                time.sleep(0.5)
                continue
            else:
                if seen_generating_button:
                    log_step("Response generation appears complete.", level="success")
                    time.sleep(0.5)
                    return
                elif time.time() - start_time > 7: # Slightly longer grace period
                     log_step("No generating button seen recently, assuming completion.", level="info")
                     return

        except StaleElementReferenceException:
            log_step("Stale element encountered, retrying find...", level="debug")
            time.sleep(0.2)
            continue
        except NoSuchElementException:
             log_step("Stop button not found, assuming completion or change.", level="debug")
             if seen_generating_button: return
             time.sleep(0.5)
             if time.time() - start_time > 7: return

        time.sleep(0.3)

    log_step(f"Timeout after {timeout}s waiting for response completion.", level="error")
    raise TimeoutException(f"Response did not complete within {timeout} seconds.")

def send_message(driver, message):
    # Keep implementation from previous version (with robustness checks)
    ib = wait_for_input_box(driver)
    txt = sanitize_for_chromedriver(message)
    log_step(f"Sending message: {txt[:100]}{'...' if len(txt)>100 else ''}")
    try:
        driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", ib, txt)
    except Exception as e1:
        log_step(f"JS send failed ({e1}), trying Selenium clear/send...", level="warning")
        try:
            ib.clear()
            ib.send_keys(txt)
        except Exception as e2:
            log_step(f"Selenium clear/send also failed ({e2}). Cannot send message.", level="error")
            return

    time.sleep(0.2)
    send_button_selectors = [
        "button[data-testid='send-button']", "button:has(svg[data-icon='send'])",
        "button[aria-label*='Send message']", "button[class*='send']"
    ]
    send_button_clicked = False
    for sel in send_button_selectors:
        try:
            btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
            try: btn.click()
            except Exception: driver.execute_script("arguments[0].click();", btn)
            log_step(f"Clicked send button using selector: {sel}", level="debug")
            send_button_clicked = True
            break
        except TimeoutException: continue
        except Exception as e: log_step(f"Error clicking send button ({sel}): {e}", level="warning")

    if not send_button_clicked:
        log_step("Could not click standard send button, falling back to ENTER key.", level="warning")
        try: ib.send_keys(Keys.ENTER)
        except Exception as e: log_step(f"Failed to send ENTER key: {e}", level="error")

    wait_for_response_completion(driver)

def get_last_response_text(driver):
    # Keep implementation from previous version (with robustness checks)
    log_step("Retrieving last response text...")
    response_containers = "div[data-message-author-role='assistant']"
    text_selectors = ['.markdown', '.prose', 'div[data-message-id]'] # Common text containers

    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, response_containers)))
        all_assistant_blocks = driver.find_elements(By.CSS_SELECTOR, response_containers)
        if not all_assistant_blocks:
            log_step("No assistant message blocks found.", level="warning"); return ""
        last_block = all_assistant_blocks[-1]
        extracted_text = ""
        for sel in text_selectors:
            try:
                text_elements = last_block.find_elements(By.CSS_SELECTOR, sel)
                if text_elements:
                    current_text = "\n".join(elem.text.strip() for elem in text_elements if elem.text and elem.text.strip()).strip()
                    if len(current_text) > len(extracted_text): extracted_text = current_text
            except (NoSuchElementException, StaleElementReferenceException): continue

        if not extracted_text: extracted_text = last_block.text.strip()
        if extracted_text: log_step(f"Retrieved response text (length {len(extracted_text)}).", level="debug")
        else: log_step("Could not extract text from the last assistant block.", level="warning")
        return extracted_text
    except TimeoutException:
        log_step("Timed out waiting for assistant response block.", level="warning"); return ""
    except Exception as e:
        log_step(f"Error retrieving last response text: {e}", level="error"); log.error(traceback.format_exc()); return ""

# --- JSON Helpers ---
def strip_markdown_fences(t):
    # Keep implementation from previous version
    return re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", t)

def try_parse_json(resp_text):
    # Keep implementation from previous version
    if not resp_text: return False, "Response text is empty"
    try: return True, json.loads(resp_text)
    except json.JSONDecodeError as e: parse_error_msg = str(e)
    cleaned_text = strip_markdown_fences(resp_text)
    if cleaned_text != resp_text:
        try: return True, json.loads(cleaned_text)
        except json.JSONDecodeError: pass
    cleaned_text = strip_markdown_fences(resp_text)
    json_starts = ['{', '[']; json_ends = {'{': '}', '[': ']'}
    for start_char in json_starts:
        start_index = cleaned_text.find(start_char)
        if start_index != -1:
            end_char = json_ends[start_char]; nesting_level = 0; in_string = False; escaped = False; end_index = -1
            for i, char in enumerate(cleaned_text[start_index:], start=start_index):
                if char == '"' and not escaped: in_string = not in_string
                elif char == '\\' and in_string: escaped = True; continue
                if not in_string:
                    if char == start_char: nesting_level += 1
                    elif char == end_char:
                        nesting_level -= 1
                        if nesting_level == 0: end_index = i + 1; break
                escaped = False
            if end_index != -1:
                potential_json = cleaned_text[start_index:end_index]
                try: return True, json.loads(potential_json)
                except json.JSONDecodeError: continue
    return False, f"JSON parsing failed. Initial error: {parse_error_msg}"

def request_json_data(driver, prompt):
    # Keep implementation from previous version
    base_prompt = prompt
    current_prompt = base_prompt + "\nIMPORTANT: Respond ONLY with the raw JSON object (no commentary, no markdown fences)."
    for attempt in range(config.CHATGPT_JSON_RETRIES + 1):
        log_step(f"Requesting JSON data (Attempt {attempt + 1}/{config.CHATGPT_JSON_RETRIES + 1})")
        send_message(driver, current_prompt)
        response_text = get_last_response_text(driver)
        is_valid, result = try_parse_json(response_text)
        if is_valid:
            log_step("Successfully parsed JSON response.", level="success"); return result
        else:
            error_reason = result
            log_step(f"JSON parsing failed: {error_reason}", level="warning")
            current_prompt = (f"Previous response invalid JSON. Reason: {error_reason}. "
                             f"Regenerate based on original request. Output ONLY raw JSON.\n---\n{base_prompt}\n---")
            time.sleep(1)
    log_step("Failed to get valid JSON data after multiple retries.", level="error"); return None


# --- Main ---
def main():
    start_time = time.time()
    driver = None
    project_path = None
    try:
        log_step("===== Started Mythic Movie Generator (Character Prompts Focus) =====", important=True)
        topic = input("Enter mythic story topic: ").strip()
        while not topic:
            topic = input("Topic cannot be empty. Please enter a topic: ").strip()

        ts = datetime.now().strftime(TIMESTAMP_FORMAT)
        sanitized_topic = sanitize_name(topic)
        project_folder_name = f"{config.PROJECT_PREFIX}_{sanitized_topic}_{ts}"
        project_path = config.PROJECTS_BASE_DIR / project_folder_name
        log_step(f"Creating project directory: {project_path}")
        project_path.mkdir(parents=True, exist_ok=True)

        log_step("Initializing WebDriver...")
        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        try:
            driver = uc.Chrome(options=options)
            log_step("WebDriver initialized.", level="success")
        except WebDriverException as e:
             log_step(f"Failed to initialize WebDriver: {e}", level="error"); log.error(traceback.format_exc()); return

        driver.get(config.CHATGPT_BASE_URL); log_step(f"Navigated to {config.CHATGPT_BASE_URL}"); time.sleep(4)
        cookies_loaded = load_cookies(driver, config.COOKIE_PATH)
        if cookies_loaded:
            driver.get(config.CHATGPT_TARGET_MODEL_URL); log_step(f"Navigated to target model URL: {config.CHATGPT_TARGET_MODEL_URL}"); time.sleep(5)
        else: log_step("Cookies not loaded. Manual login may be required.", level="info")

        try:
            wait_for_input_box(driver, timeout=60); log_step("ChatGPT interface ready.", level="success")
            if cookies_loaded: save_cookies(driver, config.COOKIE_PATH)
        except TimeoutException:
            log_step(f"Interface not ready. Please log in manually within {config.CHATGPT_LOGIN_TIMEOUT}s...", level="warning")
            input(f"Press Enter after you have successfully logged in...")
            log_step("Assuming login complete."); save_cookies(driver, config.COOKIE_PATH)
            driver.get(config.CHATGPT_TARGET_MODEL_URL); log_step(f"Navigated to target model URL post-login: {config.CHATGPT_TARGET_MODEL_URL}"); time.sleep(3)

        # --- Start Interaction ---
        system_prompt = (
            "MODE:COMMENTARY\nYou are an AI assistant collaborating to generate assets for a short cinematic mythic story. "
            "Strictly follow MODE instructions (MODE:COMMENTARY for text, MODE:JSON for JSON). "
            "For JSON, output ONLY the raw JSON object without any commentary or markdown fences."
        )
        log_step("Sending initial system prompt..."); send_message(driver, system_prompt)

        # --- Story Generation and Approval ---
        approved_text = None
        attempts = 0
        while not approved_text and attempts < config.MAX_STORY_ATTEMPTS:
            # (Story generation loop remains the same as before)
            attempts += 1
            log_step(f"Requesting story draft (Attempt {attempts}/{config.MAX_STORY_ATTEMPTS})...")
            story_prompt = (
                f"MODE:COMMENTARY\nYou are a master storyteller specializing in mythic narratives. "
                f"Write a compelling and concise cinematic story (approx. 250-300 words, suitable for a ~2 minute visual sequence) "
                f"based on the theme: '{topic}'. Focus on vivid imagery and emotional beats. "
                "Provide ONLY the story text, without any introduction or conclusion statements."
            )
            send_message(driver, story_prompt)
            draft_text = get_last_response_text(driver)

            if not draft_text:
                log_step("Received empty draft, retrying...", level="warning"); time.sleep(2); continue

            print(f"\n===== STORY DRAFT (Attempt {attempts}) =====\n{draft_text}\n======================================")
            ans = ""
            while ans not in ['yes', 'y', 'edit', 'e', 'reject', 'r']:
                 ans = input("Approve this draft? (yes/edit/reject): ").strip().lower()

            if ans in ('yes','y'):
                approved_text = draft_text; log_step("Story draft approved.", level="success")
                # send_message(driver, "MODE:COMMENTARY\nStory approved.")
            elif ans in ('edit','e'):
                feedback = input("Provide feedback: ").strip()
                while not feedback: feedback = input("Feedback cannot be empty: ").strip()
                log_step(f"Requesting revision based on feedback: '{feedback[:50]}...'")
                revision_prompt = ( f"MODE:COMMENTARY\nRevise previous draft based *only* on: '{feedback}'. "
                                   "Keep cinematic, concise (250-300 words). ONLY revised text.")
                send_message(driver, revision_prompt)
            else: # Reject
                log_step("Story draft rejected. Requesting new story.")
                reject_prompt = ( f"MODE:COMMENTARY\nPrevious draft rejected. "
                                 f"Write *different* story on theme '{topic}' (250-300 words). ONLY new story text.")
                send_message(driver, reject_prompt)

        if not approved_text:
            raise RuntimeError(f"Story approval failed after {config.MAX_STORY_ATTEMPTS} attempts.")

        # --- Save Approved Story ---
        story_file_path = project_path / config.STORY_FILENAME
        try:
            with open(story_file_path, 'w', encoding='utf-8') as f: f.write(approved_text)
            log_step(f"Approved story saved to: {story_file_path}", level="success")
        except IOError as e: log_step(f"Error saving story file: {e}", level="error")

        # --- Character Extraction ---
        log_step("Requesting character extraction...")
        char_prompt = (
             "MODE:JSON\nBased *only* on the approved story text provided previously, identify the key characters. "
             "For each character, provide their 'name' and a brief 'visual_description' suitable for image generation (e.g., appearance, key features, clothing if mentioned). "
             "Format the output as a JSON object like this: \n"
             "{\n"
             "  \"characters\": [\n"
             "    { \"name\": \"CharacterName1\", \"visual_description\": \"Description...\" },\n"
             "    { \"name\": \"CharacterName2\", \"visual_description\": \"Description...\" }\n"
             "  ]\n"
             "}\n"
             "Output ONLY the raw JSON."
        )
        chars_data = request_json_data(driver, char_prompt)

        if not chars_data or not isinstance(chars_data, dict) or 'characters' not in chars_data:
            log_step("Failed to get valid character data. Proceeding without characters.", level="error")
            chars_data = {"characters": []}
        else:
            log_step(f"Successfully extracted {len(chars_data.get('characters',[]))} characters.", level="success")

        chars_file_path = project_path / config.CHARACTERS_FILENAME
        try:
            with open(chars_file_path, 'w', encoding='utf-8') as f: json.dump(chars_data, f, indent=2, ensure_ascii=False)
            log_step(f"Character data saved to: {chars_file_path}", level="success")
        except IOError as e: log_step(f"Error saving characters file: {e}", level="error")

        # --- >>> NEW: Character Image Prompt Generation <<< ---
        log_step("Generating character image prompts...")
        characters = chars_data.get('characters', [])
        character_image_prompts_list = []
        total_characters = len(characters)

        if not characters:
            log_step("No characters found in character data, skipping character prompt generation.", level="warning")
        else:
            for index, character in enumerate(characters):
                char_name = character.get('name')
                char_desc = character.get('visual_description')

                if not char_name or not char_desc:
                    log_step(f"Skipping character {index+1}/{total_characters} due to missing name or description.", level="warning")
                    continue

                log_step(f"Requesting image prompts for Character {index+1}/{total_characters}: {char_name}...")

                # Prompt ChatGPT for medium and full body shots for this character
                char_image_prompt_request = (
                    f"MODE:JSON\nCharacter Name: {char_name}\nVisual Description: {char_desc}\n\n"
                    "Generate two detailed image prompts for an AI image generator (like Midjourney/Stable Diffusion) to create reference images for this character:\n"
                    "1. A 'medium_shot_prompt': A clear portrait from the waist or chest up, focusing on facial features, expression, hair, and upper clothing/armor details, based on the description.\n"
                    "2. A 'full_body_prompt': A clear shot showing the character from head to toe, including stance, full outfit/armor, and any key props mentioned in the description.\n\n"
                    "Emphasize cinematic quality, appropriate lighting, and the mythic theme. "
                    "Format the output as a single JSON object like this:\n"
                    "{\n"
                    f"  \"character_name\": \"{char_name}\",\n"
                    "  \"medium_shot_prompt\": \"Detailed medium shot prompt here...\",\n"
                    "  \"full_body_prompt\": \"Detailed full body prompt here...\"\n"
                    "}\n"
                    "Output ONLY the raw JSON."
                )

                prompt_data = request_json_data(driver, char_image_prompt_request)

                # Validate and store the prompt data
                if (prompt_data and isinstance(prompt_data, dict) and
                        prompt_data.get("character_name") == char_name and
                        prompt_data.get("medium_shot_prompt") and
                        prompt_data.get("full_body_prompt")):
                    character_image_prompts_list.append(prompt_data)
                    log_step(f"Successfully generated prompts for {char_name}.", level="success")
                    log_step(f" Medium Prompt: '{prompt_data['medium_shot_prompt'][:60]}...'", level="debug")
                    log_step(f" Full Body Prompt: '{prompt_data['full_body_prompt'][:60]}...'", level="debug")
                else:
                    log_step(f"Failed to get valid prompts for {char_name}. Using fallback.", level="warning")
                    # Create fallback prompts
                    fallback_medium = f"Cinematic medium portrait of {char_name}, {char_desc}. Mythic theme."
                    fallback_full = f"Cinematic full body shot of {char_name}, {char_desc}, showing full attire. Mythic theme."
                    character_image_prompts_list.append({
                        "character_name": char_name,
                        "medium_shot_prompt": fallback_medium,
                        "full_body_prompt": fallback_full
                    })

                time.sleep(1.5) # Increased delay between character prompt requests

        # Save Character Image Prompts JSON
        char_img_prompts_file_path = project_path / CHARACTER_IMAGE_PROMPTS_FILENAME
        try:
            with open(char_img_prompts_file_path, 'w', encoding='utf-8') as f:
                json.dump(character_image_prompts_list, f, indent=2, ensure_ascii=False)
            log_step(f"Character image prompts saved to: {char_img_prompts_file_path}", level="success")
        except IOError as e:
            log_step(f"Error saving character image prompts file: {e}", level="error")

        # --- <<< END NEW SECTION >>> ---


        # --- Scene Breakdown (COMMENTED OUT) ---
        # log_step("Requesting scene breakdown...")
        # names = [c.get('name') for c in chars_data.get('characters', [])]
        # scene_prompt = (
        #     f"MODE:JSON\nUsing the approved story and characters {names}, break into exactly 24 scenes of ~5s each. "
        #     "Include scene_number, scene_description, focus_character, action." # Simplified request
        # )
        # scenes_data = request_json_data(driver, scene_prompt)
        # if not scenes_data or not isinstance(scenes_data, dict) or 'scenes' not in scenes_data:
        #      log_step("Failed to get valid scene data.", level="error")
        #      scenes_data = {"scenes": []}
        # elif len(scenes_data.get('scenes',[])) != 24:
        #      log_step(f"Warning: Received {len(scenes_data.get('scenes',[]))} scenes, expected 24.", level="warning")
        # else:
        #      log_step("Successfully extracted scene breakdown.", level="success")
        #
        # scenes_file_path = project_path / config.SCENES_FILENAME
        # try:
        #     with open(scenes_file_path, 'w', encoding='utf-8') as f:
        #         json.dump(scenes_data, f, indent=2, ensure_ascii=False)
        #     log_step(f"Scene data saved to: {scenes_file_path}", level="success")
        # except IOError as e:
        #      log_step(f"Error saving scenes file: {e}", level="error")


        # --- Scene-Based Image Prompts (COMMENTED OUT) ---
        # log_step("Generating image prompts for each scene...")
        # scenes = scenes_data.get('scenes', [])
        # img_prompts = []
        # total_scenes = len(scenes)
        # for index, scene in enumerate(scenes):
        #     num = scene.get('scene_number', index + 1)
        #     desc = scene.get('scene_description', 'No description')
        #     action = scene.get('action', 'No action')
        #     focus = scene.get('focus_character', 'None')
        #     log_step(f"Requesting image prompt for Scene {num}/{total_scenes}...")
        #     prompt_req = (
        #         f"MODE:JSON\nScene {num}: Description='{desc}', Action='{action}', Focus='{focus}'. "
        #         "Generate a detailed image prompt for an AI image generator for this scene. "
        #         "Provide only JSON {\"scene_number\": num, \"prompt_text\": string}."
        #     )
        #     data = request_json_data(driver, prompt_req)
        #     if not data or not isinstance(data, dict) or 'prompt_text' not in data:
        #          log_step(f"Failed prompt for Scene {num}. Using fallback.", level="warning")
        #          fallback = f"Cinematic scene: {desc}. Action: {action}. Focus: {focus}. Style: Mythic."
        #          img_prompts.append({"scene_number": num, "prompt_text": fallback})
        #     else:
        #          if data.get("scene_number") != num: data["scene_number"] = num # Correct if needed
        #          img_prompts.append(data)
        #          log_step(f"Generated prompt for Scene {num}: '{data.get('prompt_text', '')[:50]}...'", level="debug")
        #     time.sleep(1)
        #
        # img_prompts_file_path = project_path / config.IMAGE_PROMPTS_FILENAME
        # try:
        #     with open(img_prompts_file_path, 'w', encoding='utf-8') as f:
        #         json.dump(img_prompts, f, indent=2, ensure_ascii=False)
        #     log_step(f"Image prompts saved to: {img_prompts_file_path}", level="success")
        # except IOError as e:
        #      log_step(f"Error saving image prompts file: {e}", level="error")


        # --- Final ---
        final_message = (f"MODE:COMMENTARY\nAsset generation process partially complete for topic '{topic}'. "
                         f"Story, characters, and *character image prompts* have been generated.")
        send_message(driver, final_message)
        total_time = time.time() - start_time
        log_step(f"Mythic Movie Generator (Character Prompts) finished successfully in {total_time:.2f} seconds.", level="success", important=True)
        log_step(f"Project assets saved in: {project_path}", level="success")

    except Exception as e:
        log_step(f"An unexpected error occurred: {e}", level="error", important=True)
        log.error(traceback.format_exc())
    finally:
        if driver:
            try:
                log_step("Closing WebDriver..."); driver.quit(); log_step("WebDriver closed.")
            except Exception as e: log_step(f"Error closing WebDriver: {e}", level="warning")
        log_step("Exiting script.", level="info")

if __name__ == '__main__':
    main()