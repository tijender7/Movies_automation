# generate_images_comfyui_mythic_v1.py
# Generates images scene-by-scene based on image_prompts.json from mythic_movie_project output
# Uses config_mythic_v1.py for configuration

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
import traceback
import utils

# --- Attempt to import project modules ---
try:
    # --- Load Configuration ---
    import config_mythic_v1 as config # <<< CHANGED
    # --- ComfyUI Interaction Module ---
    import comfyui_interactions as comfyui_interactions
    # --- Helper Functions (Assuming they are moved to utils.py or adjust path) ---
    # from mythic_movie_project import find_latest_project_dir # Example if helpers in script 1
    # Best practice: Move helpers to a shared utils.py
    from utils import find_latest_project_dir # Assuming utils.py exists
    # extract_theme_from_project_path might not be needed if theme isn't used here
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules: {e}")
    print("Please ensure 'config_mythic_v1.py', 'comfyui_interactions.py', and helper functions "
          "(e.g., 'find_latest_project_dir' in 'utils.py' or accessible) are available.")
    exit(1)

# --- Constants from Config ---
LOG_FILE = config.IMAGE_GEN_LOG_FILE # <<< Use specific log file from config
PROJECTS_BASE_DIR = config.PROJECTS_BASE_DIR

# --- Input Filenames within Project Folder (from config) ---
CHARACTERS_JSON_FILENAME = config.CHARACTERS_FILENAME
SCENES_JSON_FILENAME = config.SCENES_FILENAME
IMAGE_PROMPTS_JSON_FILENAME = config.IMAGE_PROMPTS_FILENAME
SOURCE_ACTORS_FOLDER_NAME = config.SOURCE_ACTORS_FOLDER_NAME

# --- ComfyUI Settings (from config) ---
COMFYUI_INPUT_DIR = config.COMFYUI_INPUT_DIR
COMFYUI_OUTPUT_DIR = config.COMFYUI_OUTPUT_DIR
API_OUTPUTS_SUBDIR = config.API_OUTPUTS_SUBDIR
IMAGE_WORKFLOW_TEMPLATE = config.IMAGE_WORKFLOW_TEMPLATE # Crucial: Path to image gen workflow

# --- ComfyUI Node Titles (from config - MUST match IMAGE_WORKFLOW_TEMPLATE) ---
PROMPT_NODE_TITLE = config.PROMPT_NODE_TITLE
SEED_NODE_TITLE = config.SEED_NODE_TITLE
FACE_NODE_TITLE = config.FACE_NODE_TITLE # Optional face input node
SAVE_BASE_IMAGE_NODE_TITLE = config.SAVE_BASE_IMAGE_NODE_TITLE # Primary save node
SAVE_SWAPPED_IMAGE_NODE_TITLE = config.SAVE_SWAPPED_IMAGE_NODE_TITLE # Optional secondary save node

# --- Output Subfolder & Prefix (from config) ---
GENERATED_SCENES_SUBFOLDER = config.GENERATED_SCENES_SUBFOLDER
SCENE_IMAGE_PREFIX = config.SCENE_IMAGE_PREFIX
DOTENV_PATH = config.DOTENV_PATH # Use dotenv path from config

# --- Logging Setup ---
log_file_path = Path(LOG_FILE) # Use LOG_FILE defined above
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=config.LOG_LEVEL, # Use level from config
        format=config.LOG_FORMAT, # Use format from config
        handlers=[
            logging.FileHandler(log_file_path, mode='a', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Suppress overly verbose logs from libraries if needed
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
except Exception as e:
    print(f"ERROR setting up logging to {log_file_path}: {e}")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()])

log = logging.getLogger(__name__)

# --- Helper Functions (Keep as is - log_step, copy_to_comfyui_input, sanitize_name, find_source_images) ---
# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}"
    if important: final_msg = f"--- {final_msg} ---"
    getattr(log, level, log.info)(final_msg)

# --- Helper: Copy Source Face to ComfyUI Input ---
def copy_to_comfyui_input(source_file_path: Path):
    # Uses COMFYUI_INPUT_DIR from config
    if not COMFYUI_INPUT_DIR:
        log_step("COMFYUI_INPUT_DIR is not configured. Cannot copy source image.", level="error"); return False
    comfy_input_path = Path(COMFYUI_INPUT_DIR)
    if not comfy_input_path.is_dir():
        log_step(f"ComfyUI Input Dir is invalid or not found: {comfy_input_path}", level="error"); return False
    if not source_file_path or not source_file_path.is_file():
        log_step(f"Source image path is invalid or file not found: {source_file_path}", level="error"); return False

    try:
        destination_path = comfy_input_path / source_file_path.name
        shutil.copy2(source_file_path, destination_path)
        log_step(f" Copied '{source_file_path.name}' to ComfyUI input directory.", level="debug")
        return True
    except Exception as e:
        log_step(f"Error copying '{source_file_path.name}' to {comfy_input_path}: {e}", level="error")
        return False

# --- Helper: Sanitize Name (Consistent with Script 1) ---
def sanitize_name(name):
    # Keep implementation from previous version
    if not isinstance(name, str): name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name)
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip()
    sanitized = sanitized.replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized)
    sanitized = re.sub(r'[-]+', '-', sanitized)
    sanitized = sanitized.strip('_-')
    max_len = 60
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Helper: Find Source Images using Glob ---
def find_source_images(actor_name: str, source_actors_dir: Path):
    # Keep implementation from previous version
    if not actor_name or not source_actors_dir.is_dir():
        return []
    pattern = str(source_actors_dir / f"{actor_name}_*.[jp][pn]g") # Assumes exact actor_name used in filename
    log_step(f" Searching for source images with pattern: {pattern}", level="debug")
    try:
        files = glob.glob(pattern)
        log_step(f" Glob found: {files}", level="debug")
        return [Path(f) for f in files]
    except Exception as e:
        log_step(f"Error during glob search for '{actor_name}': {e}", level="warning")
        return []


# --- Main Image Generation Logic (Keep core logic, ensure config variables are used) ---
def run_image_generation(project_path: Path):
    log_step(f"--- Starting Scene Image Generation for Project: {project_path.name} ---", important=True)

    # --- Define expected file paths using config constants ---
    scene_prompts_path = project_path / IMAGE_PROMPTS_JSON_FILENAME
    scenes_info_path = project_path / SCENES_JSON_FILENAME
    characters_path = project_path / CHARACTERS_JSON_FILENAME
    project_source_actors_path = project_path / SOURCE_ACTORS_FOLDER_NAME # Uses config name

    # --- Load Scene Prompts (Essential) ---
    # (Keep loading logic, uses IMAGE_PROMPTS_JSON_FILENAME)
    if not scene_prompts_path.is_file():
        log_step(f"CRITICAL: Scene prompts file ({IMAGE_PROMPTS_JSON_FILENAME}) not found: {scene_prompts_path}", level="error"); return False
    try:
        with open(scene_prompts_path, 'r', encoding='utf-8') as f: scene_prompts_data = json.load(f)
        if not isinstance(scene_prompts_data, list): raise ValueError(f"{IMAGE_PROMPTS_JSON_FILENAME} must contain a list.")
        log_step(f"Loaded {len(scene_prompts_data)} scene prompts from {scene_prompts_path.name}", level="success")
    except Exception as e:
        log_step(f"CRITICAL: Error loading or parsing {scene_prompts_path.name}: {e}", level="error"); return False


    # --- Load Scene Details (Optional - for face swap context) ---
    # (Keep loading logic, uses SCENES_JSON_FILENAME)
    scenes_info_list = []
    if scenes_info_path.is_file():
        try:
            with open(scenes_info_path, 'r', encoding='utf-8') as f: scenes_data = json.load(f)
            if isinstance(scenes_data, dict) and 'scenes' in scenes_data and isinstance(scenes_data['scenes'], list):
                scenes_info_list = scenes_data['scenes']
                log_step(f"Loaded {len(scenes_info_list)} scene details from {scenes_info_path.name}", level="debug")
            else:
                 log_step(f"Warning: Structure of {scenes_info_path.name} is not the expected {{'scenes': [...]}}.", level="warning")
        except Exception as e:
            log_step(f"Warning: Could not load or parse {scenes_info_path.name}: {e}", level="warning")
    else:
        log_step(f"Info: {scenes_info_path.name} not found. Face swap based on focus character disabled.", level="info")


    # --- Load Character/Actor Mapping (Optional - for face swap) ---
    # (Keep loading logic, uses CHARACTERS_JSON_FILENAME)
    character_to_actor_map = {}
    if characters_path.is_file():
        try:
            with open(characters_path, 'r', encoding='utf-8') as f: characters_data = json.load(f)
            char_list = []
            if isinstance(characters_data, dict) and 'characters' in characters_data and isinstance(characters_data['characters'], list):
                 char_list = characters_data['characters']
            elif isinstance(characters_data, list):
                 char_list = characters_data

            for char_info in char_list:
                if isinstance(char_info, dict):
                    char_name = char_info.get('name')
                    actor_name = char_info.get('actor_name') # Still requires manual addition
                    if char_name and actor_name:
                        character_to_actor_map[char_name] = actor_name
                    elif char_name and not actor_name:
                         log_step(f" Character '{char_name}' in {characters_path.name} missing 'actor_name' field.", level="debug")

            if character_to_actor_map:
                 log_step(f"Loaded character-to-actor map for {len(character_to_actor_map)} characters from {characters_path.name}", level="debug")
            else:
                 log_step(f"Warning: No characters with 'actor_name' found in {characters_path.name}. Face swap disabled.", level="warning")

        except Exception as e:
            log_step(f"Warning: Could not load or parse {characters_path.name} for actor mapping: {e}", level="warning")
    else:
        log_step(f"Info: {characters_path.name} not found. Face swap disabled.", level="info")


    # --- Prepare for ComfyUI ---
    # (Keep validation, uses IMAGE_WORKFLOW_TEMPLATE from config)
    if not IMAGE_WORKFLOW_TEMPLATE or not Path(IMAGE_WORKFLOW_TEMPLATE).is_file():
        log_step(f"CRITICAL: IMAGE_WORKFLOW_TEMPLATE '{IMAGE_WORKFLOW_TEMPLATE}' not found or not configured.", level="error"); return False
    workflow_template = comfyui_interactions.load_workflow_template(IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template:
        log_step(f"CRITICAL: Failed to load workflow template from {IMAGE_WORKFLOW_TEMPLATE}", level="error"); return False


    # --- Setup ComfyUI Output Directory ---
    # (Keep logic, uses API_OUTPUTS_SUBDIR, COMFYUI_OUTPUT_DIR from config)
    run_timestamp = datetime.now().strftime(config.TIMESTAMP_FORMAT) # Use config format
    comfy_api_run_subfolder_rel = Path(API_OUTPUTS_SUBDIR) / f"{project_path.name}_{run_timestamp}"
    comfy_api_run_folder_abs = Path(COMFYUI_OUTPUT_DIR) / comfy_api_run_subfolder_rel
    log_step(f"ComfyUI output base directory for this run (absolute): {comfy_api_run_folder_abs}")
    log_step(f"ComfyUI nodes will use relative output path: {comfy_api_run_subfolder_rel}")


    # --- Initialize Counters ---
    generated_scene_count = 0
    failed_scene_count = 0
    at_least_one_comfy_run_started = False

    # --- Main Scene Generation Loop ---
    # (Keep core loop logic)
    for scene_prompt_info in scene_prompts_data:
        scene_number = scene_prompt_info.get("scene_number")
        scene_prompt = scene_prompt_info.get("prompt_text")

        if scene_number is None or not scene_prompt:
            log_step(f"Skipping invalid scene entry: {scene_prompt_info}", level="warning")
            continue

        log_step(f"\n--- Processing Scene {scene_number} ---")
        at_least_one_comfy_run_started = True
        source_image_filename_for_workflow = None
        face_swap_actor = None

        # --- Determine Face Swap Target (if possible) ---
        # (Keep logic, uses FACE_NODE_TITLE, find_source_images, copy_to_comfyui_input)
        if scenes_info_list and character_to_actor_map and FACE_NODE_TITLE:
            # ... (face swap logic remains the same) ...
             focus_character = None
             for scene_detail in scenes_info_list: # Find focus character
                 if scene_detail.get('scene_number') == scene_number:
                     focus_character = scene_detail.get('focus_character')
                     break
             if focus_character:
                 face_swap_actor = character_to_actor_map.get(focus_character) # Get actor name
                 if face_swap_actor:
                     source_images = find_source_images(face_swap_actor, project_source_actors_path) # Find image
                     if source_images:
                         source_image_to_use = source_images[0]
                         log_step(f"  Found source image: {source_image_to_use.name}", level="success")
                         if copy_to_comfyui_input(source_image_to_use): # Copy image
                             source_image_filename_for_workflow = source_image_to_use.name
                             log_step(f"  Successfully copied. Will attempt face swap with {source_image_filename_for_workflow}.")
                         else: log_step("  Failed to copy source image. Skipping face swap.", level="warning")
                     else: log_step(f"  Source image pattern NOT FOUND for Actor '{face_swap_actor}'. Skipping face swap.", level='warning')
                 else: log_step(f"  Focus character '{focus_character}' not mapped to an actor. Skipping face swap.", level="warning")
             else: log_step(f" Scene {scene_number}: No focus character specified. Skipping face swap.", level="debug")
        # ... (rest of face swap condition checks) ...


        # --- Generate Image with ComfyUI ---
        try:
            seed = random.randint(0, 2**32 - 1)
            current_workflow = copy.deepcopy(workflow_template)
            if not current_workflow: raise ValueError("Workflow deepcopy failed.")

            # --- Define Output Path and Filename for Save Nodes ---
            # Uses GENERATED_SCENES_SUBFOLDER, SCENE_IMAGE_PREFIX from config
            output_dir_for_node = comfy_api_run_subfolder_rel / GENERATED_SCENES_SUBFOLDER
            filename_prefix_for_node = f"{SCENE_IMAGE_PREFIX}{scene_number:03d}_"

            # --- Prepare Inputs for Workflow Modification ---
            # Uses PROMPT_NODE_TITLE, SEED_NODE_TITLE, FACE_NODE_TITLE,
            # SAVE_BASE_IMAGE_NODE_TITLE, SAVE_SWAPPED_IMAGE_NODE_TITLE from config
            inputs_to_set = {}
            if PROMPT_NODE_TITLE: inputs_to_set[PROMPT_NODE_TITLE] = {"text": scene_prompt}
            else: log_step("Warning: PROMPT_NODE_TITLE not set in config.", level="warning")
            if SEED_NODE_TITLE: inputs_to_set[SEED_NODE_TITLE] = {"noise_seed": seed, "seed": seed}
            else: log_step("Warning: SEED_NODE_TITLE not set in config.", level="warning")

            if FACE_NODE_TITLE and source_image_filename_for_workflow:
                inputs_to_set[FACE_NODE_TITLE] = {"image": source_image_filename_for_workflow}
                log_step(f" Setting face input '{FACE_NODE_TITLE}'.", level="debug")
            # ... (rest of input preparation) ...

            # Configure save nodes using config titles
            if SAVE_BASE_IMAGE_NODE_TITLE:
                inputs_to_set[SAVE_BASE_IMAGE_NODE_TITLE] = {
                    "filename_prefix": filename_prefix_for_node,
                    "directory": output_dir_for_node.as_posix(),
                }
                log_step(f" Setting output for base node '{SAVE_BASE_IMAGE_NODE_TITLE}'.", level="debug")
            else: log_step("CRITICAL: SAVE_BASE_IMAGE_NODE_TITLE not configured!", level="error"); raise ValueError("Base save node not configured.")

            if SAVE_SWAPPED_IMAGE_NODE_TITLE and SAVE_SWAPPED_IMAGE_NODE_TITLE != SAVE_BASE_IMAGE_NODE_TITLE:
                inputs_to_set[SAVE_SWAPPED_IMAGE_NODE_TITLE] = {
                     "filename_prefix": filename_prefix_for_node, # Or adjust prefix if needed
                     "directory": output_dir_for_node.as_posix(),
                 }
                log_step(f" Setting output for swapped node '{SAVE_SWAPPED_IMAGE_NODE_TITLE}'.", level="debug")


            # --- Modify and Run Workflow ---
            # (Keep logic, uses comfyui_interactions)
            log_step(f" Modifying workflow inputs for Scene {scene_number}...");
            if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                raise ValueError("Failed to modify workflow inputs.")

            log_step(f" Queueing workflow for Scene {scene_number} (Seed: {seed}).");
            comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=900) # Timeout could be configurable

            if not comfyui_result_history: raise RuntimeError("ComfyUI workflow failed or timed out.")
            else:
                log_step(f" ComfyUI workflow COMPLETED for Scene {scene_number}.", level="success")
                generated_scene_count += 1

        except Exception as e:
            log_step(f"ERROR processing Scene {scene_number}: {e}", level="error")
            log.error(traceback.format_exc())
            failed_scene_count += 1

    # --- End Scene Loop ---

    # --- Final Summary ---
    # (Keep logic)
    log_step("\n--- Finished Scene Image Generation Phase ---", important=True)
    if not at_least_one_comfy_run_started:
        log_step("No ComfyUI runs were attempted.", level="warning"); return False

    total_scenes_processed = generated_scene_count + failed_scene_count
    total_prompts_available = len(scene_prompts_data)
    log_step(f"Attempted: {total_scenes_processed}/{total_prompts_available} scenes.")
    log_step(f"Success: {generated_scene_count} scenes.")
    log_step(f"Failed: {failed_scene_count} scenes.")
    log_step(f"Outputs saved within: {comfy_api_run_folder_abs}")
    log_step(f"-> Relative path: {comfy_api_run_subfolder_rel / GENERATED_SCENES_SUBFOLDER}")

    # Determine overall success based on whether *any* images were generated
    return generated_scene_count > 0


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate scene images for a mythic_movie project using ComfyUI.")
    parser.add_argument( "-p", "--project_path", help="Path to the specific project folder. Omit to use the latest.")
    args = parser.parse_args()

    log_step("===== Generate Scene Images Script Started (Mythic Workflow) =====", important=True)

    # --- Load Environment Variables ---
    # Uses DOTENV_PATH from config
    try:
        if DOTENV_PATH and Path(DOTENV_PATH).is_file():
            load_dotenv(dotenv_path=DOTENV_PATH)
            log_step(f"Loaded environment variables from {DOTENV_PATH}", level="debug")
        elif DOTENV_PATH:
             log_step(f".env file specified but not found at {DOTENV_PATH}", level="debug")
    except Exception as e:
        log_step(f"Note: Error loading .env file: {e}", level="debug")

    # --- Validate Essential ComfyUI Configuration from config ---
    # (Keep validation logic)
    essential_configs = {
        "COMFYUI_INPUT_DIR": COMFYUI_INPUT_DIR,
        "COMFYUI_OUTPUT_DIR": COMFYUI_OUTPUT_DIR,
        "IMAGE_WORKFLOW_TEMPLATE": IMAGE_WORKFLOW_TEMPLATE,
        "SAVE_BASE_IMAGE_NODE_TITLE": SAVE_BASE_IMAGE_NODE_TITLE # Base save node is essential
    }
    missing_essentials = False
    for name, value in essential_configs.items():
        if not value:
            log_step(f"CRITICAL Error: '{name}' is not configured in config_mythic_v1.py.", level="error", important=True); missing_essentials = True
        elif name.endswith("_DIR") and not Path(value).is_dir():
             log_step(f"CRITICAL Error: Configured {name} path '{value}' is not valid.", level="error", important=True); missing_essentials = True
        elif name.endswith("_TEMPLATE") and not Path(value).is_file():
             log_step(f"CRITICAL Error: Configured {name} file '{value}' not found.", level="error", important=True); missing_essentials = True
    if missing_essentials: exit(1)


    # --- Determine Project Path ---
    # (Keep logic, uses PROJECTS_BASE_DIR and PROJECT_PREFIX from config)
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        if not project_to_process.is_dir():
             log_step(f"Error: Provided project path does not exist: {project_to_process}", level="error", important=True); exit(1)
        try: # Sanity check parent dir
            if not project_to_process.parent.samefile(PROJECTS_BASE_DIR):
                log_step(f"Warning: Provided path '{project_to_process.name}' parent is not the configured PROJECTS_BASE_DIR '{PROJECTS_BASE_DIR}'.", level="warning")
        except Exception: pass # Ignore errors comparing paths if needed
    else:
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR} with prefix '{config.PROJECT_PREFIX}'...")
        if not PROJECTS_BASE_DIR.is_dir():
             log_step(f"Error: PROJECTS_BASE_DIR '{PROJECTS_BASE_DIR}' not found.", level="error", important=True); exit(1)
        # Use project_prefix from config in find_latest_project_dir
        project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR, project_prefix=config.PROJECT_PREFIX)
        if not project_to_process:
            log_step(f"Error: No suitable project found in {PROJECTS_BASE_DIR} with prefix '{config.PROJECT_PREFIX}'.", level="error", important=True); exit(1)

    log_step(f"Using project: {project_to_process.name}")
    log_step(f"Full path: {project_to_process.resolve()}")


    # --- Validate Helper Modules Availability ---
    # (Keep validation logic for comfyui_interactions)
    required_comfy_funcs = ['load_workflow_template', 'modify_workflow_inputs', 'run_comfyui_workflow']
    missing_funcs = [f for f in required_comfy_funcs if not hasattr(comfyui_interactions, f)]
    if missing_funcs:
        log_step(f"CRITICAL Error: Essential function(s) missing from comfyui_interactions: {', '.join(missing_funcs)}", level="error", important=True); exit(1)


    # --- Run Main Logic ---
    success = False
    try:
        success = run_image_generation(project_to_process)
    except Exception as e:
        log_step(f"An unexpected critical error occurred: {e}", level="error", important=True)
        log.error(traceback.format_exc())
        success = False

    # --- Exit ---
    if success:
        log_step("Script finished successfully or partially.", level="success", important=True); exit(0)
    else:
        log_step("Script finished with critical errors or no generation completed.", level="error", important=True); exit(1)