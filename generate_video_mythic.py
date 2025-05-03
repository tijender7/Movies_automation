# generate_video_mythic.py (v1.1 - Single Scene Processing Added)
# Generates Mythic videos via the ORIGINAL WanVideo workflow (API_wanvideo_original.json).
# Can process all scenes or a single specified scene using --scene argument.

import logging
import json
import sys
import random
import argparse # <--- Added
import copy
import shutil
import re # <--- Added for regex matching
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# --- Imports & Config ---
try:
    import config_mythic        # for project structure + prompt suffix
    import config_v3 as vc      # for workflow path + node titles
    import comfyui_interactions
    sys.path.append(str(Path(__file__).parent))
    # Assuming find_latest_project_dir is defined elsewhere or copied here
    # If it's in generate_prompts_chatgpt.py, let's copy it here for simplicity
    # from generate_prompts_chatgpt import find_latest_project_dir
except Exception as e:
    print(f"ERROR importing configs or helpers: {e}")
    sys.exit(1)

# --- Project‐specific constants (from config_mythic) ---
PROJECTS_BASE_DIR      = Path(config_mythic.PROJECTS_BASE_DIR)
APPROVED_IMAGES_FOLDER = config_mythic.MYTHIC_APPROVED_FOLDER_NAME
VIDEO_PROMPTS_FOLDER   = config_mythic.VIDEO_PROMPTS_FOLDER_NAME
VIDEO_PROMPT_SUFFIX    = config_mythic.VIDEO_PROMPT_OUTPUT_SUFFIX # Should be ".video_prompts.json"

# --- Workflow & nodes (from config_v3) ---
# Ensure these point to the *WanVideo* workflow and its nodes
WORKFLOW_PATH   = Path(getattr(vc, 'VIDEO_WORKFLOW_TEMPLATE', '')) # Get path from vc (config_v3)
COMFYUI_INPUT_DIR  = Path(getattr(vc, 'COMFYUI_INPUT_DIR', '')) # Get path from vc
COMFYUI_OUTPUT  = Path(getattr(vc, 'COMFYUI_OUTPUT_DIR', '')) # Get path from vc
API_SUBDIR      = getattr(vc, 'API_OUTPUTS_SUBDIR', 'API_OUTPUTS') # Get from vc
TIMEOUT         = getattr(vc, 'COMFYUI_TIMEOUT', 600) # Get from vc

NODE_PROMPT     = getattr(vc, 'VIDEO_PROMPT_NODE_TITLE', '')          # "API_Prompt_Input" or similar from vc
NODE_NEGATIVE   = getattr(vc, 'VIDEO_NEGATIVE_PROMPT_NODE_TITLE', '') # Check vc for this title
NODE_SEED       = getattr(vc, 'VIDEO_SEED_NODE_TITLE', '')             # "API_Seed_Input" or similar from vc
NODE_IMAGE      = getattr(vc, 'VIDEO_START_IMAGE_NODE_TITLE', '')    # "API_Video_Start_Image" or similar from vc
NODE_SAVE       = getattr(vc, 'VIDEO_OUTPUT_SAVE_NODE_TITLE', '')      # Title of the final save/combine node in WanVideo workflow from vc
VIDEO_PREFIX    = getattr(vc, 'VIDEO_PREFIX', 'vid_')                  # "vid_" or similar from vc

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

# --- Copied Helper Function (if not imported) ---
def find_latest_project_dir(base_dir):
    log_step(f"Searching for latest project directory in: {base_dir}", level="debug")
    latest_dir = None; latest_time = 0
    try:
        base_path = Path(base_dir)
        if not base_path.is_dir(): log_step(f"Base project directory not found: {base_dir}", level="error"); return None
        subdirs = [d for d in base_path.iterdir() if d.is_dir() and d.name.startswith("mythic_movie_")]
        if not subdirs: log_step(f"No project directories found matching pattern in {base_dir}", level="warning"); return None
        for item in subdirs:
            try:
                mod_time = item.stat().st_mtime
                if mod_time > latest_time: latest_time = mod_time; latest_dir = item
            except OSError as e: log_step(f"Could not stat directory {item.name}: {e}", level="warning")
    except Exception as e: log_step(f"Error searching for latest project: {e}", level="error"); return None
    if latest_dir: log_step(f"Found latest project directory: {latest_dir.name}", level="success")
    else: log_step(f"No project directories found matching pattern in {base_dir}", level="warning")
    return latest_dir
# --- End Copied Helper ---


def run_video_generation(proj: Path, target_scene: str = None) -> bool: # Added target_scene argument
    log_step("Step 1: Setup & Validation", important=True)
    if not proj.is_dir(): log_step(f"Invalid project path: {proj}", level="error"); return False
    if target_scene: log_step(f"Targeting specific scene: {target_scene}", level="info")

    # Approved images
    imgs_dir = proj / APPROVED_IMAGES_FOLDER
    if not imgs_dir.is_dir(): log_step(f"Missing approved images folder: {imgs_dir}", level="error"); return False
    # Find all potentially relevant images first
    all_images = sorted(imgs_dir.glob('*_scene_*__*.[jp][pn]g'))
    if not all_images: log_step("No approved images matching pattern found", level="error"); return False
    log_step(f"Found {len(all_images)} total approved images", level="debug")

    # --- *** Filter images based on target_scene *** ---
    images_to_process = []
    if target_scene:
        found_target = False
        for img_path in all_images:
            match_filter = re.search(r'_scene_(\d+)__\d+_', img_path.name)
            if match_filter and match_filter.group(1) == target_scene:
                images_to_process.append(img_path)
                found_target = True
                log_step(f"Selected target image for scene {target_scene}: {img_path.name}", level="info")
                break # Assuming only one image per scene
        if not found_target:
            log_step(f"ERROR: No approved image found for specified scene number '{target_scene}' in {imgs_dir}", level="error")
            return False
    else:
        images_to_process = all_images # Process all if no target specified

    total_images_to_process = len(images_to_process)
    log_step(f"Will process {total_images_to_process} image(s).")
    if total_images_to_process == 0: return True # Nothing to do

    # Video prompts
    prompts_dir = proj / VIDEO_PROMPTS_FOLDER
    if not prompts_dir.is_dir(): log_step(f"Missing prompts folder: {prompts_dir}", level="error"); return False

    # Prepare output folder
    base_out = COMFYUI_OUTPUT / API_SUBDIR
    base_out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Output folder name depends on whether we process one or all
    run_folder_name = f"{proj.name}_VIDEO_Scene_{target_scene}_{ts}" if target_scene else f"{proj.name}_VIDEO_Run_{ts}"
    run_out = base_out / run_folder_name
    run_out.mkdir(parents=True, exist_ok=True)
    log_step(f"Video output directory for this run: {run_out}", level="info")

    # Load workflow template
    wf_template = comfyui_interactions.load_workflow_template(str(WORKFLOW_PATH)) # Ensure path is string
    if not wf_template: log_step(f"Failed loading workflow: {WORKFLOW_PATH}", level="error"); return False
    log_step("Workflow template loaded", level="info")

    # Validate node titles from config_v3 (vc)
    node_titles_to_check = {'Positive Prompt': NODE_PROMPT, 'Seed': NODE_SEED, 'Start Image': NODE_IMAGE, 'Save Node': NODE_SAVE}
    # Also check negative if used by workflow
    if NODE_NEGATIVE: node_titles_to_check['Negative Prompt'] = NODE_NEGATIVE
    valid_nodes = True
    for name, title in node_titles_to_check.items():
        if not title: log_step(f"Missing node title in config_v3 for: {name}", level="error", important=True); valid_nodes = False
        elif not comfyui_interactions.find_node_id_by_title(wf_template, title): log_step(f"Node title '{title}' for '{name}' not found in workflow '{WORKFLOW_PATH.name}'!", level="error", important=True); valid_nodes = False
    if not valid_nodes: return False

    log_step("Step 2: Generating video(s)", important=True)
    success_count = fail_count = 0

    # --- Iterate over the selected images ---
    for idx, img in enumerate(images_to_process, start=1):
        log_step(f"Processing {idx}/{total_images_to_process}: {img.name}", level="info", important=True)
        stem = img.stem

        try: # Wrap processing for each image
            # 1) Copy image into ComfyUI input dir
            try:
                dest = COMFYUI_INPUT_DIR / img.name
                shutil.copy2(img, dest)
                log_step(f"Copied '{img.name}' to ComfyUI input dir", level="debug")
            except Exception as e: raise RuntimeError(f"Failed to copy image to input dir: {e}") from e

            # 2) Load prompts JSON
            pj = prompts_dir / f"{stem}{VIDEO_PROMPT_SUFFIX}"
            if not pj.is_file(): raise FileNotFoundError(f"Prompt JSON missing: {pj}")
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
                pos = data.get("positive_prompt")
                neg = data.get("negative_prompt")
                if not pos or not neg: raise ValueError("Missing positive or negative prompt in JSON")
            except Exception as e: raise ValueError(f"Bad JSON or missing keys in {pj}: {e}") from e

            # 3) Clone workflow & prepare inputs
            wf = copy.deepcopy(wf_template)
            seed = random.randint(0, 2**32 - 1)
            # Define prefix based on image stem, outputting into the run_out folder
            filename_prefix_for_node = f"{VIDEO_PREFIX}{stem}_"
            output_path_for_node = (run_out / filename_prefix_for_node).as_posix() # Path for the save node

            # --- Modify Inputs using vc (config_v3) Node Titles ---
            inputs = {}
            # Assuming WanVideo takes combined prompt in one node
            if NODE_PROMPT: inputs[NODE_PROMPT] = {"positive_prompt": pos, "negative_prompt": neg}
            # Or handle separate positive/negative nodes if defined in config_v3
            # if NODE_POSITIVE: inputs[NODE_POSITIVE] = {"text": pos} # Example
            # if NODE_NEGATIVE: inputs[NODE_NEGATIVE] = {"text": neg} # Example

            if NODE_SEED: inputs[NODE_SEED] = {"seed": seed} # Or "value": seed depending on node type
            if NODE_IMAGE: inputs[NODE_IMAGE] = {"image": img.name} # Pass filename relative to input dir
            if NODE_SAVE: inputs[NODE_SAVE] = {"filename_prefix": output_path_for_node} # Set output path/prefix

            # 4) Modify & run
            log_step("Modifying workflow inputs", level="debug")
            if not comfyui_interactions.modify_workflow_inputs(wf, inputs):
                raise RuntimeError("modify_workflow_inputs failed")

            log_step(f"Running workflow (seed={seed})", level="info")
            history = comfyui_interactions.run_comfyui_workflow(wf, timeout=TIMEOUT)
            if not history: raise RuntimeError("Workflow run failed or timed out")

            # 5) Verify output (basic check)
            time.sleep(1.5) # Slightly longer delay for video file flush
            # Check for MP4 or other expected video format
            out_files = list(run_out.glob(f"{filename_prefix_for_node}*.mp4")) + \
                        list(run_out.glob(f"{filename_prefix_for_node}*.webm")) + \
                        list(run_out.glob(f"{filename_prefix_for_node}*.gif"))
            if out_files:
                success_count += 1
                log_step(f"✅ Video generated: {out_files[0].relative_to(base_out)}", level="success")
            else:
                raise RuntimeError(f"No output video file found matching '{filename_prefix_for_node}*'")

        except Exception as e:
            log_step(f"Error processing {img.name}: {e}", level="error", exc_info=True)
            fail_count += 1
            # Optional: Decide if you want to stop on first error or continue
            # if target_scene: return False # Stop if the single target scene failed
            # else: continue # Continue to next image if processing all

    log_step("Generation complete", important=True)
    log_step(f"Attempted: {total_images_to_process}, Succeeded: {success_count}, Failed: {fail_count}", level="info")
    log_step(f"All outputs for this run under: {run_out}", level="info")
    return (fail_count == 0)

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Mythic videos via WanVideo workflow")
    parser.add_argument("-p", "--project_path", help="Path to project (omit to auto-find latest)")
    # --- *** ADDED SCENE ARGUMENT *** ---
    parser.add_argument("-s", "--scene", help="Generate video only for this specific scene number (e.g., '11').", type=str, default=None)
    args = parser.parse_args()
    log_step(f"===== Generate Mythic Videos Script Started (v1.1 - Single Scene) =====", important=True)

    # Load .env if present
    if Path(getattr(config_mythic, 'DOTENV_PATH', '.env')).is_file(): # Use path from config_mythic
        load_dotenv(dotenv_path=config_mythic.DOTENV_PATH)

    # Validate COMFYUI_INPUT & OUTPUT directories from vc (config_v3)
    if not COMFYUI_INPUT_DIR or not COMFYUI_INPUT_DIR.is_dir():
        log_step(f"Invalid COMFYUI_INPUT_DIR in config_v3: {COMFYUI_INPUT_DIR}", level="error", important=True); sys.exit(1)
    if not COMFYUI_OUTPUT or not COMFYUI_OUTPUT.is_dir():
        log_step(f"Invalid COMFYUI_OUTPUT_DIR in config_v3: {COMFYUI_OUTPUT}", level="error", important=True); sys.exit(1)
    if not WORKFLOW_PATH or not WORKFLOW_PATH.is_file():
        log_step(f"Workflow JSON not found via config_v3: {WORKFLOW_PATH}", level="error", important=True); sys.exit(1)

    # Determine project using PROJECTS_BASE_DIR from config_mythic
    proj = None
    if args.project_path:
        proj = Path(args.project_path).resolve()
        if not proj.is_dir(): log_step(f"Project path not found: {proj}", level="error", important=True); sys.exit(1)
    else:
        proj = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not proj: log_step(f"Could not find latest project in {PROJECTS_BASE_DIR}", level="error", important=True); sys.exit(1)

    log_step(f"Using Project: {proj.name}")
    # Pass target_scene to the main function
    success = run_video_generation(proj, target_scene=args.scene)
    sys.exit(0 if success else 1)