# Comprehensive Orchestrator using ChatGPT (Selenium) + Tavily + Web Selection v4
# This script is self-contained and incorporates fixes for directory, filenames, image types, prompting, template errors, and syntax errors.

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
from urllib.parse import urlparse, quote
import logging
import shutil
import threading
from dotenv import load_dotenv

# --- Web Server & Image Processing Imports ---
try:
    from flask import Flask, request, render_template_string, send_from_directory, abort
    import cv2
    # Ensure tensorflow is installed: pip install tensorflow
    from mtcnn.mtcnn import MTCNN # Multi-task Cascaded Convolutional Networks for face detection
    from PIL import Image # Used indirectly by MTCNN? Good to have.
except ImportError as e:
    print(f"ERROR: Missing critical libraries. Please install Flask, opencv-python, mtcnn, Pillow, tensorflow. Details: {e}")
    print("Run: pip install Flask opencv-python mtcnn Pillow tensorflow")
    sys.exit(1)

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
DOTENV_PATH = Path('.env')
LOG_FILE = Path("logs") / f"orchestrator_chatgpt_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"
# --- FIXED BASE DIRECTORY ---
PROJECTS_BASE_DIR = Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects")
# --- END FIXED BASE DIRECTORY ---
CHARACTERS_FOLDER_NAME = "characters"
SOURCE_ACTORS_FOLDER_NAME = "source_actors"
TEMP_IMAGE_FOLDER_NAME = "_temp_images"
FILTERED_IMAGE_FOLDER_NAME = "_filtered_images"
COOKIE_PATH = Path(r"H:\projects\Movie_trailer\movie_vignette_generator\chatgpt_cookies.json") # Fixed path

# --- API Key Names ---
TAVILY_API_KEY_NAME = "TAVILY_API_KEY"

# --- ChatGPT Settings ---
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o"
CHATGPT_LOGIN_TIMEOUT = 120 # seconds to wait for manual login
CHATGPT_RESPONSE_TIMEOUT = 300 # seconds max wait for a response
CHATGPT_JSON_RETRIES = 2 # number of times to ask ChatGPT to fix JSON

# --- Tavily Settings ---
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_SEARCH_COUNT = 25 # How many image results to request
ALLOWED_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png']

# --- Image Filtering Settings ---
FACE_FILTER_REQUIRED_COUNT = 1 # Require exactly 1 face
FACE_CONFIDENCE_THRESHOLD = 0.95 # Minimum confidence for detected face

# --- Web Selection Settings ---
WEB_SELECTION_HOST = "127.0.0.1"
WEB_SELECTION_PORT = 5001
WEB_SELECTION_TIMEOUT = 600 # seconds (10 minutes) to wait for user selection

# --- Global variable for Flask communication ---
web_selection_results = None
web_selection_event = threading.Event()
# --- Initialize MTCNN detector globally (lazy initialization later if needed) ---
face_detector = None

# --- Load Environment Variables ---
try:
    if DOTENV_PATH.is_file():
        load_dotenv(dotenv_path=DOTENV_PATH)
        print(f"[*] Loaded environment variables from: {DOTENV_PATH}")
    else:
        print(f"[!] Warning: .env file not found at {DOTENV_PATH}. API keys must be set as environment variables.")
except Exception as e:
    print(f"[!] Warning: Failed to load .env file: {e}")

TAVILY_API_KEY = os.getenv(TAVILY_API_KEY_NAME)

# --- Logging Setup ---
try:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'),
            logging.StreamHandler(sys.stdout) # Ensure logs go to console
        ]
    )
    # Mute TensorFlow's excessive INFO logs unless needed
    tf_logger = logging.getLogger('tensorflow')
    if tf_logger: tf_logger.setLevel(logging.WARNING)
    # Mute specific noisy loggers if needed
    logging.getLogger('werkzeug').setLevel(logging.WARNING)


except Exception as e:
    print(f"ERROR setting up logging: {e}")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    """Logs messages with different levels and optional highlighting."""
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}"
    if important:
        final_msg = f"--- {final_msg} ---"

    if level == "error":
        log.error(final_msg)
    elif level == "warning":
        log.warning(final_msg)
    elif level == "debug":
         log.debug(final_msg)
    else: # Handles 'info' AND 'success' now
        log.info(final_msg) # Log 'success' messages as INFO level

# --- Helper Function: Sanitize Name ---
def sanitize_name(name):
    """Sanitizes a name for file/folder usage."""
    if not isinstance(name, str):
        name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name)
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized)
    sanitized = re.sub(r'[-]+', '-', sanitized)
    sanitized = sanitized.strip('_-')
    max_len = 60
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].strip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Helper Function: Sanitize for ChromeDriver ---
def sanitize_for_chromedriver(text):
    """Removes characters not supported by ChromeDriver (non-BMP)."""
    if not isinstance(text, str): return text
    return re.sub(r'[^\u0000-\uffff]', '', text)

# --- Cookie Management Functions ---
# (Keep save_cookies and load_cookies functions as previously defined)
def save_cookies(driver, path):
    """Save current browser session cookies to a JSON file."""
    log_step(f"Attempting to save cookies to: {path}")
    try:
        cookies = driver.get_cookies()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        log_step("Cookies saved successfully!", level="success")
    except WebDriverException as e:
        log_step(f"WebDriver error saving cookies (browser might be closed or unreachable): {e}", level="error")
    except Exception as e:
        log_step(f"Error saving cookies to {path}: {e}", level="error")

def load_cookies(driver, path):
    """Load cookies from JSON (if available) and add them to the current browser session."""
    if path.exists():
        log_step(f"Cookie file found at {path}. Attempting to load...")
        try:
            with open(path, "r", encoding="utf-8") as f:
                try:
                    cookies = json.load(f)
                except json.JSONDecodeError:
                    log_step("Error decoding JSON from cookie file. File might be corrupt.", level="error")
                    return False

            if not isinstance(cookies, list):
                log_step("Invalid cookie file format (expected a list).", level="error")
                return False

            target_domain = None
            primary_domains = ['openai.com', 'chatgpt.com']
            for cookie in cookies:
                 domain_in_cookie = cookie.get("domain", "").lstrip('.')
                 if any(d in domain_in_cookie for d in primary_domains):
                      target_domain = f"https://{domain_in_cookie}/"
                      break

            if target_domain:
                 log_step(f"Navigating to primary domain {target_domain} before loading cookies.")
                 try:
                     driver.get(target_domain)
                     time.sleep(2)
                 except Exception as e:
                     log_step(f"Warning: Failed to navigate to domain {target_domain} before cookie load: {e}", level="warning")
            else:
                log_step("No primary domain found in cookies, proceeding without pre-navigation.", level="warning")

            loaded_count = 0
            skipped_count = 0
            for cookie in cookies:
                if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
                    skipped_count += 1; continue
                if 'sameSite' in cookie and cookie['sameSite'] not in ['Lax', 'Strict', 'None']:
                    del cookie['sameSite']
                if 'expiry' in cookie and isinstance(cookie['expiry'], float):
                    cookie['expiry'] = int(cookie['expiry'])
                try:
                    driver.add_cookie(cookie)
                    loaded_count += 1
                except Exception as e:
                    skipped_count += 1
            log_step(f"Cookies loaded: {loaded_count}, Skipped: {skipped_count}", level="success")
            return True
        except Exception as e:
            log_step(f"Error loading cookies from {path}: {e}", level="error")
            return False
    else:
        log_step("Cookie file not found.", level="info")
        return False


# --- Selenium Interaction Helpers ---
# (Keep wait_for_element, wait_for_input_box, wait_for_response_completion, send_message, get_last_response_text as defined previously)
def wait_for_element(driver, by, value, timeout=30, condition=EC.presence_of_element_located):
    """General purpose wait for element."""
    try:
        element = WebDriverWait(driver, timeout).until(
            condition((by, value))
        )
        return element
    except TimeoutException:
        log_step(f"Timeout waiting for element ({by}={value}) after {timeout}s", level="error")
        raise TimeoutException(f"Element ({by}={value}) not found after {timeout}s")
    except Exception as e:
        log_step(f"Error waiting for element ({by}={value}): {e}", level="error")
        raise

def wait_for_input_box(driver, timeout=60):
    """Wait up to `timeout` seconds to find ChatGPT's input box."""
    log_step("Waiting for ChatGPT input box...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            input_box = driver.find_element(By.CSS_SELECTOR, "textarea[data-id='root'], textarea#prompt-textarea")
            if input_box.is_displayed() and input_box.is_enabled():
                log_step("Input box (textarea) found and ready.", level="success")
                return input_box
        except NoSuchElementException: pass
        except Exception as e: log_step(f"Unexpected error searching for textarea: {e}", level="warning")

        try:
            input_box_div = driver.find_element(By.CSS_SELECTOR, "div[contenteditable='true']")
            if input_box_div.is_displayed():
                 log_step("Input box (div contenteditable) found.", level="success")
                 return input_box_div
        except NoSuchElementException: pass
        except Exception as e: log_step(f"Unexpected error searching for contenteditable div: {e}", level="warning")
        time.sleep(1)
    log_step("ChatGPT input box not found after waiting.", level="error")
    raise TimeoutException("[X] ChatGPT input box not found after waiting.")

def wait_for_response_completion(driver, timeout=CHATGPT_RESPONSE_TIMEOUT):
    """Wait for ChatGPT to finish generating its response by monitoring buttons."""
    log_step("Waiting for ChatGPT response completion...")
    start_time = time.time()
    generation_started = False
    send_button_selector = "button[data-testid='send-button']"
    stop_button_selector = "button[data-testid='stop-button']"

    while time.time() - start_time < timeout:
        stop_button_visible = False
        try:
            stop_buttons = driver.find_elements(By.CSS_SELECTOR, stop_button_selector)
            if stop_buttons and stop_buttons[0].is_displayed():
                stop_button_visible = True
                if not generation_started: log_step("Generation in progress (Stop button visible)..."); generation_started = True
            else:
                 if generation_started:
                    log_step("Response generation likely complete (Stop button gone). Waiting briefly...", level="success")
                    time.sleep(2); return True

            if not generation_started:
                 send_buttons = driver.find_elements(By.CSS_SELECTOR, send_button_selector)
                 if send_buttons and send_buttons[0].is_displayed() and send_buttons[0].is_enabled():
                     if time.time() - start_time > 10:
                         log_step("Send button enabled, no stop button seen. Assuming idle state.", level="debug"); return True
                 elif send_buttons and send_buttons[0].is_displayed() and not send_buttons[0].is_enabled():
                      if not generation_started: log_step("Generation potentially starting (Send button disabled)...", level="debug")
        except Exception as e: log_step(f"Error checking response status buttons: {e}", level="warning")

        if stop_button_visible: time.sleep(1)
        else:
            if not generation_started and time.time() - start_time > 15:
                log_step("No generation started after 15s. Checking for response text anyway...", level="warning")
                if get_last_response_text(driver): log_step("Response text found without seeing stop button. Assuming completion.", level="info"); return True
                else: log_step("No response text found either. Assuming ready or error state.", level="warning"); return True
            time.sleep(0.5)

    log_step(f"Timeout ({timeout}s) waiting for response completion.", level="error")
    raise TimeoutException(f"Timeout waiting for ChatGPT response completion after {timeout}s")


def send_message(driver, message):
    """Sends a message to ChatGPT, handles multiline, and waits for completion."""
    try:
        input_box = wait_for_input_box(driver)
        sanitized_message = sanitize_for_chromedriver(message)
        log_step(f"Sending message (first 200 chars):\n{sanitized_message[:200]}..." + (" (truncated)" if len(sanitized_message) > 200 else ""))
        try: driver.execute_script("arguments[0].value = '';", input_box); # log_step("Input box cleared via JS.", level="debug")
        except Exception:
            #log_step("JS clear failed, using Keys to clear input box.", level="debug")
            input_box.click(); time.sleep(0.1); input_box.send_keys(Keys.CONTROL + "a"); time.sleep(0.1); input_box.send_keys(Keys.DELETE); time.sleep(0.2)

        lines = sanitized_message.split('\n')
        for i, line in enumerate(lines):
            input_box.send_keys(line)
            if i < len(lines) - 1: input_box.send_keys(Keys.SHIFT, Keys.ENTER); time.sleep(0.1)
        time.sleep(0.5)

        send_button_selector = "button[data-testid='send-button']"
        try:
            send_button = wait_for_element(driver, By.CSS_SELECTOR, send_button_selector, timeout=10, condition=EC.element_to_be_clickable)
            send_button.click(); # log_step("Send button clicked.")
        except TimeoutException:
             log_step("Send button not found or not clickable, attempting ENTER key.", level="warning")
             try: input_box.send_keys(Keys.ENTER)
             except Exception as enter_err: log_step(f"ENTER key fallback failed: {enter_err}", level="error"); raise RuntimeError("Send button failed and ENTER key failed.") from enter_err

        wait_for_response_completion(driver)
        log_step("Message sent and response generation finished.", level="success")
    except (NoSuchElementException, TimeoutException, WebDriverException) as e: log_step(f"Error sending message: {e}", level="error"); raise RuntimeError(f"Failed to send message to ChatGPT: {e}") from e
    except Exception as e: log_step(f"Unexpected error sending message: {e}", level="error"); raise

def get_last_response_text(driver):
    """Retrieve the text content from the last ChatGPT response block."""
    try:
        response_selectors = [
            "div[data-message-author-role='assistant'] .markdown",
            "div[data-message-author-role='assistant'] > div > div > div",
            ".text-message",
            ".message-content .markdown" ]
        response_elements = []
        for selector in response_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    assistant_elements = []
                    for el in elements:
                        parent_div = None
                        try: parent_div = el.find_element(By.XPATH, "./ancestor-or-self::div[@data-message-author-role='assistant']")
                        except NoSuchElementException:
                            try:
                                role = el.find_element(By.XPATH, "./ancestor-or-self::div[contains(@class, 'group')]").get_attribute('data-message-author-role')
                                if role == 'assistant': assistant_elements.append(el)
                            except: pass
                        if parent_div: assistant_elements.append(el)
                    if assistant_elements: response_elements = assistant_elements; break
            except Exception as find_err: log_step(f" Minor error finding elements with selector '{selector}': {find_err}", level="debug"); continue

        if response_elements:
            last_response_text = response_elements[-1].text.strip()
            if last_response_text: return last_response_text
            else: log_step("Found response element(s), but the last one was empty.", level="warning"); return ""
        else: log_step("Could not find any reliable assistant response text using known selectors.", level="warning"); return ""
    except Exception as e: log_step(f"Error getting last response text: {e}", level="warning"); return ""


# --- JSON Parsing Helpers ---
# (Keep strip_markdown_fences and try_parse_json as defined previously)
def strip_markdown_fences(text):
    """Removes ```...``` fences and optional language identifiers."""
    if not text: return ""
    pattern = r"```(?:[a-zA-Z0-9]*\n)?(.*?)```"
    stripped_text = re.sub(pattern, r"\1", text, flags=re.DOTALL | re.MULTILINE)
    lines = stripped_text.split('\n')
    cleaned_lines = [line for line in lines if line.strip() != '```']
    return '\n'.join(cleaned_lines).strip()

def try_parse_json(response_text):
    """Attempt to parse response_text as JSON after cleaning."""
    if not response_text: log_step("Cannot parse JSON: Received empty response text.", level="warning"); return (False, "Received empty response text")
    #log_step("Attempting to parse JSON from response...", level="debug")
    try: parsed = json.loads(response_text); log_step("JSON parsed successfully directly.", level="success"); return (True, parsed)
    except json.JSONDecodeError: pass #log_step("Direct JSON parsing failed. Cleaning and extracting...", level="debug")

    clean_text = strip_markdown_fences(response_text)
    if not clean_text: log_step("Cannot parse JSON: Response text is empty after cleaning markdown.", level="warning"); return (False, "Response empty after cleaning markdown")

    first_bracket = -1; first_curly = -1
    try: first_bracket = clean_text.index('[')
    except ValueError: pass
    try: first_curly = clean_text.index('{')
    except ValueError: pass

    start_idx = -1
    if first_curly != -1 and (first_curly < first_bracket or first_bracket == -1): start_idx = first_curly; end_char = '}'
    elif first_bracket != -1: start_idx = first_bracket; end_char = ']'
    else: log_step("No starting '{' or '[' found in cleaned text.", level="warning"); return (False, "No JSON object or array start found")

    open_count = 0; end_idx = -1
    for i in range(start_idx, len(clean_text)):
        char = clean_text[i]
        if char == clean_text[start_idx]: open_count += 1
        elif char == end_char:
            open_count -= 1
            if open_count == 0: end_idx = i + 1; break

    if start_idx != -1 and end_idx != -1:
        json_substring = clean_text[start_idx:end_idx].strip()
        #log_step("Extracted potential JSON substring using bracket matching.", level="debug")
        try: parsed = json.loads(json_substring); log_step("JSON parsed successfully from extracted substring.", level="success"); return (True, parsed)
        except json.JSONDecodeError as e:
            log_step(f"JSON parsing error on extracted substring: {e}", level="error")
            context_len = 50; error_context = json_substring[max(0, e.pos - context_len) : min(len(json_substring), e.pos + context_len)]
            log_step(f"Error near: ...{error_context}...", level="error")
            return (False, f"JSON parsing error: {e} near '{error_context}'")
    else: log_step("Could not find matching closing bracket/brace for potential JSON.", level="warning"); return (False, "Matching closing bracket/brace not found")


def request_json_data(driver, prompt, is_final_request=False, max_retries=CHATGPT_JSON_RETRIES):
    """Send prompt, get response, parse JSON, retry on failure."""
    log_step(f"Requesting JSON data (Final Request: {is_final_request}, Retries: {max_retries})...", important=True)
    send_message(driver, prompt) # Send the initial prompt

    for attempt in range(max_retries + 1):
        log_step(f"Attempt {attempt + 1}/{max_retries + 1} to get and parse JSON response.")
        response_text = get_last_response_text(driver)

        if not response_text:
            if attempt < max_retries:
                log_step("Received empty response text. Sending retry prompt.", level="warning")
                fix_prompt = ("There was no response text. Please provide the requested information again." + (" Strictly follow the specified JSON format. Output ONLY the JSON object." if is_final_request else ""))
                send_message(driver, fix_prompt)
                continue
            else: log_step("Received empty response text on final attempt.", level="error"); break

        log_step(f"Attempting JSON parse (Attempt {attempt + 1})...")
        success, data_or_error = try_parse_json(response_text)

        if success: log_step("JSON parsed successfully!", level="success", important=True); return data_or_error

        log_step(f"JSON parse failed: {data_or_error}", level="warning")
        if attempt < max_retries:
            log_step("Sending JSON fix prompt...")
            fix_prompt = (f"The previous response was not valid JSON or contained extra text. Error: {data_or_error}\n" +
                          f"Please regenerate the response providing ONLY the valid JSON object requested, adhering strictly to the schema. " +
                          f"{'Absolutely no extra text, comments, or markdown.' if is_final_request else 'Ensure the JSON is correctly formatted.'}")
            send_message(driver, fix_prompt)
        else: log_step("Max retries reached for JSON parsing.", level="error"); log_step(f"Final failed response text:\n---\n{response_text}\n---", level="error"); break

    log_step("Failed to get valid JSON after multiple attempts.", level="error", important=True)
    return None # Indicate failure

# --- Web Search (Tavily) & Download Functions ---
def perform_web_search(query, api_key, download_dir, num_results=10):
    """Performs web search using Tavily API and downloads ALLOWED images."""
    log_step(f"Performing Tavily web search for: '{query[:100]}...'")
    headers = {"Content-Type": "application/json"}
    payload = json.dumps({"api_key": api_key, "query": query, "search_depth": "basic", "include_answer": False, "include_images": True, "include_raw_content": False, "max_results": num_results * 2 })
    downloaded_paths = []
    web_results = None
    try:
        response = requests.post(TAVILY_SEARCH_URL, headers=headers, data=payload, timeout=45)
        response.raise_for_status()
        web_results = response.json()
        if 'images' in web_results and web_results['images']:
            log_step(f"Found {len(web_results['images'])} potential images from Tavily. Downloading allowed types...")
            download_dir.mkdir(parents=True, exist_ok=True)
            img_count = 0
            for i, img_url in enumerate(web_results['images']):
                if img_count >= num_results: break
                try:
                    parsed_url = urlparse(img_url); url_ext = os.path.splitext(parsed_url.path)[1].lower()
                    if url_ext not in ALLOWED_IMAGE_EXTENSIONS and url_ext: continue #log_step(f"  Skipping URL (disallowed extension '{url_ext}'): {img_url}", level="debug");
                    filename_base = sanitize_name(os.path.basename(parsed_url.path).replace(url_ext, ''))
                    if not filename_base: filename_base = f"image_{i:03d}"
                    download_path_base = download_dir / f"{filename_base[:50]}"
                    actual_downloaded_path = download_image(img_url, download_path_base)
                    if actual_downloaded_path: downloaded_paths.append(str(actual_downloaded_path)); img_count += 1; #log_step(f"  Downloaded image {img_count}/{num_results}: {Path(actual_downloaded_path).name}", level="debug")
                    time.sleep(0.1)
                except Exception as download_err: log_step(f"  Error processing image URL {img_url}: {download_err}", level="warning")
            log_step(f"Successfully attempted downloads. Found {len(downloaded_paths)} images of allowed types in {download_dir}")
        else: log_step("No images found in Tavily results.")
    except requests.exceptions.Timeout: log_step("Tavily API request timed out.", level="error")
    except requests.exceptions.RequestException as e: log_step(f"Tavily API request failed: {e}", level="error")
    except Exception as e: log_step(f"Error processing Tavily search results: {e}", level="error")
    return web_results, downloaded_paths

def download_image(url, filepath_base):
    """Downloads a single image if it's JPG/PNG, saving with correct extension."""
    try:
        parsed_url = urlparse(url); url_ext = os.path.splitext(parsed_url.path)[1].lower()
        if url_ext not in ALLOWED_IMAGE_EXTENSIONS and url_ext: return False
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, stream=True, timeout=20, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '').lower(); final_ext = None
        if 'image/jpeg' in content_type or 'image/jpg' in content_type: final_ext = '.jpg'
        elif 'image/png' in content_type: final_ext = '.png'
        elif url_ext in ALLOWED_IMAGE_EXTENSIONS: final_ext = url_ext
        else: response.close(); return False #log_step(f"  Skipping download (unsupported type '{content_type}' / URL ext '{url_ext}'): {url}", level="debug");
        final_filepath = filepath_base.with_suffix(final_ext)
        with open(final_filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
        return str(final_filepath)
    except requests.exceptions.Timeout: log_step(f"Timeout downloading {url}", level="warning"); return False
    except requests.exceptions.RequestException as e: log_step(f"HTTP error downloading {url}: {e}", level="warning");
    except Exception as e: log_step(f"Failed to download image {url}: {e}", level="warning");
    if 'final_filepath' in locals() and final_filepath.exists(): final_filepath.unlink(missing_ok=True)
    return False


# --- Image Filtering Function ---
def initialize_face_detector():
    """Initializes the MTCNN face detector."""
    global face_detector
    if face_detector is None:
        log_step("Initializing MTCNN face detector...")
        try:
            os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
            face_detector = MTCNN()
            log_step("MTCNN initialized successfully.", level="success")
        except Exception as e:
            log_step(f"CRITICAL ERROR: Failed to initialize MTCNN face detector: {e}", level="error")
            log_step("Face filtering will be disabled.", level="error")
    return face_detector

# --- FIXED: filter_and_copy_single_face_images includes renaming ---
def filter_and_copy_single_face_images(image_paths, target_dir, actor_name_sanitized, required_faces=FACE_FILTER_REQUIRED_COUNT):
    """Filters images for faces, copies valid ones, AND RENAMES them sequentially."""
    valid_image_paths_in_target = []
    if not image_paths: return valid_image_paths_in_target

    detector = initialize_face_detector()
    if detector is None: log_step("Face detector not available. Skipping face filtering.", level="warning"); return []

    log_step(f"Filtering {len(image_paths)} images for {actor_name_sanitized} for exactly {required_faces} face(s)...")
    target_dir.mkdir(parents=True, exist_ok=True)
    valid_image_counter = 0 # Counter for sequential naming

    for img_path_str in image_paths:
        img_path = Path(img_path_str)
        if not img_path.is_file(): continue #log_step(f"  Skipping non-file: {img_path.name}", level="debug");

        try:
            img_bgr = cv2.imread(str(img_path))
            if img_bgr is None: log_step(f"  Could not read image: {img_path.name}", level="warning"); continue
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            faces = detector.detect_faces(img_rgb)

            valid_faces_count = 0
            if len(faces) == required_faces:
                all_confident = True
                for face in faces:
                    if face['confidence'] < FACE_CONFIDENCE_THRESHOLD: all_confident = False; break
                if all_confident: valid_faces_count = len(faces)

            if valid_faces_count == required_faces:
                # --- Renaming Logic ---
                valid_image_counter += 1
                file_extension = img_path.suffix # Get original extension
                new_filename = f"{actor_name_sanitized}_{valid_image_counter}{file_extension}"
                target_path = target_dir / new_filename
                # --- End Renaming Logic ---

                #log_step(f"  VALID ({valid_faces_count} face): Copying {img_path.name} -> {target_path.name}", level="debug")
                shutil.copy2(img_path, target_path)
                valid_image_paths_in_target.append(str(target_path)) # Append the NEW path

        except cv2.error as cv_err: log_step(f"  OpenCV error processing {img_path.name}: {cv_err}", level="warning")
        except Exception as e: log_step(f"  Error processing image {img_path.name} with MTCNN: {e}", level="warning")

    log_step(f"Found {len(valid_image_paths_in_target)} valid images for {actor_name_sanitized} with {required_faces} face(s).")
    return valid_image_paths_in_target


# --- Web Selection (Flask) Functions ---
# --- FIXED: Template IDs using actor_name ---
SELECTION_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Actor Image Selection</title>
    <style>
        body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; }
        h1, h2 { text-align: center; color: #333; }
        .actor-section { background-color: #fff; border: 1px solid #ddd; margin-bottom: 30px; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .actor-header { border-bottom: 1px solid #eee; padding-bottom: 10px; margin-bottom: 20px; }
        .actor-name { font-size: 1.5em; font-weight: bold; color: #555; }
        .character-name { font-size: 1em; color: #777; }
        .image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .image-item { text-align: center; border: 1px solid #eee; padding: 5px; }
        .image-item img { max-width: 100%; height: 150px; object-fit: cover; border: 2px solid transparent; border-radius: 4px; cursor: pointer; transition: border-color 0.2s; }
        .image-item input[type="radio"] { display: none; } /* Hide radio button */
        .image-item input[type="radio"]:checked + label img { border-color: #007bff; box-shadow: 0 0 8px rgba(0,123,255,0.5); }
        .image-item label { display: block; }
        .filename { font-size: 0.8em; color: #666; word-break: break-all; margin-top: 5px; }
        .skip-option label { display: inline-block; margin-left: 10px; color: #dc3545; font-weight: bold;}
        .skip-option input[type="radio"]:checked + label { background-color: #f8d7da; padding: 2px 5px; border-radius: 3px;}
        .submit-button { display: block; width: 200px; margin: 30px auto 0 auto; padding: 12px 20px; background-color: #28a745; color: white; border: none; border-radius: 5px; font-size: 1.1em; cursor: pointer; transition: background-color 0.2s; }
        .submit-button:hover { background-color: #218838; }
        .no-images { color: #777; font-style: italic; }
    </style>
</head>
<body>
    <h1>Actor Image Selection</h1>
    <h2>Movie: {{ movie_name }}</h2>
    <form action="/submit" method="post">
        {% for actor_name, data in actors_data.items() %}
        <div class="actor-section">
            <div class="actor-header">
                 <span class="actor-name">{{ actor_name }}</span>
                 {% if data.character_name %}<span class="character-name"> (as {{ data.character_name }})</span>{% endif %}
            </div>
            {% if data.candidates %}
                <div class="image-grid">
                    {% for image_info in data.candidates %}
                    <div class="image-item">
                        <!-- FIXED: Use actor_name and loop.index for unique ID, sanitize actor name for ID -->
                        <input type="radio" id="img_{{ actor_name|replace(' ', '_')|replace('.', '_') }}_{{ loop.index }}" name="{{ actor_name }}" value="{{ image_info.full_path }}" required>
                        <label for="img_{{ actor_name|replace(' ', '_')|replace('.', '_') }}_{{ loop.index }}">
                            <img src="{{ url_for('serve_project_image', filepath=image_info.relative_path) }}" alt="{{ image_info.filename }}" title="{{ image_info.filename }}">
                            <div class="filename">{{ image_info.filename }}</div>
                        </label>
                    </div>
                    {% endfor %}
                </div>
                <hr>
                <div class="skip-option">
                    <!-- FIXED: Use actor_name for unique ID, sanitize actor name for ID -->
                    <input type="radio" id="skip_{{ actor_name|replace(' ', '_')|replace('.', '_') }}" name="{{ actor_name }}" value="SKIP" required>
                    <label for="skip_{{ actor_name|replace(' ', '_')|replace('.', '_') }}">SKIP this actor</label>
                </div>
            {% else %}
                <p class="no-images">No valid single-face images found for this actor.</p>
                <div class="skip-option">
                     <!-- FIXED: Use actor_name for unique ID, sanitize actor name for ID -->
                    <input type="radio" id="skip_{{ actor_name|replace(' ', '_')|replace('.', '_') }}" name="{{ actor_name }}" value="SKIP" checked required> <!-- Default SKIP checked -->
                    <label for="skip_{{ actor_name|replace(' ', '_')|replace('.', '_') }}">SKIP this actor (No images available)</label>
                </div>
            {% endif %}
        </div>
        {% endfor %}
        <button type="submit" class="submit-button">Submit Selections</button>
    </form>
</body>
</html>
"""

flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = os.urandom(24)

@flask_app.route('/project_files/<path:filepath>')
def serve_project_image(filepath):
    """Serves images relative to the PROJECTS_BASE_DIR."""
    #log_step(f"Flask: Request received for /project_files/{filepath}", level="debug")
    try:
        base_path = PROJECTS_BASE_DIR.resolve()
        safe_filepath = Path(filepath).as_posix()
        absolute_req_path = (base_path / safe_filepath).resolve()
        if not absolute_req_path.is_relative_to(base_path): log_step(f"Flask: Forbidden path: {filepath}", level="warning"); abort(403)
        if not absolute_req_path.is_file(): log_step(f"Flask: Image file not found: {absolute_req_path}", level="warning"); abort(404)
        relative_to_base = absolute_req_path.relative_to(base_path)
        directory = str(base_path / relative_to_base.parent); filename = relative_to_base.name
        #log_step(f"Flask: Serving file: Directory='{directory}', Filename='{filename}'", level="debug")
        return send_from_directory(directory, filename)
    except Exception as e: log_step(f"Flask: Error serving file {filepath}: {e}", level="error"); abort(500)

@flask_app.route('/')
def selection_page():
    """Renders the main selection template."""
    #log_step("Flask: Request received for / (selection_page)", level="debug")
    global actors_data_for_flask, movie_name_for_flask
    try:
        if actors_data_for_flask is None or movie_name_for_flask is None: log_step("Flask: Template data not ready.", level="error"); return "Server Error: Template data not initialized.", 500
        #log_step(f"Flask: Rendering template with {len(actors_data_for_flask)} actors for movie '{movie_name_for_flask}'.", level="debug")
        rendered_html = render_template_string(SELECTION_HTML_TEMPLATE, actors_data=actors_data_for_flask, movie_name=movie_name_for_flask)
        #log_step("Flask: Template rendered successfully.", level="debug")
        return rendered_html
    except Exception as e:
        log_step(f"Flask: Error rendering template in / route: {e}", level="error", important=True)
        import traceback; tb = traceback.format_exc(); log_step(tb, level="error")
        return f"Internal Server Error during template rendering: <pre>{e}\n\n{tb}</pre>", 500

@flask_app.route('/submit', methods=['POST'])
def submit_selection():
    """Handles form submission and stores results."""
    #log_step("Flask: Request received for /submit (POST)", level="debug")
    global web_selection_results, web_selection_event
    web_selection_results = request.form.to_dict()
    log_step("Received selections from web UI.", level="success")
    #log_step(f"Selections: {json.dumps(web_selection_results, indent=2)}", level="debug")
    web_selection_event.set()
    try:
        shutdown_func = request.environ.get('werkzeug.server.shutdown')
        if shutdown_func: log_step('Flask: Shutting down Flask server...', level="info"); shutdown_func()
        else: log_step('Flask: Werkzeug server shutdown function not found.', level="warning")
    except Exception as e: log_step(f"Flask: Error during Flask shutdown attempt: {e}", level="warning")
    return "Selections received! You can close this window."

def run_flask_app(host, port, data, movie_name):
    """Runs the Flask app in a separate thread."""
    global actors_data_for_flask, movie_name_for_flask
    actors_data_for_flask = data; movie_name_for_flask = movie_name
    log_step(f"Starting Flask server on http://{host}:{port}")
    try: flask_app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception as e: log_step(f"Flask server failed to start or crashed: {e}", level="error"); web_selection_event.set()

def present_web_selection_page(candidates_data, movie_name):
    """Starts the Flask server and waits for user selection."""
    global web_selection_results, web_selection_event
    web_selection_results = None; web_selection_event.clear()
    flask_thread = threading.Thread(target=run_flask_app, args=(WEB_SELECTION_HOST, WEB_SELECTION_PORT, candidates_data, movie_name), daemon=True)
    flask_thread.start(); time.sleep(2)
    log_step(f"Web selection interface running at http://{WEB_SELECTION_HOST}:{WEB_SELECTION_PORT}", important=True)
    log_step("Please open the URL in your browser, make your selections, and click 'Submit Selections'.")
    log_step(f"Waiting for user selection (Timeout: {WEB_SELECTION_TIMEOUT} seconds)...")
    event_set = web_selection_event.wait(timeout=WEB_SELECTION_TIMEOUT)
    if not event_set: log_step("Timeout waiting for web selection.", level="error"); return None
    else:
        if web_selection_results is None: log_step("Web selection event triggered, but no results captured.", level="error"); return None
        log_step("Web selection submitted.", level="success"); return web_selection_results


# --- Main Execution ---
if __name__ == "__main__":
    main_start_time = time.time()
    log_step("===== Orchestrator Script Started =====", important=True)
    driver = None

    try:
        # --- 1. Get User Input ---
        log_step("--- Phase 1: Initialization and Setup ---", important=True)
        movie_name = input("Enter the movie name: ").strip()
        while not movie_name: print("Movie name cannot be empty."); movie_name = input("Enter the movie name: ").strip()
        themes = [ "Cyberpunk Noir", "Solarpunk Utopia", "Biopunk Dystopia", "Post-Apocalyptic Dieselpunk", "Retro-Futurism (1950s Atom Age)", "Space Opera Galaxy", "Steampunk Mechanized World", "Nanopunk Microverse", "Clockpunk Renaissance", "Deep Sea Hydro-Futurism" ]
        print("\nSelect a futuristic theme:"); [print(f"{i + 1}. {theme}") for i, theme in enumerate(themes)]; print(f"{len(themes) + 1}. Enter a custom theme")
        selected_theme = None
        while selected_theme is None:
            try:
                choice = input(f"Enter number (1-{len(themes)+1}) or type custom theme: ").strip()
                if not choice: print("Input cannot be empty."); continue
                try: choice_num = int(choice)
                except ValueError: selected_theme = choice # Treat as custom if not int
                else:
                    if 1 <= choice_num <= len(themes): selected_theme = themes[choice_num - 1]
                    elif choice_num == len(themes) + 1: custom_theme = input("Enter your custom theme: ").strip(); selected_theme = custom_theme if custom_theme else None; print("Custom theme cannot be empty." if not selected_theme else "")
                    else: print(f"Invalid number (1-{len(themes)+1}).")
            except Exception as e: print(f"Input error: {e}")
        log_step(f"Input received - Movie: '{movie_name}', Theme: '{selected_theme}'")

        # --- 2. Validate API Keys ---
        log_step("Validating API keys...")
        if not TAVILY_API_KEY: log_step(f"{TAVILY_API_KEY_NAME} not found!", level="error", important=True); sys.exit(1)
        log_step("Tavily API key found.", level="success")

        # --- 3. Create Project Directories ---
        log_step("Creating project directories...")
        try: PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True); log_step(f"Ensured base project directory exists: {PROJECTS_BASE_DIR}", level="info")
        except OSError as e: log_step(f"CRITICAL: Failed to access/create base dir {PROJECTS_BASE_DIR}: {e}", level="error"); sys.exit(1)
        project_timestamp = datetime.now().strftime(TIMESTAMP_FORMAT); project_name_sanitized = f"{sanitize_name(movie_name)}_{sanitize_name(selected_theme)}_{project_timestamp}"
        project_path = PROJECTS_BASE_DIR / project_name_sanitized; characters_path = project_path / CHARACTERS_FOLDER_NAME; source_actors_path = project_path / SOURCE_ACTORS_FOLDER_NAME
        try: project_path.mkdir(parents=True, exist_ok=True); characters_path.mkdir(exist_ok=True); source_actors_path.mkdir(exist_ok=True); log_step(f"Project directory structure ensured at: {project_path}", level="success")
        except OSError as e: log_step(f"Failed to create specific project directories in {PROJECTS_BASE_DIR}: {e}", level="error", important=True); sys.exit(1)

        # --- 4. Initialize Selenium & Handle Login ---
        log_step("Initializing WebDriver...")
        options = uc.ChromeOptions(); options.add_argument("--start-maximized")
        driver = uc.Chrome(options=options); log_step("WebDriver initialized.", level="success")
        log_step(f"Navigating to {CHATGPT_BASE_URL}..."); driver.get(CHATGPT_BASE_URL); time.sleep(4)
        log_step("Attempting to load cookies for session..."); cookies_loaded = load_cookies(driver, COOKIE_PATH)
        if cookies_loaded:
            log_step("Cookies loaded. Refreshing page..."); driver.refresh(); time.sleep(6)
            try:
                 login_elements = driver.find_elements(By.XPATH, "//button[contains(., 'Log in')] | //button[contains(., 'Sign up')]")
                 if any(el.is_displayed() for el in login_elements if el.is_displayed()): log_step("Login prompts visible after cookie load. Manual login likely needed.", level="warning"); cookies_loaded = False
                 else: log_step("Session likely active.", level="success")
            except Exception as e: log_step(f"Could not verify session state: {e}", level="warning")
        if not cookies_loaded:
            log_step("Manual login required.", important=True); print(f"\n>>> Please log in to ChatGPT manually ({CHATGPT_LOGIN_TIMEOUT}s timeout). <<<"); input(f">>> Press Enter ONLY after you are fully logged in. <<<")
            log_step("User indicated login complete."); save_cookies(driver, COOKIE_PATH)

        # --- 5. Navigate to Target Model & Wait ---
        log_step(f"Navigating to target model URL: {CHATGPT_TARGET_MODEL_URL}"); driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(3)
        wait_for_input_box(driver, timeout=45); log_step("ChatGPT page ready.", level="success")

        # --- 6. ChatGPT Interaction Phase (Revised 3-Prompt Strategy) ---
        log_step("--- Phase 2: Get Character Info via ChatGPT ---", important=True)
        json_schema_example = """{ "movie_characters": [ { "character_name": "...", "actor_name": "...", "description": "...", "release_year": "YYYY" } ] }""" # Compact example
        prompt_1 = (f"List iconic characters from '{movie_name}'. Include actor name, iconic description, and release year (YYYY). " +
                    f"Try to use this JSON schema, commentary allowed:\n```json\n{json_schema_example}\n```\n")
        log_step("Requesting initial character list (Prompt 1 - commentary allowed)..."); send_message(driver, prompt_1); response_1_text = get_last_response_text(driver); log_step("Received response for Prompt 1.", level="debug")
        prompt_2 = (f"Review the list for '{movie_name}'. Add any missing iconic characters (even minor memorable ones). " +
                    f"Provide the COMPLETE updated list. Use the same JSON schema, explanations allowed:\n```json\n{json_schema_example}\n```\n")
        log_step("Requesting refined character list (Prompt 2 - commentary allowed)..."); send_message(driver, prompt_2); response_2_text = get_last_response_text(driver); log_step("Received response for Prompt 2.", level="debug")
        prompt_3 = ("Provide JUST the final, complete list from our previous exchange as a raw JSON object. " +
                    f"Use this exact JSON schema:\n```json\n{json_schema_example}\n```\n" +
                    "IMPORTANT: Output ONLY the raw JSON. No extra text, comments, or markdown fences.")
        log_step("Requesting FINAL CLEAN JSON character list (Prompt 3)..."); final_char_data = request_json_data(driver, prompt_3, is_final_request=True)

        if not final_char_data or "movie_characters" not in final_char_data or not isinstance(final_char_data.get("movie_characters"), list):
             log_step("Failed to get valid final character data from ChatGPT.", level="error", important=True); raise RuntimeError("ChatGPT did not provide valid final character data.")
        log_step("Successfully received and parsed final character list.", level="success", important=True); final_char_list = final_char_data["movie_characters"]
        chatgpt_output_path = project_path / "chatgpt_movie_info.json"
        try:
            with open(chatgpt_output_path, 'w', encoding='utf-8') as f: json.dump(final_char_data, f, indent=2, ensure_ascii=False); log_step(f"Saved final ChatGPT data to {chatgpt_output_path}")
        except IOError as e: log_step(f"Failed to save ChatGPT data: {e}", level="error")

        actor_info_map = {}
        for item in final_char_list:
            actor_name = item.get("actor_name"); char_name = item.get("character_name", "Unknown")
            if actor_name and isinstance(actor_name, str) and actor_name.strip():
                 if actor_name not in actor_info_map: actor_info_map[actor_name] = {"character_name": char_name, "release_year": item.get("release_year")}
            else: log_step(f"Skipping character '{char_name}' due to missing actor name.", level="warning")
        actor_list = list(actor_info_map.keys())
        if not actor_list: log_step("No valid actors extracted. Cannot proceed.", level="error", important=True); raise RuntimeError("No actors found.")
        log_step(f"Extracted {len(actor_list)} unique actors: {', '.join(actor_list)}")

        # --- 7. Image Acquisition & Filtering Phase ---
        log_step("--- Phase 3: Image Acquisition & Filtering ---", important=True)
        all_candidates_for_web = {}; processed_actors_count = 0
        for actor_name in actor_list:
            processed_actors_count += 1; log_step(f"\nProcessing Actor {processed_actors_count}/{len(actor_list)}: {actor_name}")
            actor_details = actor_info_map[actor_name]; character_name = actor_details["character_name"]; release_year = actor_details.get("release_year")
            actor_name_sanitized = sanitize_name(actor_name); actor_dir = characters_path / actor_name_sanitized; actor_dir.mkdir(parents=True, exist_ok=True)
            decade_str = "";
            # --- FIXED: Moved try/except for decade calculation to new lines ---
            if release_year:
                 try:
                     year_int = int(re.sub(r'\D', '', str(release_year)))
                     decade_start = (year_int // 10) * 10
                     decade_str = f"{decade_start}s"
                 except ValueError: # More specific exception
                      log_step(f"Could not parse year '{release_year}' for decade calculation.", level="warning")
                      pass # Continue without decade if parsing fails

            year_context = f"({release_year})" if release_year else ""; decade_context = f"{decade_str}" if decade_str else ""; era_query_part = f"{year_context} OR {decade_context}".strip().strip("OR").strip()
            search_query = f'"{actor_name}" {era_query_part} face portrait close-up OR headshot -poster -group -cast -multiple -advertisement -logo -text -movie -scene -comic -cartoon -toy -figure -drawing -painting -illustration -art -sketch -cgi -render -ai -generated'.strip()
            log_step(f"  Using Tavily Image Search Query: {search_query}")
            actor_temp_image_dir = actor_dir / TEMP_IMAGE_FOLDER_NAME; actor_filtered_image_dir = actor_dir / FILTERED_IMAGE_FOLDER_NAME
            _, downloaded_paths_in_temp = perform_web_search(search_query, TAVILY_API_KEY, download_dir=actor_temp_image_dir, num_results=TAVILY_SEARCH_COUNT)

            valid_filtered_image_paths = []
            if downloaded_paths_in_temp:
                 valid_filtered_image_paths = filter_and_copy_single_face_images(
                     downloaded_paths_in_temp, actor_filtered_image_dir, actor_name_sanitized, required_faces=FACE_FILTER_REQUIRED_COUNT)
            else: log_step(f"  No suitable images downloaded for {actor_name}.")

            candidates_list_for_actor = []
            if valid_filtered_image_paths:
                base_serving_dir = PROJECTS_BASE_DIR.resolve()
                for img_path_str in valid_filtered_image_paths: # These paths now have sequential names
                    try:
                        img_path_obj = Path(img_path_str).resolve(); relative_path_obj = img_path_obj.relative_to(base_serving_dir); relative_path_url = relative_path_obj.as_posix()
                        candidates_list_for_actor.append({"filename": img_path_obj.name, "full_path": str(img_path_obj), "relative_path": relative_path_url})
                    except ValueError as e: log_step(f"  Cannot make path relative: Img='{img_path_obj}', Base='{base_serving_dir}'. Error: {e}. Skipping.", level="warning")
                    except Exception as e: log_step(f"  Error processing candidate path '{img_path_str}': {e}", level="warning")

            all_candidates_for_web[actor_name] = {"candidates": candidates_list_for_actor, "character_name": character_name}
            if candidates_list_for_actor: log_step(f"  Prepared {len(candidates_list_for_actor)} candidates for {actor_name}.")
            else: log_step(f"  No valid single-face images prepared for {actor_name}.")
            # --- FIXED: Moved try/except for rmtree to new lines ---
            if actor_temp_image_dir.exists():
                try:
                    shutil.rmtree(actor_temp_image_dir)
                except OSError as e:
                    logging.warning(f"  Could not remove temp dir {actor_temp_image_dir}: {e}")
        log_step(f"Finished gathering and filtering images for {len(actor_list)} actors.")

        # --- 8. Web Selection Phase ---
        log_step("--- Phase 4: Web-Based User Selection ---", important=True)
        actor_image_map = {}
        if not all_candidates_for_web: log_step("No actors have candidates. Skipping web selection.", level="warning")
        else:
            selections = present_web_selection_page(all_candidates_for_web, movie_name)
            if selections:
                log_step("Processing web selections...")
                for actor_name, selected_value in selections.items():
                    if selected_value != "SKIP":
                        try:
                            selected_path_from_filtered = Path(selected_value) # Path already has sequential name
                            if selected_path_from_filtered.is_file():
                                # --- FIXED: Simplified copy - name is already correct ---
                                final_target_path = source_actors_path / selected_path_from_filtered.name # Use name directly
                                shutil.copy2(selected_path_from_filtered, final_target_path)
                                log_step(f"Copied selected image for '{actor_name}' TO final location: {final_target_path.name}", level="success")
                                actor_image_map[actor_name] = str(final_target_path)
                            else: log_step(f"Selected path '{selected_value}' for {actor_name} not found.", level="error"); actor_image_map[actor_name] = None
                        except Exception as e: log_step(f"Error copying selected file for {actor_name}: {e}", level="error"); actor_image_map[actor_name] = None
                    else: log_step(f"User skipped actor: {actor_name}."); actor_image_map[actor_name] = None

                log_step("Cleaning up filtered image folders...", level="debug")
                for actor_folder in characters_path.iterdir():
                     if actor_folder.is_dir():
                         filtered_dir = actor_folder / FILTERED_IMAGE_FOLDER_NAME
                         # --- FIXED: Moved try/except for rmtree to new lines ---
                         if filtered_dir.exists():
                              try:
                                   shutil.rmtree(filtered_dir)
                              except OSError as e:
                                   log_step(f"Could not remove {filtered_dir}: {e}", level="warning")
            else: log_step("Web selection failed, timed out, or skipped.", level="error", important=True)

        # --- 9. Finalization ---
        log_step("--- Phase 5: Finalizing ---", important=True)
        log_step(f"Final Actor Source Image Map (in {source_actors_path}):"); log_step(json.dumps(actor_image_map, indent=2))
        actors_with_source_images = sum(1 for path in actor_image_map.values() if path is not None)
        if actors_with_source_images == 0: log_step("No source images selected/copied.", level="warning")
        log_step(f"Process complete. {actors_with_source_images} actor source images saved.", level="success"); log_step(f"Project Path: {project_path}", important=True)

    except KeyboardInterrupt: log_step("Process interrupted by user (Ctrl+C).", level="warning", important=True)
    except TimeoutException as e: log_step(f"Operation timed out: {e}", level="error", important=True)
    except WebDriverException as e: log_step(f"WebDriver error: {e}", level="error", important=True); log_step("Browser closed? Driver mismatch? Network issue?", level="error")
    except RuntimeError as e: log_step(f"Runtime error: {e}", level="error", important=True)
    except Exception as e: log_step(f"An unexpected critical error occurred: {e}", level="error", important=True); import traceback; traceback.print_exc()
    finally:
        log_step("--- Phase 6: Cleanup ---", important=True)
        if driver:
            log_step("Attempting to close WebDriver...")
            try:
                driver.quit()
                log_step("WebDriver closed.", level="success")
            except Exception as e:
                log_step(f"Error closing WebDriver: {e}", level="warning")
        else:
            log_step("WebDriver not initialized or already closed.")

    main_end_time = time.time(); log_step(f"===== Orchestrator Script Finished in {main_end_time - main_start_time:.2f} seconds =====", important=True)
    input("\n<<< Process complete. Press Enter to exit... >>>"); sys.exit(0)