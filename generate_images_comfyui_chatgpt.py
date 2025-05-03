# generate_images_comfyui.py v2
# Fixes prompt key lookup and ComfyUI output directory path.

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
import glob # Keep glob import
import traceback

# Import project modules
try:
    import config_v2 as config
    import comfyui_interactions as comfyui_interactions # Assuming v1 name based on your previous code run
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules (config_v2, comfyui_interactions, helpers): {e}")
    exit(1)

# --- Constants ---
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "comfyui_image_gen.log")
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects"))
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME', "characters")
SOURCE_ACTORS_FOLDER_NAME = getattr(config, 'SOURCE_ACTORS_FOLDER_NAME', "source_actors")
INFO_JSON_FILENAME = "chatgpt_movie_info.json"
PROMPT_JSON_FILENAME = "image_prompts.json"
COMFYUI_INPUT_DIR = getattr(config, 'COMFYUI_INPUT_DIR', None)
COMFYUI_OUTPUT_DIR = getattr(config, 'COMFYUI_OUTPUT_DIR', None)
API_OUTPUTS_SUBDIR = getattr(config, 'API_OUTPUTS_SUBDIR', "API_OUTPUTS")
IMAGE_WORKFLOW_TEMPLATE = getattr(config, 'IMAGE_WORKFLOW_TEMPLATE', None)
# --- Debug print removed after confirmation ---
# print(f"[DEBUG] IMAGE_WORKFLOW_TEMPLATE resolved to: {IMAGE_WORKFLOW_TEMPLATE}")
COMFYUI_BASE_IMAGES_SUBFOLDER = getattr(config, 'COMFYUI_BASE_IMAGES_SUBFOLDER', 'base_images')
COMFYUI_SWAPPED_IMAGES_SUBFOLDER = getattr(config, 'COMFYUI_SWAPPED_IMAGES_SUBFOLDER', 'swapped_images')
BASE_IMAGE_PREFIX = getattr(config, 'BASE_IMAGE_PREFIX', 'base_')
SWAPPED_IMAGE_PREFIX = getattr(config, 'SWAPPED_IMAGE_PREFIX', 'swapped_')
PROMPT_NODE_TITLE = getattr(config, 'PROMPT_NODE_TITLE', 'API_Prompt_Input')
SEED_NODE_TITLE = getattr(config, 'SEED_NODE_TITLE', 'API_Seed_Input')
FACE_NODE_TITLE = getattr(config, 'FACE_NODE_TITLE', 'API_Face_Input')
IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE = getattr(config, 'SAVE_BASE_IMAGE_NODE_TITLE', 'API_Base_Image_Output_SaveNode')
IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE = getattr(config, 'SAVE_SWAPPED_IMAGE_NODE_TITLE', 'API_Image_Output_SaveNode')


# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('tensorflow').setLevel(logging.ERROR)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
except Exception as e: print(f"ERROR setting up logging: {e}"); logging.basicConfig( level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}";
    if important: final_msg = f"--- {final_msg} ---"
    if level == "error": log.error(final_msg)
    elif level == "warning": log.warning(final_msg)
    elif level == "debug": log.debug(final_msg)
    else: log.info(final_msg)

# --- Helper: Copy Source Face to ComfyUI Input ---
def copy_to_comfyui_input(source_file_path: Path):
    if not source_file_path.is_file(): log_step(f"Source copy failed: not found {source_file_path}", level="error"); return False
    if not COMFYUI_INPUT_DIR or not Path(COMFYUI_INPUT_DIR).is_dir(): log_step(f"ComfyUI Input Dir invalid: {COMFYUI_INPUT_DIR}", level="error"); return False
    try: destination_path = Path(COMFYUI_INPUT_DIR) / source_file_path.name; shutil.copy2(source_file_path, destination_path); log_step(f" Copied '{source_file_path.name}' to input", level="debug"); return True
    except Exception as e: log_step(f"Error copying '{source_file_path.name}': {e}", level="error"); return False

# --- Helper: Sanitize Name ---
def sanitize_name(name):
    if not isinstance(name, str): name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name); sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized); sanitized = re.sub(r'[-]+', '-', sanitized); sanitized = sanitized.strip('_-'); max_len = 60
    if len(sanitized) > max_len: sanitized = sanitized[:max_len].strip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Helper: Find Source Images using Glob ---
def find_source_images(actor_name, source_dir):
    sanitized_name = sanitize_name(actor_name)
    # Use glob to find files matching the pattern ActorName_*.jpg/png
    pattern = str(source_dir / f"{sanitized_name}_*.[jp][pn]g")
    files = glob.glob(pattern) # glob handles path separators correctly
    return [Path(f) for f in files] # Return list of Path objects

# --- Main Logic ---
def run_image_generation(project_path: Path, theme: str):
    log_step(f"--- Starting Image Generation for Project: {project_path.name} ---", important=True)
    log_step(f"Using Theme: {theme}")

    info_json_path = project_path / INFO_JSON_FILENAME
    if not info_json_path.is_file(): log_step(f"Info JSON file not found: {info_json_path}", level="error"); return False
    try:
        with open(info_json_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
        if "movie_characters" not in movie_info: raise ValueError("Missing 'movie_characters' key")
    except Exception as e: log_step(f"Error loading {INFO_JSON_FILENAME}: {e}", level="error"); return False

    project_characters_base_path = project_path / CHARACTERS_FOLDER_NAME
    project_source_actors_path = project_path / SOURCE_ACTORS_FOLDER_NAME
    medium_images_succeeded = []; full_images_succeeded = []; all_actors_processed_with_source = []; at_least_one_comfy_run_started = False

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    comfy_api_run_subfolder_rel = Path(f"{project_path.name}_{run_timestamp}")
    comfy_api_base_folder_abs = Path(COMFYUI_OUTPUT_DIR) / API_OUTPUTS_SUBDIR
    comfy_api_run_folder_abs = comfy_api_base_folder_abs / comfy_api_run_subfolder_rel
    log_step(f"ComfyUI output base directory for this run: {comfy_api_run_folder_abs}")
    try: comfy_api_base_folder_abs.mkdir(parents=True, exist_ok=True)
    except OSError as e: log_step(f"Could not create API base output dir: {e}", level="error"); return False

    workflow_template = comfyui_interactions.load_workflow_template(IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template: log_step("Failed to load workflow template.", level="error"); return False

    for char_info in movie_info.get('movie_characters', []):
        character_name = char_info.get('character_name'); actor_name = char_info.get('actor_name')
        if not character_name or not actor_name: continue

        # --- Step 1: Find Approved Source Image(s) using Glob ---
        log_step(f"\nProcessing Actor: {actor_name} (Character: {character_name})")
        source_images = find_source_images(actor_name, project_source_actors_path) # Use updated helper
        if not source_images:
            log_step(f" Source image pattern NOT FOUND for Actor '{actor_name}'. Skipping generation.", level='warning')
            continue

        # Use the first found source image for this actor
        source_image_found = source_images[0]
        if len(source_images) > 1:
             log_step(f" Warning: Found multiple matches: {[f.name for f in source_images]}. Using: {source_image_found.name}", level="warning")
        log_step(f" Found source image: {source_image_found.name}", level="success")

        all_actors_processed_with_source.append(actor_name)

        # --- Step 2: Load Prompts ---
        project_character_dir = project_characters_base_path / sanitize_name(actor_name) # Folder based on actor name
        prompt_file_path = project_character_dir / PROMPT_JSON_FILENAME
        if not prompt_file_path.is_file(): log_step(f" Prompt JSON not found: {prompt_file_path}. Skipping {actor_name}.", level="warning"); continue
        prompt_json_data = None
        try:
            with open(prompt_file_path, 'r', encoding='utf-8') as f: prompt_json_data = json.load(f)
            log_step(f" Loaded prompts from {PROMPT_JSON_FILENAME} for {actor_name}")
        except Exception as e: log_step(f" Error loading/parsing {prompt_file_path}: {e}. Skipping {actor_name}.", level="error"); continue

        # --- Step 3: Generate Medium and Full Body ---
        for shot_type in ["medium", "full"]:
            # --- FIXED: Use correct key for prompt ---
            prompt_key = f"{shot_type}_body_prompt" if shot_type == "full" else f"{shot_type}_prompt"
            # --- END FIX ---

            flux_prompt = prompt_json_data.get(prompt_key)
            if not flux_prompt:
                log_step(f" '{prompt_key}' key not found in {PROMPT_JSON_FILENAME} for {actor_name}. Skipping {shot_type} shot.", level="warning")
                continue # Skip this shot type if prompt is missing

            log_step(f"--- Generating {shot_type.capitalize()} Shot Image for {character_name} ---")
            try:
                at_least_one_comfy_run_started = True
                log_step(f" Using prompt: {flux_prompt[:100]}...", level="debug")
                if not copy_to_comfyui_input(source_image_found): raise IOError(f"Failed copy source image")

                # --- Define ComfyUI Output Paths ---
                comfy_char_actor_rel_path = Path(f"{sanitize_name(character_name)}__{sanitize_name(actor_name)}")
                comfy_base_img_output_rel_path = comfy_char_actor_rel_path / COMFYUI_BASE_IMAGES_SUBFOLDER
                comfy_swapped_img_output_rel_path = comfy_char_actor_rel_path / COMFYUI_SWAPPED_IMAGES_SUBFOLDER
                # --- FIXED: Prepend API_OUTPUTS_SUBDIR to relative path ---
                prefix_node_base_dir = Path(API_OUTPUTS_SUBDIR) / comfy_api_run_subfolder_rel / comfy_base_img_output_rel_path
                prefix_node_swapped_dir = Path(API_OUTPUTS_SUBDIR) / comfy_api_run_subfolder_rel / comfy_swapped_img_output_rel_path
                # --- DEBUG LOGGING ---
                log_step(f"[DEBUG] prefix_node_base_dir: {prefix_node_base_dir}", level="debug")
                log_step(f"[DEBUG] prefix_node_swapped_dir: {prefix_node_swapped_dir}", level="debug")
                # --- END FIX ---
                base_filename_prefix = f"{BASE_IMAGE_PREFIX}{shot_type}_"; swapped_filename_prefix = f"{SWAPPED_IMAGE_PREFIX}{shot_type}_"
                seed = random.randint(0, 2**32 - 1)

                # --- Prepare & Execute ---
                current_workflow = copy.deepcopy(workflow_template);
                if not current_workflow: raise ValueError("Workflow copy failed.")
                inputs_to_set = {
                    PROMPT_NODE_TITLE: {"text": flux_prompt},
                    SEED_NODE_TITLE: {"noise_seed": seed},
                    FACE_NODE_TITLE: {"image": source_image_found.name},
                    "API_Base_Output_Prefix": {
                        "custom_text": base_filename_prefix,
                        "custom_directory": prefix_node_base_dir.as_posix(),
                        "date_directory": "false"
                    },
                    "API_Swapped_Output_Prefix": {
                        "custom_text": swapped_filename_prefix,
                        "custom_directory": prefix_node_swapped_dir.as_posix(),
                        "date_directory": "false"
                    }
                }
                log_step(f" Modifying inputs for {shot_type}...");
                if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set): raise ValueError("Failed modify workflow")
                log_step(f" Queueing {shot_type} workflow (Seed: {seed})...");
                comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=600) # Adjust timeout if needed
                if not comfyui_result_history: raise TimeoutError("ComfyUI workflow timeout/fail.")
                else:
                    log_step(f" ComfyUI {shot_type} workflow COMPLETED.", level="success")
                    if shot_type == "medium": medium_images_succeeded.append(actor_name)
                    else: full_images_succeeded.append(actor_name)

            except Exception as e: log_step(f"Error during {shot_type} generation for {character_name}: {e}\n{traceback.format_exc()}", level="error") # Log full traceback for unexpected

    # --- Final Summary ---
    log_step("\n--- Finished Image Generation Phase ---", important=True)
    if not at_least_one_comfy_run_started: log_step("No ComfyUI runs attempted.", level="warning"); return False
    unique_medium_success = set(medium_images_succeeded); unique_full_success = set(full_images_succeeded); processed_actors_count = len(all_actors_processed_with_source)
    log_step(f"Attempted generation for {processed_actors_count} actors with source images.")
    log_step(f"Success - Medium shots: {len(unique_medium_success)} actors.")
    log_step(f"Success - Full body shots: {len(unique_full_success)} actors.")
    log_step(f"Outputs saved within: {comfy_api_run_folder_abs}")
    failed_actors = [actor for actor in all_actors_processed_with_source if actor not in unique_medium_success and actor not in unique_full_success]
    if failed_actors: log_step(f"Failed ALL image types for: {', '.join(failed_actors)}", level="warning"); return True # Partial success
    elif processed_actors_count == 0: log_step("No actors processed.", level="warning"); return False
    else:
        all_succeeded_both = all( (actor in unique_medium_success and actor in unique_full_success) for actor in all_actors_processed_with_source )
        if all_succeeded_both: log_step("Completed: Both image types generated for all processed actors.", level="success")
        else: log_step("Completed: Some actors might be missing one image type.", level="warning")
        return True

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate character images using ComfyUI."); parser.add_argument("-p", "--project_path", help="Path to project folder. Omit for latest.")
    args = parser.parse_args(); log_step("===== Generate Images Script Started =====", important=True)
    try: load_dotenv(dotenv_path=config.DOTENV_PATH)
    except Exception as e: log_step(f"Note: .env load error: {e}", level="debug")

    if not COMFYUI_INPUT_DIR or not COMFYUI_OUTPUT_DIR or not IMAGE_WORKFLOW_TEMPLATE: log_step("Critical Error: ComfyUI paths/template not configured.", level="error", important=True); exit(1)
    if not Path(COMFYUI_INPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_INPUT_DIR invalid: {COMFYUI_INPUT_DIR}", level="error"); exit(1)
    if not Path(COMFYUI_OUTPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_OUTPUT_DIR invalid: {COMFYUI_OUTPUT_DIR}", level="error"); exit(1)
    if not Path(IMAGE_WORKFLOW_TEMPLATE).is_file() : log_step(f"Error: IMAGE_WORKFLOW_TEMPLATE not found: {IMAGE_WORKFLOW_TEMPLATE}", level="error"); exit(1)

    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not project_to_process.parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", level="error", important=True); exit(1)
    log_step(f"Using project: {project_to_process}")

    project_theme = extract_theme_from_project_path(project_to_process)
    if not project_theme or project_theme == "Default Futuristic Theme": log_step(f"Cannot determine theme from: {project_to_process}", level="error"); exit(1)

    if not hasattr(comfyui_interactions, 'load_workflow_template') or \
       not hasattr(comfyui_interactions, 'modify_workflow_inputs') or \
       not hasattr(comfyui_interactions, 'run_comfyui_workflow'):
        log_step("Critical Error: Essential functions missing from comfyui_interactions.py!", level="error", important=True); exit(1)

    success = run_image_generation(project_to_process, project_theme)

    if success: log_step("Script finished successfully or partially.", level="success", important=True); exit(0)
    else: log_step("Script finished with critical errors or no generation attempted.", level="error", important=True); exit(1)