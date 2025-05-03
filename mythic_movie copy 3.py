# -*- coding: utf-8 -*-
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
import re
from pathlib import Path
from datetime import datetime
import logging
from dotenv import load_dotenv
import traceback # For detailed error logging

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
PROJECT_BASE_FOLDER_NAME = "mythic_movie_project" # Root folder in script's directory
PROJECT_PREFIX = "mythic_movie"
LOG_FILE_NAME = f"mythic_movie_log_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"

# --- Dynamic Paths (Relative to script location) ---
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECTS_BASE_DIR = SCRIPT_DIR / PROJECT_BASE_FOLDER_NAME
COOKIE_PATH = SCRIPT_DIR / "chatgpt_cookies.json"
DOTENV_PATH = SCRIPT_DIR / ".env"
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / LOG_FILE_NAME

# --- Output Filenames ---
SYSTEM_PROMPT_LOG_FILENAME = "00_system_prompt.txt" # Log the system prompt sent
STORY_FILENAME = "01_story_approved.txt"
CHARACTERS_FILENAME = "02_characters.json"
SCENES_FILENAME = "03_scenes.json"
IMAGE_PROMPTS_FILENAME = "04_image_prompts.json"
ELEMENTS_FILENAME = "05_elements.json"
VIDEO_PROMPTS_FILENAME = "06_video_prompts.json"
TIMELINE_FILENAME = "07_timeline.json"

# --- ChatGPT Settings ---
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o"
CHATGPT_LOGIN_TIMEOUT = 120
CHATGPT_RESPONSE_TIMEOUT = 360 # Slightly increased
CHATGPT_JSON_RETRIES = 3      # Slightly increased
MAX_STORY_ATTEMPTS = 3      # Max attempts for story approval

# --- Load Environment Variables ---
if DOTENV_PATH.is_file():
    load_dotenv(dotenv_path=DOTENV_PATH)
    print(f"[*] Loaded environment variables from: {DOTENV_PATH}")
else:
    print(f"[!] Warning: .env file not found at {DOTENV_PATH}. Continuing without it.")

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
logging.getLogger("urllib3").setLevel(logging.WARNING); logging.getLogger("selenium").setLevel(logging.WARNING)
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"; logging.getLogger("tensorflow").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# --- System Prompt Definition ---
SYSTEM_PROMPT = """
You are an AI assistant collaborating with a Python script to generate assets for a short (2-minute) cinematic mythic movie vignette for YouTube.
Follow these instructions precisely:

1.  **Modes:** Pay attention to the `MODE:` prefix in prompts.
    *   `MODE:COMMENTARY`: You can respond conversationally, but stick to the request. Provide requested text directly without introductory phrases like "Here is...".
    *   `MODE:JSON`: You MUST output ONLY a valid, raw JSON object matching the requested schema. Absolutely NO commentary, introductions, apologies, explanations, or markdown ```json fences``` are allowed. Just the pure JSON.
2.  **JSON Schema Adherence:** When `MODE:JSON` is used, strictly adhere to the schema described or exemplified in the prompt. Use snake_case for keys unless the example shows otherwise.
3.  **Conciseness:** Keep responses focused. Story drafts should be ~250-300 words. JSON data should be complete but not overly verbose.
4.  **Error Handling (Internal):** If you cannot fulfill a JSON request exactly as specified, output a simple JSON error object like: `{"error": "Could not generate data as requested. Reason: [brief reason]"}`. Do NOT add commentary around this error object in JSON mode.
5.  **Context:** Remember the approved story text when generating subsequent components like characters and scenes. Reference previous messages accurately.

Ready to begin when you receive the first user prompt after this system message.
"""


# --- Helper: log_step ---
def log_step(message, level="info", important=False):
    prefix_map = {"info":"[*]","warning":"[!]","error":"[X]","success":"[+]","debug":"[D]"}
    final = f"{prefix_map.get(level,'[?]')} {message}"
    if important: final = f"--- {final} ---"
    if level == "error": log.error(final)
    elif level == "warning": log.warning(final)
    elif level == "debug": log.debug(final)
    else: log.info(final)

# --- Sanitizers ---
def sanitize_name(name):
    s = re.sub(r'[<>:"/\\|?*\']', "_", str(name))
    s = "".join(c for c in s if c.isalnum() or c in (" ","_","-")).strip().replace(" ","_")
    s = re.sub(r"[_]+","_",s).strip("_-")
    return s or "invalid_name"

def sanitize_for_chromedriver(text):
    return re.sub(r"[^\u0000-\uffff]","", text) if isinstance(text, str) else text

# --- Cookie Management ---
def save_cookies(driver, path):
    log_step(f"Saving cookies to: {path}")
    try: cookies = driver.get_cookies(); path.parent.mkdir(parents=True, exist_ok=True);
    except WebDriverException as e: log_step(f"WebDriver error preparing save cookies: {e}", level="error"); return
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(cookies, f, indent=2)
        log_step("Cookies saved", level="success")
    except Exception as e: log_step(f"Error saving cookies to {path}: {e}", level="error")

def load_cookies(driver, path):
    if not path.exists(): log_step("Cookie file not found", level="info"); return False
    log_step(f"Loading cookies from: {path}")
    try: cookies = json.load(open(path, encoding="utf-8"))
    except Exception as e: log_step(f"Error reading cookie file: {e}", level="error"); return False
    # Navigate to base domain *before* loading cookies
    base_domain_set = False
    for cookie in cookies:
        domain = cookie.get("domain")
        if domain and ("openai.com" in domain or "chatgpt.com" in domain):
             try: base_url = f"https://{domain.lstrip('.')}"; driver.get(base_url); time.sleep(1); base_domain_set = True; break
             except Exception as nav_e: log_step(f"Warn: Could not navigate to base domain {domain}: {nav_e}", level="warning")
    if not base_domain_set: log_step("Could not determine base domain, loading on current page.", level="warning")
    # Load cookies
    added, skipped = 0, 0
    for c in cookies:
        if not isinstance(c, dict) or "name" not in c or "value" not in c: skipped += 1; continue
        if 'sameSite' in c and c['sameSite'] not in ['Lax','Strict','None']: del c['sameSite']
        if 'expiry' in c and isinstance(c['expiry'], float): c['expiry'] = int(c['expiry'])
        try: driver.add_cookie(c); added += 1
        except Exception as add_e: skipped += 1; log_step(f"Debug: Failed add cookie {c.get('name')}: {add_e}", level="debug")
    log_step(f"Cookies loaded: {added}, skipped: {skipped}", level="success")
    return added > 0

# --- Selenium Helpers ---
def wait_for_element(driver, by, value, timeout=30, condition=EC.presence_of_element_located):
    try: return WebDriverWait(driver, timeout).until(condition((by, value)))
    except Exception as e: log_step(f"Error waiting for element {by}={value}: {e}", level="error"); raise

def wait_for_input_box(driver, timeout=90):
    log_step(f"Waiting for ChatGPT input box (timeout: {timeout}s)...")
    selectors = [ "textarea#prompt-textarea", "textarea[data-testid='prompt-textarea']", "textarea[placeholder*='Message']", "textarea[placeholder*='Send a message']", "div[contenteditable='true']", "textarea[data-id='root']" ]
    start_time = time.time(); last_exception = None
    while time.time() < deadline: # Use deadline for clarity
        for selector in selectors:
             try: element = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector))); log_step(f"Found interactable input box using: {selector}", level="success"); return element
             except TimeoutException: continue
             except Exception as e: last_exception = e; log_step(f"Debug: Error checking selector {selector}: {e}",level="debug"); continue
        time.sleep(0.5)
    log_step(f"Input box not found or interactable after {timeout}s. Last error: {last_exception}", level="error"); raise TimeoutException(f"Input box not found after {timeout}s")

def wait_for_response_completion(driver, timeout=CHATGPT_RESPONSE_TIMEOUT):
    log_step("Waiting for response completion...")
    start = time.time(); seen=False
    stop_selector = "button[data-testid='stop-button'], button[aria-label*='Stop generating']"
    send_selector = "button[data-testid='send-button']"
    while time.time()-start < timeout:
        stops = driver.find_elements(By.CSS_SELECTOR, stop_selector)
        if stops and stops[0].is_displayed(): seen=True; time.sleep(0.5); continue
        if seen: log_step("Generation complete (stop button disappeared)", level="success"); return
        try:
             sends = driver.find_elements(By.CSS_SELECTOR, send_selector)
             if sends and sends[0].is_displayed() and sends[0].is_enabled() and time.time()-start > 5: log_step("Generation assumed complete (send button enabled)", level="info"); return
        except: pass
        time.sleep(0.5)
    log_step(f"Timeout ({timeout}s) waiting for response completion.", level="error"); raise TimeoutException("Response did not finish in time")

def send_message(driver, message):
    ib = wait_for_input_box(driver)
    txt = sanitize_for_chromedriver(message)
    log_step(f"Sending message (first 300 chars):\n{txt[:300]}..." + (" (truncated)" if len(txt) > 300 else ""))
    try: driver.execute_script("arguments[0].value='';arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", ib)
    except:
        try: ib.clear()
        except: ib.send_keys(Keys.CONTROL+"a", Keys.DELETE)
    for i,line in enumerate(txt.split("\n")):
        ib.send_keys(line)
        if i < len(txt.split("\n"))-1: ib.send_keys(Keys.SHIFT, Keys.ENTER)
        time.sleep(0.02)
    time.sleep(0.2)
    sent = False
    send_selectors = ["button[data-testid='send-button']", "button[data-testid='fruitjuice-send-button']", "button[aria-label*='Send']"]
    for selector in send_selectors:
         try: btn = WebDriverWait(driver,10).until(EC.element_to_be_clickable((By.CSS_SELECTOR,selector))); btn.click(); sent = True; log_step(f"Clicked send button via selector: {selector}", level="debug"); break
         except: continue
    if not sent:
        log_step("Send button click failed, attempting ENTER key fallback.", level="warning")
        try: ib.send_keys(Keys.ENTER); sent = True
        except Exception as e: log_step(f"ENTER key fallback failed: {e}", level="error"); raise RuntimeError("Failed to send message") from e
    wait_for_response_completion(driver)
    log_step("Message sent and response completed.", level="success")

def get_last_response_text(driver):
    log_step("Attempting to retrieve last response text...")
    try:
        assistant_blocks = driver.find_elements(By.CSS_SELECTOR, "div[data-message-author-role='assistant']")
        if not assistant_blocks: log_step("No assistant message blocks found.", level="warning"); return ""
        last_block = assistant_blocks[-1]
        content_selectors = ["div.markdown", ".text-base", "div.prose", "div.text-token-text-primary"]
        response_text = ""
        for selector in content_selectors:
            try:
                content_elements = last_block.find_elements(By.CSS_SELECTOR, selector)
                if content_elements:
                    current_text = "\n".join(el.text for el in content_elements if el.is_displayed() and el.text).strip()
                    if len(current_text) > len(response_text): response_text = current_text
            except Exception: continue
        if not response_text or len(response_text) < 10: log_step("Could not extract specific content, falling back to full text.", level="warning"); response_text = last_block.text.strip()
        if response_text: log_step("Successfully retrieved last response text.", level="success")
        else: log_step("Response block found but text extraction failed.", level="warning")
        return response_text
    except Exception as e: log_step(f"Error getting last response text: {e}", level="error"); return ""

# --- JSON Helpers ---
def strip_markdown_fences(text):
    return re.sub(r"```(?:[a-zA-Z0-9]*\n)?(.*?)```", r"\1", text, flags=re.DOTALL).strip()

# Using the corrected try_parse_json from previous attempt
def try_parse_json(response_text):
    if not response_text: log_step("Cannot parse JSON: Empty response.", level="warning"); return (False, "Empty response")
    try: parsed = json.loads(response_text); return (True, parsed) # No need for log here, request_json_data logs success
    except json.JSONDecodeError: pass
    clean_text = strip_markdown_fences(response_text)
    if not clean_text: return (False, "Response empty after cleaning markdown.")
    try: return True, json.loads(clean_text)
    except json.JSONDecodeError: pass
    log_step("Direct/Clean parsing failed, attempting substring extraction...", level="debug")
    first_bracket=clean_text.find('['); first_curly=clean_text.find('{');
    start_idx = -1; start_char = None; end_char = None
    if first_curly!=-1 and (first_curly < first_bracket or first_bracket == -1): start_idx=first_curly; start_char='{'; end_char='}'
    elif first_bracket!=-1: start_idx=first_bracket; start_char='['; end_char=']'
    else: return (False, "No JSON object or array start found")
    if start_idx!=-1:
        open_count=0; end_idx=-1; in_string=False; escape=False
        for i in range(start_idx, len(clean_text)):
            char=clean_text[i]
            if char=='"' and not escape: in_string = not in_string
            elif char=='\\' and in_string: escape = not escape
            else: escape = False
            if not in_string:
                if char==start_char: open_count+=1
                elif char==end_char: open_count-=1
            if open_count==0:
                end_idx=i+1
                break
        if end_idx!=-1:
            json_substring=clean_text[start_idx:end_idx].strip()
            try: return True, json.loads(json_substring)
            except json.JSONDecodeError as e:
                context = json_substring[max(0, e.pos-20):e.pos+20]
                return False, f"Substring parse error: {e} near '{context}'"
        else: return False, "Matching bracket/brace not found in substring"
    return False, "JSON parsing failed" # Fallback

def request_json_data(driver, prompt, max_retries=CHATGPT_JSON_RETRIES):
    if "Output ONLY the JSON object" not in prompt: prompt += "\nIMPORTANT: Output ONLY the raw JSON object. No commentary or markdown fences."
    log_step(f"Requesting JSON data (retries={max_retries})...", important=True)
    for attempt in range(max_retries + 1):
        log_step(f"JSON Request Attempt {attempt + 1}/{max_retries + 1}", level="debug")
        time.sleep(0.5)
        send_message(driver, prompt)
        resp = get_last_response_text(driver)
        ok, data_or_error = try_parse_json(resp)
        if ok: log_step("Successfully parsed JSON response.", level="success"); return data_or_error
        log_step(f"JSON parse failed attempt {attempt + 1}. Reason: {data_or_error}", level="warning")
        if attempt < max_retries:
            log_step("Sending JSON fix prompt...", level="info")
            prompt = (f"MODE:JSON\nPrevious response invalid JSON. Error: '{data_or_error}'.\nRegenerate response. Strictly follow schema.\nCRITICAL: Output ONLY raw, valid JSON. No text, explanations, or ```.")
            time.sleep(1)
        else:
            log_step("Max retries reached for JSON parsing.", level="error"); log_step(f"Final failed response text:\n---\n{resp}\n---", level="error"); return None
    return None

# --- User Interaction ---
def get_user_approval(msg):
    while True:
        ans = input(f"{msg} (yes/edit/reject): ").strip().lower()
        feedback = None
        if ans in ("yes", "y"): return "yes", None
        if ans in ("reject", "r"): return "reject", None
        if ans in ("edit", "e"):
            feedback = input("Feedback: ").strip()
            if fb: return "edit", fb
            else: print("Feedback cannot be empty for edit.")
        else: print("Invalid input.")

# --- Character Details Helper ---
def find_character_details(name, chars_data):
    if not isinstance(chars_data, dict): return {}
    for c in chars_data.get("characters", []):
        if isinstance(c, dict) and c.get("name") == name: return c
    return {}

# --- Main ---
def main():
    start = time.time()
    log_step("===== Mythic Movie Generator Started =====", important=True)
    driver=None; project_path=None
    try:
        # --- Init ---
        log_step("--- Phase 1: Initialization ---", important=True)
        topic = input("Enter mythic story topic: ").strip()
        while not topic: topic = input("Topic cannot be empty. Enter topic: ").strip()
        ts = datetime.now().strftime(TIMESTAMP_FORMAT)
        project_folder_name = f"{PROJECT_PREFIX}_{sanitize_name(topic)}_{ts}"
        project_path = PROJECTS_BASE_DIR / project_folder_name
        PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
        project_path.mkdir(parents=True, exist_ok=True)
        log_step(f"Project dir created: {project_path}", level="success")

        # --- WebDriver + Login ---
        log_step("Initializing WebDriver..."); options = uc.ChromeOptions(); options.add_argument("--start-maximized")
        driver = uc.Chrome(options=options); log_step("WebDriver initialized.", level="success")
        log_step(f"Navigating to {CHATGPT_BASE_URL}..."); driver.get(CHATGPT_BASE_URL); time.sleep(4)
        log_step("Attempting to load cookies..."); cookies_loaded = load_cookies(driver, COOKIE_PATH)
        log_step(f"Navigating to target model URL: {CHATGPT_TARGET_MODEL_URL}")
        try: driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(5)
        except WebDriverException as nav_err: log_step(f"Error navigating to target URL: {nav_err}", level="error"); raise
        session_active = False
        try: log_step("Verifying chat interface readiness..."); wait_for_input_box(driver, timeout=60); log_step("Chat interface ready.", level="success"); session_active = True
        except TimeoutException: log_step("Input box not found on target URL.", level="warning"); session_active = False
        if not session_active:
            log_step("Manual login required.", important=True); log_step(f"Navigating back to {CHATGPT_BASE_URL} for login..."); driver.get(CHATGPT_BASE_URL); time.sleep(3)
            print(f"\n>>> Please log in manually ({CHATGPT_LOGIN_TIMEOUT}s timeout). <<<"); input(f">>> Press Enter ONLY after logged in. <<<")
            log_step("User indicated login complete. Saving cookies..."); save_cookies(driver, COOKIE_PATH)
            log_step(f"Re-navigating to target URL: {CHATGPT_TARGET_MODEL_URL}")
            try: driver.get(CHATGPT_TARGET_MODEL_URL); time.sleep(5)
            except WebDriverException as nav_err: log_step(f"Error re-navigating: {nav_err}", level="error"); raise
            wait_for_input_box(driver); log_step("Chat interface ready after manual login.", level="success")

        # --- Send System Prompt ---
        log_step("--- Phase 2: Setting Context (Sending System Prompt) ---", important=True)
        send_message(driver, SYSTEM_PROMPT)
        try:
            # CORRECTED FILE WRITE INDENTATION
            with open(project_path / SYSTEM_PROMPT_LOG_FILENAME, 'w', encoding="utf-8") as f:
                f.write(SYSTEM_PROMPT)
        except Exception as log_e: log_step(f"Warning: Could not log system prompt: {log_e}", level="warning")
        log_step("System prompt sent.", level="success")
        time.sleep(2)

        # --- Story loop ---
        log_step("--- Phase 3: Story Generation & Approval ---", important=True)
        approved_story_text = None; story_prompt_count = 0
        while not approved_story_text:
            story_prompt_count += 1
            if story_prompt_count > MAX_STORY_ATTEMPTS: raise RuntimeError(f"Exceeded max attempts ({MAX_STORY_ATTEMPTS}) for story.")
            log_step(f"Requesting Story Draft {story_prompt_count}/{MAX_STORY_ATTEMPTS}...")
            # Use the improved story prompt
            story_request_prompt = (
                f"MODE:COMMENTARY\n"
                f"You are a professional YouTube storyteller. Write a highly engaging, 2-minute cinematic story draft (approx. 250-300 words) suitable for a viral YouTube video voiceover, based on the topic: \"{topic}\".\n\n"
                f"Follow these mandatory rules for structure and style:\n"
                f"1.  **Hook (≤ 15 words):** Start with a jaw-dropping question or provocative statement to grab attention instantly. (e.g., 'What happens if you shoot a love arrow at the angriest god?').\n"
                f"2.  **Emotional Build-up:** Make the central figure relatable. Show their intentions, the risks they take, or their sacrifice to create empathy.\n"
                f"3.  **Rising Tension:** Build suspense before the key moment or climax. Use dramatic pauses (ellipses... or dashes-).\n"
                f"4.  **Climax / Divine Twist:** Deliver a powerful, unexpected payoff or a divine intervention moment with visual awe.\n"
                f"5.  **Philosophical Message:** End the core story with a modern takeaway or spiritual insight that resonates.\n"
                f"6.  **Viewer CTA:** After the story concludes, add a separate, final line with a thought-provoking question related to the story to encourage comments (e.g., 'Would you have dared to...? Comment below!').\n\n"
                f"**Tone/Style:**\n"
                f"- Cinematic, modern, and engaging.\n"
                f"- Use short, punchy sentences.\n"
                f"- Energetic, emotional, and spiritual vibe (like a movie trailer).\n\n"
                f"**Output Requirement:** Provide ONLY the story text (including the final CTA line), with no introductory phrases like 'Here is the draft...'. Ensure it flows well when read aloud."
            )
            draft = None
            try: send_message(driver, story_request_prompt); draft = get_last_response_text(driver)
            except Exception as e:
                log_step(f"Error getting story draft: {e}", level="error")
                if story_prompt_count >= MAX_STORY_ATTEMPTS: raise RuntimeError("Failed to get story draft.") from e
                log_step("Retrying story request...", level="warning"); time.sleep(5); continue
            if not draft:
                log_step("Empty story draft received, retrying...", level="warning")
                if story_prompt_count >= MAX_STORY_ATTEMPTS: raise RuntimeError("Failed to get story draft (empty).")
                time.sleep(5); continue
            print("\n" + "="*20 + " DRAFT " + "="*20 + "\n" + draft + "\n" + "="*54)
            stat, fb = get_user_approval("Approve this draft?")
            if stat=="yes":
                 approved_story_text = draft
                 send_message(driver, f"MODE:COMMENTARY\nStory draft approved. Proceeding.")
                 time.sleep(1)
            elif stat=="edit": send_message(driver, f"MODE:COMMENTARY\nRevise based on feedback: \"{fb}\". Provide ONLY revised text.")
            else: send_message(driver, f"MODE:COMMENTARY\nDraft rejected. Write a *different* draft on \"{topic}\". Provide ONLY story text.")
        # CORRECTED FILE WRITE INDENTATION
        try:
            with open(project_path / STORY_FILENAME, 'w', encoding="utf-8") as f:
                 f.write(approved_story_text)
            log_step(f"Approved story saved: {STORY_FILENAME}", level="success")
        except IOError as e: log_step(f"Error saving approved story file: {e}", level="error")


        # --- Characters ---
        log_step("--- Phase 4: Character Generation ---", important=True)
        CHAR_SCHEMA_EXAMPLE = '{"characters": [{"name": "Name", "description": "Visual details", "visual_tags": ["tag1", "tag2"]}]}' # Simplified example
        char_prompt = ( f"MODE:JSON\nBased *only* on the approved story, identify key characters. " f"Provide 'name', 'description', and 'visual_tags' for each. Use schema like: `{CHAR_SCHEMA_EXAMPLE}`" )
        chars_data = request_json_data(driver, char_prompt)
        if not chars_data: raise RuntimeError("Failed to get Character JSON")
        if not isinstance(chars_data, dict) or "characters" not in chars_data:
            if isinstance(chars_data, list): chars_data = {"characters": chars_data}
            else: chars_data = {"characters": []}; log_step("Character data invalid, using empty.", level="error")
        # CORRECTED FILE WRITE INDENTATION
        try:
             with open(project_path/CHARACTERS_FILENAME, 'w', encoding="utf-8") as f:
                  json.dump(chars_data, f, indent=2, ensure_ascii=False)
             log_step(f"Characters saved: {CHARACTERS_FILENAME}", level="success")
        except Exception as e: log_step(f"Error saving character JSON: {e}", level="error")


        # --- Scenes ---
        log_step("--- Phase 5: Scene Breakdown ---", important=True)
        names = [c.get("name") for c in chars_data.get("characters",[]) if c.get("name")]
        SCENE_SCHEMA_EXAMPLE = '{"scenes": [{"scene_number": N, "start_time": N, "end_time": N, "scene_description": "...", "focus_character": "...", "background": "...", "action": "...", "dialogue": "...", "narration": "..."}]}' # Simplified example
        scene_prompt = ( f"MODE:JSON\nUsing approved story & chars {names}, break into exactly 24 scenes (~5s). Use schema like: `{SCENE_SCHEMA_EXAMPLE}`" )
        scenes_data = request_json_data(driver, scene_prompt)
        if not scenes_data: raise RuntimeError("Failed to get Scene JSON")
        if not isinstance(scenes_data, dict) or "scenes" not in scenes_data or not isinstance(scenes_data["scenes"], list): raise RuntimeError("Scene JSON structure invalid.")
        scene_list = scenes_data.get("scenes", [])
        if len(scene_list) != 24: log_step(f"Warning: Received {len(scene_list)} scenes, expected 24.", level="warning")
        # CORRECTED FILE WRITE INDENTATION
        try:
            with open(project_path/SCENES_FILENAME, 'w', encoding="utf-8") as f:
                 json.dump(scenes_data, f, indent=2, ensure_ascii=False)
            log_step(f"Scenes saved: {SCENES_FILENAME}", level="success")
        except Exception as e: log_step(f"Error saving scene JSON: {e}", level="error")


        # --- Per-scene prompts ---
        log_step("--- Phase 6: Generating Prompts per Scene ---", important=True)
        img_prompts, elems_data, vid_prompts = [], [], []
        if not scene_list: log_step("Scene list empty, skipping prompts.", level="warning")
        else:
            IMAGE_SCHEMA_LOOP = '{"scene_number":N,"prompt_text":"..."}' # Simplified
            ELEMENT_SCHEMA_LOOP = '{"scene_number":N,"sound_effects":[],"visual_effects":[],"background_music":"..."}'
            VIDEO_SCHEMA_LOOP = '{"scene_number":N,"video_prompt":"..."}'

            for i, scene in enumerate(scene_list):
                 if not isinstance(scene, dict): log_step(f"Skipping invalid scene data index {i}", level="warning"); continue
                 num = scene.get("scene_number", i + 1); log_step(f"Processing Scene {num}/{len(scene_list)}...")
                 char = scene.get("focus_character"); details = find_character_details(char, chars_data)
                 # Image Prompt
                 iprompt = (f"MODE:JSON\nScene {num}: Desc=\"{scene.get('scene_description','')}\" Focus=\"{char or 'Env'}\" Action=\"{scene.get('action','')}\" BG=\"{scene.get('background','')}\" CharDet:{json.dumps(details, default=str)}\n"
                            f"Gen detailed image prompt. Use schema: `{IMAGE_SCHEMA_LOOP}`")
                 ipj = request_json_data(driver, iprompt)
                 img_prompts.append(ipj if isinstance(ipj, dict) else {"scene_number": num, "prompt_text": "ERROR"})
                 if not isinstance(ipj, dict): log_step(f"Error: Image Prompt Sc {num}", level="warning")
                 # Elements
                 eprompt = (f"MODE:JSON\nScene {num}: Desc=\"{scene.get('scene_description','')}\" Action=\"{scene.get('action','')}\"\n"
                            f"Identify SFX, VFX, BGM keywords. Use schema: `{ELEMENT_SCHEMA_LOOP}`")
                 epj = request_json_data(driver, eprompt)
                 elems_data.append(epj if isinstance(epj, dict) else {"scene_number": num, "sfx": [], "vfx": [], "bgm": "ERROR"})
                 if not isinstance(epj, dict): log_step(f"Error: Elements Sc {num}", level="warning")
                 # Video Prompt
                 current_ip_text = img_prompts[-1].get("prompt_text", "ERROR")
                 current_elems = elems_data[-1]
                 vprompt = (f"MODE:JSON\nScene {num}: ImgIdea=\"{current_ip_text}\" Elems:{json.dumps(current_elems, default=str)} Action=\"{scene.get('action','')}\"\n"
                            f"Gen video prompt (5s, 16:9) for motion/action/effects. Use schema: `{VIDEO_SCHEMA_LOOP}`")
                 vpj = request_json_data(driver, vprompt)
                 vid_prompts.append(vpj if isinstance(vpj, dict) else {"scene_number": num, "video_prompt": "ERROR"})
                 if not isinstance(vpj, dict): log_step(f"Error: Video Prompt Sc {num}", level="warning")
                 time.sleep(1)

            log_step("Saving aggregated prompt data...")
            # CORRECTED FILE WRITE INDENTATION
            try:
                with open(project_path/IMAGE_PROMPTS_FILENAME, 'w', encoding="utf-8") as f: json.dump(img_prompts, f, indent=2, ensure_ascii=False)
                with open(project_path/ELEMENTS_FILENAME, 'w', encoding="utf-8") as f: json.dump(elems_data, f, indent=2, ensure_ascii=False)
                with open(project_path/VIDEO_PROMPTS_FILENAME, 'w', encoding="utf-8") as f: json.dump(vid_prompts, f, indent=2, ensure_ascii=False)
                log_step("Prompt files saved.", level="success")
            except Exception as e: log_step(f"Error saving prompt files: {e}", level="error")


        # --- Timeline ---
        log_step("--- Phase 7: Generating Timeline ---", important=True)
        if not scene_list: log_step("Scene list empty, cannot generate timeline.", level="warning")
        else:
            TIMELINE_SCHEMA_EXAMPLE = '{"timeline": [{"scene_number":N,"start_time":N,"end_time":N,"narration_words":[],"image":"...","video":"..."}]}' # Simplified example
            tm_prompt = (f"MODE:JSON\nUsing {len(scene_list)} scenes generated, create final timeline. Use schema like: `{TIMELINE_SCHEMA_EXAMPLE}`")
            tm_data = request_json_data(driver, tm_prompt)
            if not tm_data: log_step("Failed to get Timeline JSON", level="error")
            elif not isinstance(tm_data, dict) or "timeline" not in tm_data: log_step("Timeline JSON structure invalid", level="error")
            else:
                 # CORRECTED FILE WRITE INDENTATION
                try:
                    with open(project_path / TIMELINE_FILENAME, 'w', encoding="utf-8") as f:
                        json.dump(tm_data, f, indent=2, ensure_ascii=False)
                    log_step(f"Timeline saved: {TIMELINE_FILENAME}", level="success")
                except Exception as e: log_step(f"Error saving timeline JSON: {e}", level="error")

        # --- Final Confirmation ---
        log_step("--- Phase 8: Finalizing ---", important=True)
        try: send_message(driver, f"MODE:COMMENTARY\nAll planning steps complete for topic '{topic}'. Thank you.")
        except Exception as final_e: log_step(f"Minor error sending final msg: {final_e}", level="warning")
        log_step(f"Finished! Files generated in {project_path}", level="success", important=True)

    # --- Exception Handling & Cleanup ---
    except KeyboardInterrupt: log_step("Process interrupted by user.", level="warning", important=True)
    except TimeoutException as e: log_step(f"Operation timed out: {e}", level="error", important=True); log.error(f"Timeout details: {e.msg}")
    except WebDriverException as e: log_step(f"WebDriver error: {e.msg}", level="error", important=True); log_step("Browser closed or driver mismatch?", level="error")
    except RuntimeError as e: log_step(f"Script runtime error: {e}", level="error", important=True); log.error(traceback.format_exc())
    except Exception as e: log_step(f"An unexpected critical error occurred: {e}", level="error", important=True); log.error(traceback.format_exc())
    finally:
        log_step("--- Phase 9: Cleanup ---", important=True)
        if driver:
            log_step("Attempting to close WebDriver...");
            try: driver.quit(); log_step("WebDriver closed.", level="success")
            except Exception as e: log_step(f"Error closing WebDriver: {e}", level="warning")
        else: log_step("WebDriver not initialized.")
        end = time.time(); log_step(f"===== Mythic Movie Generator Finished in {end-start:.2f} seconds =====", important=True)
        if project_path and project_path.exists(): log_step(f"Project Path: {project_path}")
        else: log_step("Project path not created or inaccessible.")

if __name__ == "__main__":
    main()
    input("\n<<< Process complete. Press Enter to exit... >>>")
    sys.exit(0)