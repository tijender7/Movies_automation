# generate_videos.py v2
# Fixes prompt key lookup and interaction with primitive Seed/Prefix nodes.

import logging
import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import argparse
import random
import copy
from datetime import datetime
import sys
import re
import glob

# --- Import project config and helpers ---
try:
    import config_v2 as config
    import comfyui_interactions as comfyui_interactions # Use the interaction module
    sys.path.append(str(Path(__file__).parent)) # Add script's dir to path
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir
except ImportError as e: print(f"ERROR: Import failed (config_v2, comfyui_interactions, helpers): {e}"); exit(1)
except FileNotFoundError as e: print(f"ERROR: Cannot find generate_prompts_chatgpt.py: {e}"); exit(1)

# --- Constants ---
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "video_gen.log")
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects"))
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME', "characters")
APPROVED_IMAGES_FOLDER_NAME = getattr(config, 'APPROVED_IMAGES_FOLDER_NAME', "Approved_images_for_videos")
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json"
COMFYUI_INPUT_DIR = getattr(config, 'COMFYUI_INPUT_DIR', None)
COMFYUI_OUTPUT_DIR = getattr(config, 'COMFYUI_OUTPUT_DIR', None)
API_OUTPUTS_SUBDIR = getattr(config, 'API_OUTPUTS_SUBDIR', "API_OUTPUTS")
VIDEO_WORKFLOW_TEMPLATE = getattr(config, 'VIDEO_WORKFLOW_TEMPLATE', None)
COMFYUI_VIDEO_SUBFOLDER = getattr(config, 'COMFYUI_VIDEO_SUBFOLDER', 'generated_videos')
VIDEO_PREFIX = getattr(config, 'VIDEO_PREFIX', 'vid_')

# --- Node Titles from Config ---
VIDEO_PROMPT_NODE_TITLE = getattr(config, 'VIDEO_PROMPT_NODE_TITLE', None)
VIDEO_NEGATIVE_PROMPT_NODE_TITLE = getattr(config, 'VIDEO_NEGATIVE_PROMPT_NODE_TITLE', None)
VIDEO_START_IMAGE_NODE_TITLE = getattr(config, 'VIDEO_START_IMAGE_NODE_TITLE', None)

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-[%(filename)s:%(lineno)d]-%(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING); logging.getLogger('werkzeug').setLevel(logging.WARNING); logging.getLogger('tensorflow').setLevel(logging.ERROR)
except Exception as e: print(f"ERROR logging setup: {e}"); logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-%(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Functions ---
def log_step(m, l="info", i=False, exc_info=None):
    prefix = f"--- " if i else ""; suffix = f" ---" if i else ""
    if exc_info: log.log(getattr(logging, l.upper(), logging.INFO), f"{prefix}[{l[0].upper()}] {m}{suffix}", exc_info=exc_info)
    else: log.log(getattr(logging, l.upper(), logging.INFO), f"{prefix}[{l[0].upper()}] {m}{suffix}")

def sanitize_name(n): return re.sub(r'[_]+', '_', "".join(c for c in re.sub(r'[<>:"/\\|?*\']', '_', str(n)) if c.isalnum() or c in (' ','_','-')).strip().replace(' ','_')).strip('_-')[:60] or "invalid"
def copy_to_comfyui_input(source_file_path: Path):
    if not source_file_path.is_file(): log_step(f"Source missing: {source_file_path}", l="error"); return False
    if not COMFYUI_INPUT_DIR or not Path(COMFYUI_INPUT_DIR).is_dir(): log_step(f"Comfy Input Dir invalid: {COMFYUI_INPUT_DIR}", l="error"); return False
    try: dest = Path(COMFYUI_INPUT_DIR) / source_file_path.name; shutil.copy2(source_file_path, dest); log_step(f" Copied '{source_file_path.name}' to input", l="debug"); return True
    except Exception as e: log_step(f"Error copying '{source_file_path.name}': {e}", l="error"); return False

def find_source_images(actor_name, source_dir): # Kept for consistency if needed elsewhere, but not used directly below
    sanitized_name = sanitize_name(actor_name); pattern = str(source_dir / f"{sanitized_name}_*.[jp][pn]g"); files = glob.glob(pattern)
    return [Path(f) for f in files]

# --- Main Logic ---
def run_video_generation(project_path: Path):
    log_step(f"--- Step 1: Setup & Validation ---", l="info", i=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", l="error"); return False
    selected_theme = extract_theme_from_project_path(project_path); log_step(f"Theme: '{selected_theme}'")

    approved_images_dir = project_path / APPROVED_IMAGES_FOLDER_NAME
    project_characters_base_path = project_path / CHARACTERS_FOLDER_NAME
    if not approved_images_dir.is_dir(): log_step(f"Approved images folder not found: {approved_images_dir}", l="error"); return False
    comfy_api_base_folder_abs = Path(COMFYUI_OUTPUT_DIR) / API_OUTPUTS_SUBDIR
    try: comfy_api_base_folder_abs.mkdir(parents=True, exist_ok=True)
    except OSError as e: log_step(f"Cannot create API output base dir: {e}", l="error"); return False

    approved_image_paths = sorted(list(approved_images_dir.glob('*.[jp][pn]g')))
    if not approved_image_paths: log_step(f"No approved images found in {approved_images_dir}", l="error"); return False
    log_step(f"Found {len(approved_image_paths)} approved images to process.")

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    comfy_api_run_subfolder_rel = Path(f"{project_path.name}_VIDEO_{run_timestamp}")
    comfy_api_run_folder_abs = comfy_api_base_folder_abs / comfy_api_run_subfolder_rel
    log_step(f"ComfyUI output base directory for this video run: {comfy_api_run_folder_abs}")

    workflow_template = comfyui_interactions.load_workflow_template(VIDEO_WORKFLOW_TEMPLATE)
    if not workflow_template: log_step("Failed to load VIDEO workflow template.", l="error"); return False
    log_step(f"Loaded workflow: {VIDEO_WORKFLOW_TEMPLATE}")

    log_step(f"--- Step 2: Generating Videos ---", l="info", i=True); success_count = 0; fail_count = 0; processed_count = 0; at_least_one_comfy_run_started = False

    for image_path in approved_image_paths:
        processed_count += 1; log_step(f"--- Processing Video {processed_count}/{len(approved_image_paths)} for: {image_path.name} ---", l="info", i=True)
        filename_stem = image_path.stem; parts = filename_stem.split("__")
        if len(parts) < 3: log_step(f" Skip bad name format: {filename_stem}", l="warning"); fail_count+=1; continue
        char_name_san, actor_name_san = parts[0], parts[1]

        prompt_filename = filename_stem + VIDEO_PROMPT_OUTPUT_SUFFIX
        prompt_file_path = project_characters_base_path / actor_name_san / prompt_filename # Prompts are in folder named after actor
        log_step(f" Looking for prompt file: ...{prompt_file_path.relative_to(PROJECTS_BASE_DIR)}", l="debug") # Use relative path for cleaner log

        if not prompt_file_path.is_file(): log_step(f" Video prompt file not found. Skipping.", l="warning"); fail_count+=1; continue

        try:
            with open(prompt_file_path, "r", encoding="utf-8") as f: video_prompts = json.load(f)
            positive_prompt = video_prompts.get("positive_prompt")
            negative_prompt = video_prompts.get("negative_prompt")
            if not positive_prompt or not negative_prompt: raise ValueError("Missing prompt keys")
            log_step(f" Loaded prompts for {actor_name_san}.")
        except Exception as e: log_step(f" Error loading prompts {prompt_file_path}: {e}", l="error"); fail_count+=1; continue

        try:
            at_least_one_comfy_run_started = True
            start_image_path = image_path
            log_step(f" Using start image: {start_image_path.name}")
            if not copy_to_comfyui_input(start_image_path): raise IOError("Failed copy start image")

            comfy_char_actor_rel_path = Path(f"{char_name_san}__{actor_name_san}")
            comfy_video_output_rel_path = comfy_char_actor_rel_path / COMFYUI_VIDEO_SUBFOLDER
            prefix_node_video_dir = comfy_api_run_subfolder_rel / comfy_video_output_rel_path
            output_prefix = f"{VIDEO_PREFIX}{filename_stem}_" # Use full stem in prefix
            seed = random.randint(0, 2**32 - 1)

            current_workflow = copy.deepcopy(workflow_template);
            if not current_workflow: raise ValueError("Workflow copy failed.")

            # --- FIXED: Inputs for direct node fields ---
            inputs_to_set = {
                VIDEO_PROMPT_NODE_TITLE: {"text": positive_prompt},
                VIDEO_NEGATIVE_PROMPT_NODE_TITLE: {"text": negative_prompt},
                # Set seed directly on the noise node ("Sampler Noise Seed")
                "Sampler Noise Seed": {"noise_seed": seed},
                VIDEO_START_IMAGE_NODE_TITLE: {"image": start_image_path.name},
                # Set prefix fields on FileNamePrefix node
                "API_Output_Prefix": {
                    "custom_text": output_prefix,
                    "custom_directory": "",
                    "date": "false",
                    "date_directory": "false"
                }
            }
            # --- END FIX ---

            log_step(f" Modifying workflow inputs...")
            if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set): raise ValueError("Failed modify workflow")

            log_step(f" Queueing VIDEO workflow (Seed: {seed})...");
            comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=900) # Longer timeout
            if not comfyui_result_history: raise TimeoutError("ComfyUI VIDEO workflow timeout/fail.")
            else: log_step(f" ComfyUI VIDEO workflow COMPLETED for {image_path.name}.", l="success"); success_count += 1

        except Exception as e: log_step(f" Error generation for {image_path.name}: {e}", l="error", exc_info=True); fail_count += 1

    # --- Final Summary ---
    log_step("\n--- Finished Video Generation Phase ---", l="info", i=True)
    if not at_least_one_comfy_run_started: log_step("No ComfyUI video runs attempted.", l="warning"); return False
    log_step(f"Attempted generation for {processed_count} approved images.")
    log_step(f"Success: {success_count}")
    log_step(f"Failed: {fail_count}")
    log_step(f"Outputs saved within: {comfy_api_run_folder_abs}")
    return fail_count == 0

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Videos using ComfyUI."); parser.add_argument("-p", "--project_path", help="Project folder. Omit for latest.")
    args = parser.parse_args(); log_step("===== Generate Videos Script Started =====", l="info", i=True)
    try: load_dotenv(dotenv_path=config.DOTENV_PATH)
    except Exception as e: log_step(f"Note: .env load error: {e}", l="debug")

    if not COMFYUI_INPUT_DIR or not COMFYUI_OUTPUT_DIR or not VIDEO_WORKFLOW_TEMPLATE: log_step("CRITICAL Error: ComfyUI paths/VIDEO template not configured.", l="error", i=True); exit(1)
    if not Path(COMFYUI_INPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_INPUT_DIR invalid: {COMFYUI_INPUT_DIR}", l="error"); exit(1)
    if not Path(COMFYUI_OUTPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_OUTPUT_DIR invalid: {COMFYUI_OUTPUT_DIR}", l="error"); exit(1)
    if not Path(VIDEO_WORKFLOW_TEMPLATE).is_file() : log_step(f"Error: VIDEO_WORKFLOW_TEMPLATE not found: {VIDEO_WORKFLOW_TEMPLATE}", l="error"); exit(1)
    if not all([VIDEO_PROMPT_NODE_TITLE, VIDEO_NEGATIVE_PROMPT_NODE_TITLE, VIDEO_START_IMAGE_NODE_TITLE]): log_step("CRITICAL Error: VIDEO node titles missing in config_v2.py.", l="error", i=True); exit(1)

    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not Path(project_to_process).parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", l="error", i=True); sys.exit(1)

    log_step(f"Using project: {project_to_process.name}");
    success = run_video_generation(project_to_process)

    if success: log_step("Script finished successfully.", l="success", i=True); exit(0)
    else: log_step("Script finished with errors.", l="error", i=True); exit(1)