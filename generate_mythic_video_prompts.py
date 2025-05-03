
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    StaleElementReferenceException,
    ElementClickInterceptedException
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import time
import json
import os
import sys
import platform
import re
from pathlib import Path
from datetime import datetime
import logging
import argparse
import random
import traceback
from dotenv import load_dotenv

# --- Platform Specific Clipboard Handling ---
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    try:
        import win32clipboard
        from PIL import Image
        import io
        print("[*] Successfully imported pywin32 & Pillow. Image pasting enabled.")
    except ImportError:
        print("[!] Warning: pywin32 or Pillow not installed. Image pasting will FAIL.")
        print("    Install: pip install pywin32 Pillow")
        IS_WINDOWS = False
else:
    print("[*] Not on Windows. Clipboard image pasting may not work.")
    IS_WINDOWS = False

# --- Import project config ---
try:
    import config_mythic as config
except ImportError:
    print("ERROR: Failed to import config_mythic.py. Ensure it exists.")
    sys.exit(1)

# --- Constants ---
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
DOTENV_PATH = config.DOTENV_PATH
LOG_FILE = config.LOG_DIR / f"mythic_video_prompts_{datetime.now().strftime(TIMESTAMP_FORMAT)}.log"
PROJECTS_BASE_DIR = config.PROJECTS_BASE_DIR
APPROVED_IMAGES_FOLDER_NAME = config.MYTHIC_APPROVED_FOLDER_NAME
STORY_FILENAME = getattr(config, 'STORY_FILENAME', "story_approved.txt")
SCENES_FILENAME = getattr(config, 'SCENES_FILENAME', "scenes_batched.json")
VIDEO_PROMPTS_FOLDER_NAME = config.VIDEO_PROMPTS_FOLDER_NAME
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json"
COOKIE_PATH = getattr(
    config,
    'COOKIE_PATH',
    Path(r"H:\projects\Movie_trailer\movie_vignette_generator\chatgpt_cookies.json"),
)

# --- FIXED COMPREHENSIVE NEGATIVE PROMPT ---
FIXED_NEGATIVE_PROMPT = (
    "static image, motionless, frozen pose, no movement, stiff character, rigid clothes,"
    " stuck hair, inactive background, boring, plain, bad anatomy, deformed hands, extra fingers,"
    " missing fingers, fused fingers, badly drawn hands, deformed face, badly drawn face,"
    " ugly face, deformed limbs, extra limbs, three legs, blurry, low quality, worst quality, jpeg artifacts,"
    " noisy, grain, watermark, text, signature, words, letters, subtitles, jerky motion, stuttering animation,"
    " low frame rate, weird camera angle, ugly, disfigured, mutated, plastic look, cartoonish, illustration,"
    " drawing, painting, sketch, artwork, 3D render, MMD, SFM, Blender, Unity, Unreal, CGI, video game look,"
    " gaudy colors, oversaturated colors, overexposed, underexposed, washed out colors, overall greyish,"
    " messy background, cluttered background, too many people in background, walking backwards"
)

# --- ChatGPT Settings ---
CHATGPT_BASE_URL = "https://chat.openai.com"
CHATGPT_TARGET_MODEL_URL = "https://chatgpt.com/?model=gpt-4o"
CHATGPT_LOGIN_TIMEOUT = 120
CHATGPT_RESPONSE_TIMEOUT = 300
CHATGPT_JSON_RETRIES = 3
IMAGE_PASTE_WAIT_TIMEOUT = 45
IMAGE_PASTE_CHECK_INTERVAL = 0.5
MAX_STORY_SUMMARY_WORDS = 300
GET_TEXT_DELAY = 1.5

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, mode='a', encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )
    for lib in ('urllib3', 'selenium', 'undetected_chromedriver'):
        logging.getLogger(lib).setLevel(logging.WARNING)
    tf_logger = logging.getLogger('tensorflow')
    if tf_logger:
        tf_logger.setLevel(logging.ERROR)
except Exception as e:
    print(f"ERROR setting up logging: {e}")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[logging.StreamHandler()],
    )

log = logging.getLogger(__name__)

# --- Helper: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix = {
        'info': '[*]',
        'warning': '[!]',
        'error': '[X]',
        'success': '[+]',
        'debug': '[D]',
    }.get(level, '[?]')
    msg = f"{prefix} {message}"
    if important:
        msg = f"--- {msg} ---"
    log.log(getattr(logging, level.upper(), logging.INFO), msg)

# --- Helpers: Name Sanitization ---
def sanitize_name(name):
    name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name)
    sanitized = ''.join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-'))
    sanitized = sanitized.strip().replace(' ', '_')
    sanitized = re.sub(r'[_-]+', '_', sanitized).strip('_-')
    return sanitized[:60] if len(sanitized) > 60 else sanitized or 'invalid_name'

def sanitize_for_chromedriver(text):
    return text if not isinstance(text, str) else re.sub(r'[^\u0000-\uFFFF]', '', text)

# --- Find Latest Project Directory ---
def find_latest_project_dir(base_dir):
    log_step(f"Searching for latest project dir in {base_dir}", level='debug')
    latest_dir = None
    latest_time = 0
    try:
        base = Path(base_dir)
        if not base.is_dir():
            log_step(f"Base project directory not found: {base_dir}", level='error')
            return None
        subs = [d for d in base.iterdir() if d.is_dir() and d.name.startswith('mythic_movie_')]
        for d in subs:
            try:
                m = d.stat().st_mtime
                if m > latest_time:
                    latest_time = m
                    latest_dir = d
            except OSError as e:
                log_step(f"Cannot stat {d.name}: {e}", level='warning')
    except Exception as e:
        log_step(f"Error searching latest project: {e}", level='error')
        return None
    if latest_dir:
        log_step(f"Found latest project: {latest_dir.name}", level='success')
    return latest_dir

# --- Clipboard Helper ---
def copy_image_to_clipboard(path):
    if not IS_WINDOWS:
        log_step("Clipboard paste only on Windows.", level='error')
        return False
    p = Path(path)
    if not p.is_file():
        log_step(f"Image not found: {path}", level='error')
        return False
    try:
        image = Image.open(p)
        if image.mode != 'RGB':
            image = image.convert('RGB')
        buf = io.BytesIO()
        image.save(buf, 'BMP')
        data = buf.getvalue()[14:]
        buf.close()
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log_step(f"Failed to copy {p.name}: {e}", level='error')
        try:
            win32clipboard.CloseClipboard()
        except:
            pass
        return False

# --- Cookie Management ---
def save_cookies(driver, path):
    log_step(f"Saving cookies to {path}")
    try:
        ck = driver.get_cookies()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(ck, f, indent=2)
        log_step("Cookies saved.", level='success')
    except Exception as e:
        log_step(f"Error saving cookies: {e}", level='error')

def load_cookies(driver, path):
    p = Path(path)
    if not p.exists():
        log_step("No cookie file.", level='info')
        return False
    try:
        with open(p, 'r', encoding='utf-8') as f:
            cks = json.load(f)
        driver.get(CHATGPT_BASE_URL)
        time.sleep(3)
        loaded, skipped = 0, 0
        for c in cks:
            if not isinstance(c, dict) or 'name' not in c:
                skipped += 1
                continue
            if c.get('sameSite') not in ('Lax','Strict','None'):
                c['sameSite']='Lax'
            if c.get('sameSite')=='None' and 'secure' not in c:
                c['secure']=True
            if 'expiry' in c and isinstance(c['expiry'], float):
                c['expiry']=int(c['expiry'])
            try:
                driver.add_cookie(c)
                loaded +=1
            except Exception:
                skipped +=1
        log_step(f"Loaded cookies: {loaded}, skipped: {skipped}", level='success')
        driver.refresh()
        time.sleep(5)
        driver.get(CHATGPT_TARGET_MODEL_URL)
        time.sleep(4)
        return True
    except Exception as e:
        log_step(f"Error loading cookies: {e}", level='error')
        return False

# --- Selenium Helpers ---
def wait_for_input_box(driver, timeout=60):
    log_step("Waiting for input box...")
    start = time.time()
    sels = ['textarea#prompt-textarea', "textarea[data-testid='prompt-textarea']", 'div[contenteditable="true"]']
    while time.time() - start < timeout:
        for sel in sels:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if el.is_displayed():
                        ready = (el.tag_name=='textarea' and el.is_enabled()) or el.tag_name=='div'
                        if ready:
                            return el
            except Exception:
                pass
        time.sleep(0.5)
    raise TimeoutException("Input box not found.")

def wait_for_response_completion(driver, timeout=CHATGPT_RESPONSE_TIMEOUT):
    log_step("Waiting for response...")
    start = time.time()
    gen_started = False
    while time.time() - start < timeout:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR,
                "button[aria-label*='Stop generating'], button[data-testid='stop-button']")
            running = any(b.is_displayed() for b in btns)
        except Exception:
            running = False
        if running and not gen_started:
            gen_started = True
        if gen_started and not running:
            time.sleep(GET_TEXT_DELAY)
            return
        time.sleep(0.5)
    raise TimeoutException("Response not completed.")

def send_text_message(driver, text):
    box = wait_for_input_box(driver)
    clean = sanitize_for_chromedriver(text)
    driver.execute_script("arguments[0].value = '';", box)
    for line in clean.split('\n'):
        box.send_keys(line)
        box.send_keys(Keys.SHIFT, Keys.ENTER)
    box.send_keys(Keys.ENTER)
    wait_for_response_completion(driver)

def wait_for_image_paste(driver, timeout=IMAGE_PASTE_WAIT_TIMEOUT):
    start = time.time()
    phase = False
    while time.time() - start < timeout:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button[data-testid='send-button']")
            if not btn.is_enabled():
                phase = True
            if phase and btn.is_enabled():
                time.sleep(0.5)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def get_last_response_text(driver):
    els = driver.find_elements(By.CSS_SELECTOR, "div[data-message-author-role='assistant']")
    if not els:
        return ''
    block = els[-1]
    parts = []
    for sel in ['div.markdown', 'div[class*="result-streaming"]', 'div>p']:
        for e in block.find_elements(By.CSS_SELECTOR, sel):
            text = e.text.strip()
            if text:
                parts.append(text)
    return max(parts, key=len) if parts else block.text.strip()

# --- JSON Parsing Helpers ---
def strip_markdown_fences(text):
    if not isinstance(text, str) or not text:
        return ""
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()

def try_parse_json(response_text):
    if not response_text:
        return False, "Empty response text."
    try:
        data = json.loads(response_text)
        return True, data
    except json.JSONDecodeError:
        cleaned = strip_markdown_fences(response_text)
        try:
            data = json.loads(cleaned)
            return True, data
        except json.JSONDecodeError:
            start = cleaned.find('{')
            end = cleaned.rfind('}') + 1
            if start != -1 and end != -1:
                try:
                    data = json.loads(cleaned[start:end])
                    return True, data
                except Exception:
                    pass
    return False, "JSON parse failed."

# --- Request JSON Data ---
def request_json_data(driver, prompt_text, context_description="item"):
    log_step(f"Requesting JSON data for {context_description}...", important=True)
    send_text_message(driver, prompt_text)
    last_error = ""
    for attempt in range(CHATGPT_JSON_RETRIES + 1):
        log_step(f"Parse attempt {attempt+1}/{CHATGPT_JSON_RETRIES+1}", level="debug")
        resp = get_last_response_text(driver)
        success, data_or_err = try_parse_json(resp)
        if success:
            if isinstance(data_or_err, dict) and "positive_prompt" in data_or_err and isinstance(data_or_err["positive_prompt"], str):
                log_step("Valid JSON received", level="success")
                return data_or_err
            last_error = "Missing or invalid 'positive_prompt'"
        else:
            last_error = data_or_err
        if attempt < CHATGPT_JSON_RETRIES:
            fix = (
                f"Previous response invalid JSON ({last_error})."
                " Please resend ONLY raw JSON in format {\"positive_prompt\": \"...\"}."
            )
            send_text_message(driver, fix)
        else:
            log_step(f"Failed after retries: {last_error}", level="error", important=True)
    return None

# --- Save Video Prompt File ---
def save_video_prompt_file(output_path: Path, prompt_data: dict):
    try:
        final = {
            "positive_prompt": prompt_data["positive_prompt"],
            "negative_prompt": FIXED_NEGATIVE_PROMPT
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final, f, indent=2, ensure_ascii=False)
        log_step(f"Saved prompts: {output_path.name}", level="success")
        return True
    except Exception as e:
        log_step(f"Error saving prompts '{output_path.name}': {e}", level="error")
        return False

# --- Main Generation Logic ---
def run_mythic_video_prompt_generation(project_path: Path):
    log_step("Step 1: Setup & Validation", important=True)
    if not project_path.is_dir():
        log_step(f"Invalid project path: {project_path}", level="error")
        return False
    # Load story
    story_path = project_path / STORY_FILENAME
    story_summary = ""
    if story_path.is_file():
        text = story_path.read_text(encoding='utf-8')
        words = text.split()
        story_summary = " ".join(words[:MAX_STORY_SUMMARY_WORDS]) + ("..." if len(words)>MAX_STORY_SUMMARY_WORDS else "")
    else:
        log_step("Story file missing, continuing without it.", level="warning")
    # Load scenes
    scenes_path = project_path / SCENES_FILENAME
    scenes_lookup = {}
    if scenes_path.is_file():
        data = json.loads(scenes_path.read_text(encoding='utf-8'))
        for s in data.get("scenes", []):
            scenes_lookup[str(s.get("scene_number"))] = s
    else:
        log_step("Scenes file missing, will use defaults.", level="warning")
    # Find images
    approved_dir = project_path / APPROVED_IMAGES_FOLDER_NAME
    if not approved_dir.is_dir():
        log_step(f"No approved images folder: {approved_dir}", level="error")
        return False
    images = sorted(approved_dir.glob("*_scene_*__*.[jp][pn]g"))
    if not images:
        log_step("No images found to process.", level="error")
        return False
    prompts_dir = project_path / VIDEO_PROMPTS_FOLDER_NAME
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Start Selenium & ChatGPT
    driver = None
    try:
        log_step("Initializing WebDriver...", important=True)
        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        driver = uc.Chrome(options=options)
        driver.get(CHATGPT_BASE_URL)
        time.sleep(4)
        if not load_cookies(driver, COOKIE_PATH):
            input("Please login and then press Enter...")
            save_cookies(driver, COOKIE_PATH)
            driver.get(CHATGPT_TARGET_MODEL_URL)
            time.sleep(4)
        else:
            driver.get(CHATGPT_TARGET_MODEL_URL)
            time.sleep(4)
        wait_for_input_box(driver, timeout=90)
        # Send system prompt
        system = f"""You are an AI assistant generating CONCISE video prompts... Overall story: {story_summary}
        Follow instructions: output only raw JSON {{\"positive_prompt\": \"...\"}}."""
        send_text_message(driver, system)
        time.sleep(2)
    except Exception as e:
        log_step(f"Setup failed: {e}", level="error", important=True)
        if driver: driver.quit()
        return False

    # Process each image
    success_count = fail_count = skip_count = 0
    for idx, img in enumerate(images, 1):
        remaining = len(images) - idx
        log_step(f"Processing {idx}/{len(images)}: {img.name} ({remaining} left)", important=True)
        try:
            m = re.search(r"_scene_(\d+)__\d+_", img.name)
            if not m:
                log_step(f"Filename pattern error: {img.name}", level="error")
                skip_count +=1
                continue
            scene_no = m.group(1)
            out_name = img.stem + VIDEO_PROMPT_OUTPUT_SUFFIX
            out_path = prompts_dir / out_name
            if out_path.exists():
                log_step(f"Skipping existing: {out_name}", level="warning")
                skip_count +=1
                continue

            desc = scenes_lookup.get(scene_no, {}).get("scene_description", "No scene description.")
            if not copy_image_to_clipboard(img):
                raise RuntimeError("Clipboard copy failed")
            box = wait_for_input_box(driver)
            box.clear()
            box.send_keys(Keys.CONTROL, 'v')
            if not wait_for_image_paste(driver):
                raise RuntimeError("Image paste/upload timed out")
            task = f"""Image pasted. SCENE {scene_no} desc: {desc}
            TASK: Generate raw JSON {{\"positive_prompt\":\"...\"}}"""
            prompt_data = request_json_data(driver, task, context_description=f"Scene {scene_no}")
            if not prompt_data:
                raise ValueError("No valid JSON from ChatGPT")
            if not save_video_prompt_file(out_path, prompt_data):
                raise IOError("Failed saving prompt file")
            success_count +=1
        except Exception as ex:
            log_step(f"Error on {img.name}: {ex}", level="error")
            fail_count +=1
            if driver:
                try:
                    ts = datetime.now().strftime(TIMESTAMP_FORMAT)
                    ss = log_file_path.parent / f"err_{ts}_{img.stem}.png"
                    driver.save_screenshot(str(ss))
                    log_step(f"Saved screenshot: {ss}", level="info")
                except:
                    pass
        time.sleep(random.uniform(3,6))

    # Cleanup
    log_step("Cleanup & Summary", important=True)
    if driver:
        driver.quit()
        log_step("WebDriver closed.", level="success")
    log_step(f"Total images: {len(images)}")
    log_step(f"Succeeded: {success_count}, Failed: {fail_count}, Skipped: {skip_count}")
    return (fail_count == 0 and success_count > 0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate VIDEO prompts for Mythic...")
    parser.add_argument("-p", "--project_path", type=str, default=None,
                        help="Path to project folder; omit to auto-find latest")
    args = parser.parse_args()
    if DOTENV_PATH.is_file():
        load_dotenv(dotenv_path=DOTENV_PATH)
    if args.project_path:
        proj = Path(args.project_path).resolve()
    else:
        proj = find_latest_project_dir(PROJECTS_BASE_DIR)
    if not proj or not proj.is_dir():
        print(f"Error: project not found: {proj}")
        sys.exit(1)
    success = run_mythic_video_prompt_generation(proj)
    sys.exit(0 if success else 1)

