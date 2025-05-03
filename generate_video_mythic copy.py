# generate_video_mythic.py
# Generates Mythic videos via the ORIGINAL WanVideo workflow (API_wanvideo_original.json).

import logging
import json
import sys
import random
import argparse
import copy
import shutil
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# --- Imports & Config ---
try:
    import config_mythic        # for project structure + prompt suffix
    import config_v3 as vc      # for workflow path + node titles
    import comfyui_interactions
    sys.path.append(str(Path(__file__).parent))
    from generate_prompts_chatgpt import find_latest_project_dir
except Exception as e:
    print(f"ERROR importing configs or helpers: {e}")
    sys.exit(1)

# --- Project‐specific constants (from config_mythic) ---
PROJECTS_BASE_DIR      = Path(config_mythic.PROJECTS_BASE_DIR)
APPROVED_IMAGES_FOLDER = config_mythic.MYTHIC_APPROVED_FOLDER_NAME
VIDEO_PROMPTS_FOLDER   = config_mythic.VIDEO_PROMPTS_FOLDER_NAME
VIDEO_PROMPT_SUFFIX    = config_mythic.VIDEO_PROMPT_OUTPUT_SUFFIX

# --- Workflow & nodes (from config_v3) ---
WORKFLOW_PATH   = Path(vc.VIDEO_WORKFLOW_TEMPLATE)    # API_wanvideo_original.json
COMFYUI_INPUT_DIR  = Path(vc.COMFYUI_INPUT_DIR)       # must be a real directory
COMFYUI_OUTPUT  = Path(vc.COMFYUI_OUTPUT_DIR)
API_SUBDIR      = vc.API_OUTPUTS_SUBDIR
TIMEOUT         = getattr(vc, 'COMFYUI_TIMEOUT', 600)

NODE_PROMPT     = vc.VIDEO_PROMPT_NODE_TITLE           # "API_Prompt_Input"
NODE_SEED       = vc.VIDEO_SEED_NODE_TITLE             # "API_Seed_Input"
NODE_IMAGE      = vc.VIDEO_START_IMAGE_NODE_TITLE      # "API_Video_Start_Image"
NODE_SAVE       = vc.VIDEO_OUTPUT_SAVE_NODE_TITLE      # "mp4"
VIDEO_PREFIX    = vc.VIDEO_PREFIX                       # "vid_"

# --- Logging Setup ---
LOG_FILE = Path(config_mythic.LOG_DIR) / "generate_video_mythic.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s-%(levelname)s-[%(filename)s:%(lineno)d]-%(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

def log_step(msg, level="info", important=False, exc_info=None):
    pfx = "--- " if important else ""
    sfx = " ---" if important else ""
    lvl = getattr(logging, level.upper(), logging.INFO)
    log.log(lvl, f"{pfx}[{level[0].upper()}] {msg}{sfx}", exc_info=exc_info)

def run_video_generation(proj: Path) -> bool:
    log_step("Step 1: Setup & Validation", important=True)
    if not proj.is_dir():
        log_step(f"Invalid project path: {proj}", level="error")
        return False

    # Approved images
    imgs_dir = proj / APPROVED_IMAGES_FOLDER
    if not imgs_dir.is_dir():
        log_step(f"Missing approved images folder: {imgs_dir}", level="error")
        return False
    images = sorted(imgs_dir.glob("*.[jp][pn]g"))
    if not images:
        log_step("No approved images found", level="error")
        return False
    log_step(f"Found {len(images)} approved images", level="info")

    # Video prompts
    prompts_dir = proj / VIDEO_PROMPTS_FOLDER
    if not prompts_dir.is_dir():
        log_step(f"Missing prompts folder: {prompts_dir}", level="error")
        return False

    # Prepare output folder
    base_out = COMFYUI_OUTPUT / API_SUBDIR
    base_out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_out = base_out / f"{proj.name}_VIDEO_{ts}"
    run_out.mkdir(parents=True, exist_ok=True)

    # Load workflow template
    wf_template = comfyui_interactions.load_workflow_template(WORKFLOW_PATH)
    if not wf_template:
        log_step(f"Failed loading workflow: {WORKFLOW_PATH}", level="error")
        return False
    log_step("Workflow template loaded", level="info")

    # Validate node titles
    for title in (NODE_PROMPT, NODE_SEED, NODE_IMAGE, NODE_SAVE):
        if not title:
            log_step(f"Missing node‐title in config_v3: {title}", level="error", important=True)
            return False

    log_step("Step 2: Generating videos", important=True)
    success = fail = 0

    for idx, img in enumerate(images, start=1):
        log_step(f"Processing {idx}/{len(images)}: {img.name}", level="info", important=True)
        stem = img.stem

        # 1) Copy image into ComfyUI input dir
        try:
            dest = COMFYUI_INPUT_DIR / img.name
            shutil.copy2(img, dest)
            log_step(f"Copied '{img.name}' to ComfyUI input dir", level="debug")
        except Exception as e:
            log_step(f"Failed to copy image to input dir: {e}", level="error")
            fail += 1
            continue

        # 2) Load prompts JSON
        pj = prompts_dir / f"{stem}{VIDEO_PROMPT_SUFFIX}"
        if not pj.is_file():
            log_step(f"Prompt JSON missing: {pj}", level="warning")
            fail += 1
            continue
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            pos = data["positive_prompt"]
            neg = data["negative_prompt"]
        except Exception as e:
            log_step(f"Bad JSON {pj}: {e}", level="error")
            fail += 1
            continue

        # 3) Clone workflow & prepare inputs
        wf = copy.deepcopy(wf_template)
        seed = random.randint(0, 2**32 - 1)
        prefix = f"{VIDEO_PREFIX}{stem}_"
        inputs = {
            NODE_PROMPT: {"positive_prompt": pos, "negative_prompt": neg},
            NODE_SEED:   {"seed": seed},
            NODE_IMAGE:  {"image": img.name},
            NODE_SAVE:   {"filename_prefix": (run_out / prefix).as_posix()}
        }

        # 4) Modify & run
        try:
            log_step("Modifying workflow inputs", level="debug")
            if not comfyui_interactions.modify_workflow_inputs(wf, inputs):
                raise RuntimeError("modify_workflow_inputs failed")

            log_step(f"Running workflow (seed={seed})", level="info")
            history = comfyui_interactions.run_comfyui_workflow(wf, timeout=TIMEOUT)
            if not history:
                raise RuntimeError("Workflow run failed or timed out")

            # 5) Verify output
            # small delay to let file flush
            import time; time.sleep(1)
            out_files = list(run_out.glob(f"{prefix}*.mp4"))
            if out_files:
                success += 1
                log_step(f"✅ Video generated: {out_files[0].relative_to(base_out)}", level="success")
            else:
                fail += 1
                log_step(f"❌ No output matched '{prefix}*.mp4'", level="error")

        except Exception as e:
            log_step(f"Error on {img.name}: {e}", level="error", exc_info=True)
            fail += 1

    log_step("Generation complete", important=True)
    log_step(f"Succeeded: {success}, Failed: {fail}", level="info")
    log_step(f"All outputs under: {run_out}", level="info")
    return (fail == 0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Mythic videos via WanVideo workflow")
    parser.add_argument("-p", "--project_path", help="Path to project (omit to auto-find latest)")
    args = parser.parse_args()

    # Load .env if present
    if Path(config_mythic.DOTENV_PATH).is_file():
        load_dotenv(dotenv_path=config_mythic.DOTENV_PATH)

    # Validate COMFYUI_INPUT & OUTPUT directories
    if not COMFYUI_INPUT_DIR.is_dir():
        log_step(f"Invalid COMFYUI_INPUT_DIR: {COMFYUI_INPUT_DIR}", level="error")
        sys.exit(1)
    if not COMFYUI_OUTPUT.is_dir():
        log_step(f"Invalid COMFYUI_OUTPUT_DIR: {COMFYUI_OUTPUT}", level="error")
        sys.exit(1)
    if not WORKFLOW_PATH.is_file():
        log_step(f"Workflow JSON not found: {WORKFLOW_PATH}", level="error")
        sys.exit(1)

    # Determine project
    proj = Path(args.project_path).resolve() if args.project_path else find_latest_project_dir(PROJECTS_BASE_DIR)
    if not proj or not proj.is_dir():
        log_step(f"Project not found: {proj}", level="error")
        sys.exit(1)

    success = run_video_generation(proj)
    sys.exit(0 if success else 1)
