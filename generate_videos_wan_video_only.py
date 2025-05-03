# generate_videos.py v6
# Targets inputs for the modified WanVideo workflow.

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
import time

# --- Import project config and helpers ---
try:
    import config_v2 as config
    import comfyui_interactions as comfyui_interactions
    sys.path.append(str(Path(__file__).parent))
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir
except ImportError as e: print(f"ERROR: Import failed: {e}"); exit(1)
except FileNotFoundError as e: print(f"ERROR: Cannot find helpers script: {e}"); exit(1)

# --- Constants ---
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "video_gen.log")
PROJECTS_BASE_DIR = config.PROJECTS_BASE_DIR
CHARACTERS_FOLDER_NAME = config.CHARACTERS_FOLDER_NAME
APPROVED_IMAGES_FOLDER_NAME = config.APPROVED_IMAGES_FOLDER_NAME
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json"
COMFYUI_INPUT_DIR = Path(config.COMFYUI_INPUT_DIR) if config.COMFYUI_INPUT_DIR else None
COMFYUI_OUTPUT_DIR = Path(config.COMFYUI_OUTPUT_DIR) if config.COMFYUI_OUTPUT_DIR else None
API_OUTPUTS_SUBDIR = config.API_OUTPUTS_SUBDIR
VIDEO_WORKFLOW_TEMPLATE = Path(config.VIDEO_WORKFLOW_TEMPLATE) if config.VIDEO_WORKFLOW_TEMPLATE else None
# COMFYUI_VIDEO_SUBFOLDER = config.COMFYUI_VIDEO_SUBFOLDER # Determined by FileNamePrefix node
VIDEO_PREFIX = config.VIDEO_PREFIX

# --- Node Titles from Config ---
VIDEO_PROMPT_NODE_TITLE = config.VIDEO_PROMPT_NODE_TITLE             # e.g., "API_Prompt_Input" (WanVideoTextEncode)
VIDEO_NEGATIVE_PROMPT_NODE_TITLE = config.VIDEO_NEGATIVE_PROMPT_NODE_TITLE
VIDEO_SEED_NODE_TITLE = config.VIDEO_SEED_NODE_TITLE              # e.g., "API_Seed_Input" (KSampler)
VIDEO_START_IMAGE_NODE_TITLE = config.VIDEO_START_IMAGE_NODE_TITLE # e.g., "API_Video_Start_Image" (LoadAndResizeImage)
VIDEO_OUTPUT_SAVE_NODE_TITLE = config.VIDEO_OUTPUT_SAVE_NODE_TITLE # e.g., "Video Combine 🎥🅥🅗🅢" (VHS_VideoCombine)
# VIDEO_OUTPUT_PREFIX_NODE_TITLE = config.VIDEO_OUTPUT_PREFIX_NODE_TITLE # e.g., "API_Output_Prefix" (FileNamePrefix) # <<< COMMENTED OUT, node not in workflow

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-[%(filename)s:%(lineno)d]-%(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING); logging.getLogger('werkzeug').setLevel(logging.WARNING); logging.getLogger('tensorflow').setLevel(logging.ERROR)
except Exception as e: print(f"ERROR logging setup: {e}"); logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-%(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Functions ---
def log_step(m, l="info", i=False, exc_info=None): prefix = f"--- " if i else ""; suffix = f" ---" if i else ""; log.log(getattr(logging, l.upper(), logging.INFO), f"{prefix}[{l[0].upper()}] {m}{suffix}", exc_info=exc_info)
def sanitize_name(n): return re.sub(r'[_]+', '_', "".join(c for c in re.sub(r'[<>:"/\\|?*\']', '_', str(n)) if c.isalnum() or c in (' ','_','-')).strip().replace(' ','_')).strip('_-')[:60] or "invalid"
def copy_to_comfyui_input(source_file_path: Path):
    if not source_file_path.is_file(): log_step(f"Source missing: {source_file_path}", l="error"); return False
    if not COMFYUI_INPUT_DIR or not COMFYUI_INPUT_DIR.is_dir(): log_step(f"Comfy Input Dir invalid: {COMFYUI_INPUT_DIR}", l="error"); return False
    try: dest = COMFYUI_INPUT_DIR / source_file_path.name; shutil.copy2(source_file_path, dest); log_step(f" Copied '{source_file_path.name}' to input", l="debug"); return True
    except Exception as e: log_step(f"Error copying '{source_file_path.name}': {e}", l="error"); return False

# --- Main Logic ---
def run_video_generation(project_path: Path):
    log_step(f"--- Step 1: Setup & Validation ---", l="info", i=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", l="error"); return False
    selected_theme = extract_theme_from_project_path(project_path); log_step(f"Theme: '{selected_theme}'")
    approved_images_dir = project_path / APPROVED_IMAGES_FOLDER_NAME
    project_characters_base_path = project_path / CHARACTERS_FOLDER_NAME
    if not approved_images_dir.is_dir(): log_step(f"Approved images folder missing: {approved_images_dir}", l="error"); return False
    comfy_api_base_folder_abs = Path(COMFYUI_OUTPUT_DIR) / API_OUTPUTS_SUBDIR
    try: comfy_api_base_folder_abs.mkdir(parents=True, exist_ok=True)
    except OSError as e: log_step(f"Cannot create API output base dir: {e}", l="error"); return False
    approved_image_paths = sorted(list(approved_images_dir.glob('*.[jp][pn]g')))
    if not approved_image_paths: log_step(f"No approved images found in {approved_images_dir}", l="warning"); return True
    log_step(f"Found {len(approved_image_paths)} approved images.")
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    comfy_api_run_subfolder_rel = Path(f"{project_path.name}_VIDEO_{run_timestamp}")
    comfy_api_run_folder_abs = comfy_api_base_folder_abs / comfy_api_run_subfolder_rel
    log_step(f"ComfyUI output directory for this run: {comfy_api_run_folder_abs}")
    workflow_template = comfyui_interactions.load_workflow_template(VIDEO_WORKFLOW_TEMPLATE)
    if not workflow_template: log_step("Failed load VIDEO workflow.", l="error"); return False
    log_step(f"Loaded workflow: {VIDEO_WORKFLOW_TEMPLATE}")

    log_step(f"--- Step 2: Generating Videos ---", l="info", i=True); success_count = 0; fail_count = 0; processed_count = 0; at_least_one_comfy_run_started = False
    for image_path in approved_image_paths:
        processed_count += 1; log_step(f"--- Processing Video {processed_count}/{len(approved_image_paths)} for: {image_path.name} ---", l="info", i=True)
        filename_stem = image_path.stem; parts = filename_stem.split("__")
        if len(parts) < 3: log_step(f" Skip bad name format: {filename_stem}", l="warning"); fail_count+=1; continue
        try: char_name_san, actor_name_san = parts[0], parts[1]; original_comfy_suffix = "__".join(parts[2:])
        except IndexError: log_step(f" Skip bad name split: {filename_stem}", l="warning"); fail_count+=1; continue
        prompt_filename = filename_stem + VIDEO_PROMPT_OUTPUT_SUFFIX
        prompt_file_path = project_characters_base_path / actor_name_san / prompt_filename
        log_step(f" Looking for prompt file: ...{prompt_file_path.relative_to(PROJECTS_BASE_DIR)}", l="debug")
        if not prompt_file_path.is_file(): log_step(f" Video prompt file not found. Skipping.", l="warning"); fail_count+=1; continue
        try:
            with open(prompt_file_path, "r", encoding="utf-8") as f: video_prompts = json.load(f)
            positive_prompt = video_prompts.get("positive_prompt"); negative_prompt = video_prompts.get("negative_prompt")
            if not positive_prompt or not negative_prompt: raise ValueError("Missing prompt keys")
            log_step(f" Loaded prompts for {actor_name_san}.")
        except Exception as e: log_step(f" Error loading prompts {prompt_file_path}: {e}", l="error"); fail_count+=1; continue

        try:
            at_least_one_comfy_run_started = True
            start_image_path = image_path
            log_step(f" Using start image: {start_image_path.name}")
            if not copy_to_comfyui_input(start_image_path): raise IOError("Failed copy start image")

            # --- Define output prefix for FileNamePrefix node ---
            # The final filename will be controlled by this prefix + the ComfyUI counter/suffix
            output_prefix = f"{VIDEO_PREFIX}{filename_stem}_" # e.g., vid_CharacterName__ActorName__swapped_type_num_

            seed = random.randint(0, 2**32 - 1)
            current_workflow = copy.deepcopy(workflow_template);
            if not current_workflow: raise ValueError("Workflow copy failed.")

            # --- Prepare node modifications for the WanVideo Workflow ---
            inputs_to_set = {
                # Target the CLIPTextEncode nodes by title for prompts
                VIDEO_PROMPT_NODE_TITLE: {"text": positive_prompt},
                VIDEO_NEGATIVE_PROMPT_NODE_TITLE: {"text": negative_prompt},

                # Target the KSampler node by title for the seed
                VIDEO_SEED_NODE_TITLE: {"seed": seed},

                # Target the LoadAndResizeImage node by title for the image filename
                VIDEO_START_IMAGE_NODE_TITLE: {"image": start_image_path.name},

                # --- Target the Video Combine node DIRECTLY for output naming ---
                VIDEO_OUTPUT_SAVE_NODE_TITLE: {
                    # Set the 'filename_prefix' input of the Video Combine node
                    "filename_prefix": (comfy_api_run_folder_abs / f"{char_name_san}__{actor_name_san}" / "final_video_output" / output_prefix).as_posix()
                    # Note: This sets the FULL path prefix. ComfyUI will add _00001.mp4 etc.
                    # Ensure the target directory exists before queueing, or handle potential errors.
                    # You might need to create the ".../final_video_output" directory here if it doesn't exist yet.
                    # Example: (comfy_api_run_folder_abs / f"{char_name_san}__{actor_name_san}" / "final_video_output").mkdir(parents=True, exist_ok=True)
                }
                # --- REMOVED entry for non-existent 'API_Output_Prefix' node ---
            }
            log_step(f" Modifying workflow inputs...", l="info")
            log.debug(f"Workflow BEFORE modification attempt for node {VIDEO_PROMPT_NODE_TITLE} (ID {comfyui_interactions.find_node_id_by_title(current_workflow, VIDEO_PROMPT_NODE_TITLE)}): {json.dumps(current_workflow.get(comfyui_interactions.find_node_id_by_title(current_workflow, VIDEO_PROMPT_NODE_TITLE), {}).get('inputs', 'NODE_NOT_FOUND'), indent=2)}")
            log.debug(f"Inputs intended to be set: {json.dumps(inputs_to_set, indent=2)}")
            comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set)

            log_step(f" Queueing VIDEO workflow (Seed: {seed})...");
            comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=900)
            if not comfyui_result_history: raise TimeoutError("ComfyUI VIDEO workflow timeout/fail.")
            else:
                log_step(f" ComfyUI VIDEO workflow COMPLETED for {image_path.name}.", l="success")
                # --- Verification Step (Optional but recommended) ---
                # Check if the expected file was actually created by ComfyUI
                output_dir_for_node = comfy_api_run_folder_abs / f"{char_name_san}__{actor_name_san}" / "final_video_output"
                output_prefix_for_save_node = output_prefix
                expected_output_pattern = output_dir_for_node / f"{output_prefix_for_save_node}*.mp4"
                time.sleep(1) # Brief pause for filesystem
                created_files = list(output_dir_for_node.glob(f"{output_prefix_for_save_node}*.mp4"))
                if created_files:
                    success_count += 1
                    log_step(f" Video OK (Verified): {created_files[0].relative_to(PROJECTS_BASE_DIR)}", l="success")
                else:
                    fail_count += 1
                    log_step(f" ComfyUI finished, but expected output file matching '{expected_output_pattern}' not found!", l="error")
                    # You might want to log details from comfyui_result_history here for debugging

        except Exception as e: log_step(f" Error generation for {image_path.name}: {e}", l="error", exc_info=True); fail_count += 1

    log_step("\n--- Finished Video Generation Phase ---", l="info", i=True)
    if not at_least_one_comfy_run_started: log_step("No ComfyUI video runs attempted.", l="warning"); return False
    log_step(f"Attempted generation for {processed_count} images.")
    log_step(f"Success: {success_count}")
    log_step(f"Failed: {fail_count}")
    log_step(f"Raw outputs may exist within ComfyUI output folder: {comfy_api_run_folder_abs}") # Point to general area
    return fail_count == 0

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Videos using ComfyUI (WanVideo)."); parser.add_argument("-p", "--project_path", help="Project folder. Omit for latest.")
    args = parser.parse_args(); log_step("===== Generate Videos Script Started =====", l="info", i=True)
    try: load_dotenv(dotenv_path=config.DOTENV_PATH)
    except Exception as e: log_step(f"Note: .env load error: {e}", l="debug")

    if not all([COMFYUI_INPUT_DIR, COMFYUI_OUTPUT_DIR, VIDEO_WORKFLOW_TEMPLATE]): log_step("CRITICAL Error: ComfyUI paths/template missing.", l="error", i=True); exit(1)
    if not Path(COMFYUI_INPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_INPUT_DIR invalid: {COMFYUI_INPUT_DIR}", l="error"); exit(1)
    if not Path(COMFYUI_OUTPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_OUTPUT_DIR invalid: {COMFYUI_OUTPUT_DIR}", l="error"); exit(1)
    workflow_path = Path(VIDEO_WORKFLOW_TEMPLATE);
    if not workflow_path.is_file() : log_step(f"Error: VIDEO_WORKFLOW_TEMPLATE not found: {workflow_path}", l="error"); exit(1)
    required_titles = [VIDEO_PROMPT_NODE_TITLE, VIDEO_NEGATIVE_PROMPT_NODE_TITLE, VIDEO_SEED_NODE_TITLE, VIDEO_START_IMAGE_NODE_TITLE, VIDEO_OUTPUT_SAVE_NODE_TITLE]
    if not all(required_titles): log_step(f"CRITICAL Error: Required VIDEO node titles missing in config: Need {required_titles}", l="error", i=True); exit(1)
    log_step("Config validation passed.", l="debug")

    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not Path(project_to_process).parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", l="error", i=True); sys.exit(1)

    log_step(f"Using project: {project_to_process.name}");
    success = run_video_generation(project_to_process)

    if success: log_step("Script finished successfully.", l="success", i=True); sys.exit(0)
    else: log_step("Script finished with errors.", l="error", i=True); sys.exit(1)