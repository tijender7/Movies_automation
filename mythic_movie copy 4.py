import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException
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
    s = re.sub(r'[<>:"/\\|?*']', "_", str(name))
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
    domain = None
    for c in cookies:
        dom = c.get("domain","...").lstrip('.')
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

# --- Remaining Logic ---
# All steps remain same until after characters.json is generated
# After scene breakdown, generate ONLY image prompts per scene
# Skip video_prompt and elements (SFX/VFX/BGM) sections
# This simplifies asset generation and keeps it image-focused only
