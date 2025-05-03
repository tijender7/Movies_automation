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

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
PROJECT_BASE_FOLDER_NAME = "mythic_movie_project"
PROJECT_PREFIX = "mythic_movie"
LOG_FILE_NAME = f"mythic_movie_log_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"

# --- Dynamic Paths ---
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECTS_BASE_DIR = SCRIPT_DIR / PROJECT_BASE_FOLDER_NAME
COOKIE_PATH = SCRIPT_DIR / "chatgpt_cookies.json"
DOTENV_PATH = SCRIPT_DIR / ".env"
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / LOG_FILE_NAME

# --- Output Filenames ---
STORY_FILENAME = "story_approved.txt"
CHARACTERS_FILENAME = "characters.json"
SCENES_FILENAME = "scenes.json"
IMAGE_PROMPTS_FILENAME = "image_prompts.json"
ELEMENTS_FILENAME = "elements.json"
VIDEO_PROMPTS_FILENAME = "video_prompts.json"
TIMELINE_FILENAME = "timeline.json"

# --- ChatGPT Settings ---
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o"
CHATGPT_LOGIN_TIMEOUT = 120
CHATGPT_RESPONSE_TIMEOUT = 300
CHATGPT_JSON_RETRIES = 2

# --- Load Environment Variables ---
if DOTENV_PATH.is_file():
    load_dotenv(dotenv_path=DOTENV_PATH)
else:
    print(f"[!] .env not found at {DOTENV_PATH}. Continuing without it.")

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
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("selenium").setLevel(logging.WARNING)
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
logging.getLogger("tensorflow").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# --- Helper: log_step ---
def log_step(message, level="info", important=False):
    prefix_map = {"info":"[*]","warning":"[!]","error":"[X]","success":"[+]","debug":"[D]"}
    final = f"{prefix_map.get(level,'[?]')} {message}"
    if important:
        final = f"--- {final} ---"
    if level == "error":
        log.error(final)
    elif level == "warning":
        log.warning(final)
    elif level == "debug":
        log.debug(final)
    else:
        log.info(final)

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
    try:
        cookies = driver.get_cookies()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        log_step("Cookies saved", level="success")
    except Exception as e:
        log_step(f"Error saving cookies: {e}", level="error")

def load_cookies(driver, path):
    if not path.exists():
        log_step("Cookie file not found", level="info")
        return False
    log_step(f"Loading cookies from: {path}")
    try:
        cookies = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log_step(f"Error reading cookie file: {e}", level="error")
        return False
    # auto-navigate
    domain = None
    for c in cookies:
        dom = c.get("domain","").lstrip('.')
        if "openai.com" in dom or "chatgpt.com" in dom:
            domain = f"https://{dom}"
            break
    if domain:
        try:
            driver.get(domain); time.sleep(2)
        except:
            log_step(f"Warning: could not load domain {domain}", level="warning")
    added, skipped = 0, 0
    for c in cookies:
        if not isinstance(c, dict) or "name" not in c or "value" not in c:
            skipped += 1; continue
        if 'sameSite' in c and c['sameSite'] not in ['Lax','Strict','None']:
            del c['sameSite']
        if 'expiry' in c and isinstance(c['expiry'], float):
            c['expiry'] = int(c['expiry'])
        try:
            driver.add_cookie(c); added += 1
        except:
            skipped += 1
    log_step(f"Cookies loaded: {added}, skipped: {skipped}", level="success")
    return True

# --- Selenium Helpers ---
def wait_for_input_box(driver, timeout=60):
    log_step("Waiting for ChatGPT input box...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ta = driver.find_element(By.CSS_SELECTOR, "textarea#prompt-textarea, textarea[data-testid='prompt-textarea']")
            if ta.is_displayed() and ta.is_enabled():
                log_step("Found textarea", level="success"); return ta
        except: pass
        try:
            div = driver.find_element(By.CSS_SELECTOR, "div[contenteditable='true']")
            if div.is_displayed():
                log_step("Found contenteditable div", level="success"); return div
        except: pass
        time.sleep(0.5)
    raise TimeoutException("Input box not found")

def wait_for_response_completion(driver, timeout=CHATGPT_RESPONSE_TIMEOUT):
    log_step("Waiting for response completion...")
    start = time.time(); seen=False
    while time.time()-start < timeout:
        stops = driver.find_elements(By.CSS_SELECTOR, "button[data-testid='stop-button'], button[aria-label*='Stop generating']")
        if stops and stops[0].is_displayed(): seen=True; time.sleep(0.5); continue
        if seen:
            log_step("Generation complete", level="success"); return
        sends = driver.find_elements(By.CSS_SELECTOR, "button[data-testid='send-button']")
        if sends and sends[0].is_displayed() and sends[0].is_enabled() and time.time()-start>5:
            return
        time.sleep(0.5)
    raise TimeoutException("Response did not finish in time")

def send_message(driver, message):
    ib = wait_for_input_box(driver)
    txt = sanitize_for_chromedriver(message)
    try: driver.execute_script("arguments[0].value='';arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", ib)
    except:
        try: ib.clear()
        except: ib.send_keys(Keys.CONTROL+"a", Keys.DELETE)
    for i,line in enumerate(txt.split("\n")):
        ib.send_keys(line)
        if i < len(txt.split("\n"))-1: ib.send_keys(Keys.SHIFT, Keys.ENTER)
        time.sleep(0.02)
    time.sleep(0.2)
    try:
        btn = WebDriverWait(driver,10).until(EC.element_to_be_clickable((By.CSS_SELECTOR,"button[data-testid='send-button']")))
        btn.click()
    except:
        ib.send_keys(Keys.ENTER)
    wait_for_response_completion(driver)

def get_last_response_text(driver):
    blocks = driver.find_elements(By.CSS_SELECTOR,"div[data-message-author-role='assistant']")
    if not blocks: return ""
    last = blocks[-1]
    for sel in ["div.markdown","div.text-token-text-primary","div[role='region']"]:
        try:
            elems = last.find_elements(By.CSS_SELECTOR,sel)
            if elems:
                t = "\n".join(el.text for el in elems if el.text).strip()
                if len(t)>10: return t
        except: pass
    return last.text.strip()

# --- JSON Helpers ---
def strip_markdown_fences(text):
    return re.sub(r"```(?:[a-zA-Z0-9]*\n)?(.*?)```", r"\1", text, flags=re.DOTALL).strip()

def try_parse_json(resp):
    if not resp: return False, "Empty"
    try: return True, json.loads(resp)
    except:
        clean = strip_markdown_fences(resp)
        for start,end in [("{","}"),("[","]")]:
            if clean.startswith(start):
                depth=0
                for i,ch in enumerate(clean):
                    if ch==start: depth+=1
                    elif ch==end: depth-=1
                    if depth==0:
                        sub=clean[:i+1]
                        try: return True, json.loads(sub)
                        except Exception as e: return False, str(e)
        return False, "No JSON"

def request_json_data(driver, prompt, max_retries=CHATGPT_JSON_RETRIES):
    if "ONLY the raw JSON" not in prompt:
        prompt += "\nIMPORTANT: Output ONLY the raw JSON object. No extra text."
    for attempt in range(max_retries+1):
        send_message(driver, prompt)
        resp = get_last_response_text(driver)
        ok,data = try_parse_json(resp)
        if ok: return data
        if attempt < max_retries:
            prompt = f"Invalid JSON ({data}). Please resend ONLY the valid JSON object."
    log_step("Failed to parse JSON after retries", level="error")
    return None

# --- User & Character Helpers ---
def get_user_approval(msg):
    """Prompt user to approve, edit, or reject. Returns tuple(status, feedback)."""
    while True:
        ans = input(f"{msg} (yes/edit/reject): ").strip().lower()
        if ans in ("yes", "y"):
            return "yes", None
        if ans in ("edit", "e"):
            fb = input("Feedback: ").strip()
            if fb:
                return "edit", fb
            else:
                print("Feedback cannot be empty for edit.")
        if ans in ("reject", "r"):
            return "reject", None

def find_character_details(name, chars):
    for c in chars.get("characters",[]):
        if c.get("name")==name: return c
    return {}

# --- Main ---
def main():
    start = time.time()
    log_step("===== Mythic Movie Generator Started =====", important=True)
    driver=None; project_path=None
    try:
        topic = input("Enter mythic story topic: ").strip()
        while not topic: topic = input("Topic cannot be empty. Enter topic: ").strip()
        ts = datetime.now().strftime(TIMESTAMP_FORMAT)
        project_path = PROJECTS_BASE_DIR / f"{PROJECT_PREFIX}_{ts}"
        PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
        project_path.mkdir(parents=True, exist_ok=True)
        log_step(f"Project dir: {project_path}", level="success")

        options = uc.ChromeOptions(); options.add_argument("--start-maximized")
        driver = uc.Chrome(options=options)
        driver.get(CHATGPT_BASE_URL); time.sleep(4)
        loaded = load_cookies(driver, COOKIE_PATH)
        if loaded: driver.refresh(); time.sleep(3)

        driver.get(CHATGPT_TARGET_MODEL_URL)
        try: wait_for_input_box(driver, timeout=60)
        except TimeoutException:
            print(f"Please log in manually within {CHATGPT_LOGIN_TIMEOUT}s, then press Enter.")
            input(); save_cookies(driver, COOKIE_PATH)
            driver.get(CHATGPT_TARGET_MODEL_URL); wait_for_input_box(driver)

        # Send master system prompt
        SYSTEM_PROMPT = (
            "SYSTEM: You are a world-class storyteller and video-script assistant. "
            "Every response must follow the MODE tags and JSON schemas I provide, "
            "and never include any extra commentary or markdown outside of those modes."
        )
        send_message(driver, SYSTEM_PROMPT)
        log_step("Master system prompt sent.", level="debug")

        # Story generation loop
        language = "English"  # or set dynamically as needed
        approved = None
        while not approved:
            # Build the HARD RULES story prompt template using a triple-quoted f-string
            story_prompt = f"""
MODE:COMMENTARY
You are a professional YouTube shorts scriptwriter.
I will pass you one SHORT NOTE or TOPIC.
Write a 2-minute mythic story script that people will watch start-to-finish.
HARD RULES (follow all):
1️⃣ Word count: 240–280 words.
2️⃣ Language: {language}. Use engaging, modern tone. If 'Hinglish', mix Hindi+English naturally.
3️⃣ Structure (label each beat):
    [HOOK] – ≤ 15 words, question or shocking fact.
    [SET-UP] – Build empathy for main character.
    [RISING TENSION] – Describe risk; escalate suspense.
    [CLIMAX / DIVINE TWIST] – Wow moment or reveal.
    [MESSAGE] – Modern takeaway or philosophical punch.
    [CTA] – 1 line: viewer-decision question + 'Like/Share/Subscribe'.
4️⃣ Sensory cues: sprinkle optional [VISUAL: …], [SFX: …], [MUSIC: …].
5️⃣ Keep sentences punchy (max ~18 words). Use dramatic pauses (ellipses or dashes).
6️⃣ Absolutely no extra commentary—return only the labeled script.

INPUT NOTE → "{topic}"
"""
            send_message(driver, story_prompt)
            draft = get_last_response_text(driver)
            print("
" + "="*20 + " DRAFT " + "="*20 + "
" + draft + "
" + "="*54)
            stat, fb = get_user_approval("Approve this draft?")
            if stat == "yes":
                approved = draft
            elif stat == "edit":
                send_message(driver, f"MODE:COMMENTARY
Revise based on feedback: '{fb}' using the same HARD RULES template.")
            else:
                send_message(driver, f"MODE:COMMENTARY
Write a different 2-minute mythic story on '{topic}' using the HARD RULES template.")
        # Save approved story
        (project_path / STORY_FILENAME).write_text(approved, encoding="utf-8")
# Save approved story
        (project_path / STORY_FILENAME).write_text(approved, encoding="utf-8")

        # Character generation
        CHAR_SCHEMA = """
    {
      "character_generation_agent": [
        { "name": "string", "description": "string" }
      ]
    }
        """
        char_prompt = (
            "MODE:JSON\nBased *only* on the approved story, identify key characters with visual descriptions.\n"
            "Use the following JSON schema exactly:\n```json\n" + CHAR_SCHEMA.strip() + "\n```\n"
            "Output ONLY the JSON object."
        )
        chars = request_json_data(driver, char_prompt) or {}
        (project_path / CHARACTERS_FILENAME).write_text(json.dumps(chars, indent=2, ensure_ascii=False), encoding="utf-8")

        # Scene breakdown
        names = [c.get("name") for c in chars.get("characters",[])]
        SCENE_SCHEMA = """
    {
      "scene_planner_agent": [
        { "scene_number": number, "start_time": "string", "end_time": "string", "scene_description": "string", "focus_character": "string", "background": "string", "action": "string", "dialogue": "string", "narration": "string" }
      ]
    }
        """
        scene_prompt = (
            "MODE:JSON\nUsing the approved story and characters " + str(names) + ", break into exactly 24 scenes (5s each).\n"
            "Use this JSON schema exactly:\n```json\n" + SCENE_SCHEMA.strip() + "\n```\n"
            "Output ONLY the JSON."
        )
        scenes = request_json_data(driver, scene_prompt) or {}
        (project_path / SCENES_FILENAME).write_text(json.dumps(scenes, indent=2, ensure_ascii=False), encoding="utf-8")
        scene_list = scenes.get("scenes", [])

        # Per-scene prompts
        img_prompts, elems, vid_prompts = [], [], []
        IMAGE_SCHEMA = """
    { "image_prompt_gen": [ { "scene_number": number, "prompt_text": "string" } ] }
        """
        ELEMENT_SCHEMA = """
    { "element_detector": [ { "scene_number": number, "sfx": ["string"], "vfx": ["string"], "bgm": "string" } ] }
        """
        VIDEO_SCHEMA = """
    { "video_prompt_gen": [ { "scene_number": number, "video_prompt": "string" } ] }
        """
        for scene in scene_list:
            num = scene.get("scene_number")
            details = find_character_details(scene.get("focus_character"), chars)
            iprompt = (
                f"MODE:JSON\nFor Scene {num}: Desc:\"{scene.get('scene_description','')}\" Focus:\"{scene.get('focus_character','')}\" CharDet:{json.dumps(details)}\n"
                "Use this schema:\n```json\n" + IMAGE_SCHEMA.strip() + "\n```"
            )
            ipj = request_json_data(driver, iprompt) or {}
            img_prompts.append(ipj)
            eprompt = (
                f"MODE:JSON\nFor Scene {num}: Desc:\"{scene.get('scene_description','')}\" Identify SFX, VFX, BGM."
                " Use this schema:\n```json\n" + ELEMENT_SCHEMA.strip() + "\n```"
            )
            epj = request_json_data(driver, eprompt) or {}
            elems.append(epj)
            vprompt = (
                f"MODE:JSON\nFor Scene {num}: ImgPrompt:\"{ipj.get('prompt_text','')}\" Elems:{json.dumps(epj)}"
                " Use this schema:\n```json\n" + VIDEO_SCHEMA.strip() + "\n```"
            )
            vpj = request_json_data(driver, vprompt) or {}
            vid_prompts.append(vpj)
            time.sleep(1)
        (project_path / IMAGE_PROMPTS_FILENAME).write_text(json.dumps(img_prompts, indent=2, ensure_ascii=False), encoding="utf-8")
        (project_path / ELEMENTS_FILENAME).write_text(json.dumps(elems, indent=2, ensure_ascii=False), encoding="utf-8")
        (project_path / VIDEO_PROMPTS_FILENAME).write_text(json.dumps(vid_prompts, indent=2, ensure_ascii=False), encoding="utf-8")

        # Timeline
        TIMELINE_SCHEMA = """
    { "timeline_gen": [ { "scene_number": number, "start": "string", "end": "string", "narration_words": ["string"], "file": "string" } ] }
        """
        tm_prompt = (
            "MODE:JSON\nUsing the above scenes, generate a final timeline with scene numbers, timings, narration words, filenames.\n"
            "Use this schema:\n```json\n" + TIMELINE_SCHEMA.strip() + "\n```\n"
            "Output ONLY the JSON."
        )
        timeline = request_json_data(driver, tm_prompt) or {}
        (project_path / TIMELINE_FILENAME).write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")

        send_message(driver, f"MODE:COMMENTARY\nAll done for '{topic}'.")
        log_step(f"Finished! Files in {project_path}", level="success")
    except Exception as e:
        log_step(f"Fatal error: {e}", level="error", important=True)
    finally:
        if driver:
            try: driver.quit()
            except: pass
        log_step(f"Total time: {time.time()-start:.2f}s", important=True)

if __name__ == "__main__":
    main()
    input("Press Enter to exit...")
    sys.exit(0)
