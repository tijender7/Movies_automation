# generate_images_comfyui.py v3 (Adapted for mythic_movie_project output)
# Generates images scene-by-scene based on image_prompts.json
# Optional face-swapping based on scenes.json and characters.json

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

# --- Attempt to import project modules ---
try:
    # Assuming these modules/files exist and are correctly configured
    import config_v2 as config
    import comfyui_interactions as comfyui_interactions
    # Assuming these helpers are in a place accessible via PYTHONPATH or same dir
    # If they are in the first script's file, you might need to refactor them into a shared 'utils.py'
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules: {e}")
    print("Please ensure 'config_v2.py', 'comfyui_interactions.py', and helpers "
          "('extract_theme_from_project_path', 'find_latest_project_dir') are accessible.")
    exit(1)

# --- Constants from Config (or defaults) ---
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "comfyui_scene_gen.log") # Changed log filename
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path("mythic_movie_project")) # Should point to the PARENT of project folders

# --- Input Filenames within Project Folder ---
CHARACTERS_JSON_FILENAME = "characters.json"
SCENES_JSON_FILENAME = "scenes.json"
IMAGE_PROMPTS_JSON_FILENAME = "image_prompts.json" # Main driver
SOURCE_ACTORS_FOLDER_NAME = getattr(config, 'SOURCE_ACTORS_FOLDER_NAME', "source_actors") # Folder inside project dir

# --- ComfyUI Settings ---
COMFYUI_INPUT_DIR = getattr(config, 'COMFYUI_INPUT_DIR', None)
COMFYUI_OUTPUT_DIR = getattr(config, 'COMFYUI_OUTPUT_DIR', None)
API_OUTPUTS_SUBDIR = getattr(config, 'API_OUTPUTS_SUBDIR', "API_OUTPUTS") # Subfolder within ComfyUI output
IMAGE_WORKFLOW_TEMPLATE = getattr(config, 'IMAGE_WORKFLOW_TEMPLATE', None) # Path to workflow JSON

# --- ComfyUI Node Titles (Ensure these match your workflow exactly) ---
PROMPT_NODE_TITLE = getattr(config, 'PROMPT_NODE_TITLE', 'API_Prompt_Input') # Text prompt input
SEED_NODE_TITLE = getattr(config, 'SEED_NODE_TITLE', 'API_Seed_Input') # Seed input
FACE_NODE_TITLE = getattr(config, 'FACE_NODE_TITLE', 'API_Face_Input') # Face image input (optional)

# --- ComfyUI Save Node Titles & Config ---
# Define titles for nodes that save images (e.g., SaveImage, VHS_SaveImage 등)
# If your workflow only has one save node, set both to the same or leave one as None
SAVE_BASE_IMAGE_NODE_TITLE = getattr(config, 'SAVE_BASE_IMAGE_NODE_TITLE', 'API_Save_Image') # Main/Base save node
SAVE_SWAPPED_IMAGE_NODE_TITLE = getattr(config, 'SAVE_SWAPPED_IMAGE_NODE_TITLE', None) # Optional swapped save node

# --- Output Subfolder & Prefix ---
GENERATED_SCENES_SUBFOLDER = "generated_scenes" # Subfolder within the run's output dir
SCENE_IMAGE_PREFIX = "scene_"

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, mode='a', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Suppress overly verbose logs from libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
    # Add others as needed: 'tensorflow', 'werkzeug'
except Exception as e:
    print(f"ERROR setting up logging: {e}")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()])

log = logging.getLogger(__name__)

# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}"
    if important: final_msg = f"--- {final_msg} ---"
    getattr(log, level, log.info)(final_msg) # Use getattr to call log method

# --- Helper: Copy Source Face to ComfyUI Input ---
def copy_to_comfyui_input(source_file_path: Path):
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
        return True # Return True on success
    except Exception as e:
        log_step(f"Error copying '{source_file_path.name}' to {comfy_input_path}: {e}", level="error")
        return False

# --- Helper: Sanitize Name (Consistent with Script 1) ---
def sanitize_name(name):
    if not isinstance(name, str): name = str(name)
    # Allow alphanumeric, space, underscore, hyphen. Replace others with underscore.
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name)
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip()
    # Replace spaces with underscores, collapse multiple underscores/hyphens
    sanitized = sanitized.replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized)
    sanitized = re.sub(r'[-]+', '-', sanitized)
    # Strip leading/trailing underscores/hyphens
    sanitized = sanitized.strip('_-')
    # Limit length (optional, good practice for filenames)
    max_len = 60
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Helper: Find Source Images using Glob ---
def find_source_images(actor_name: str, source_actors_dir: Path):
    """Finds image files matching ActorName_*.png/jpg in the source directory."""
    if not actor_name or not source_actors_dir.is_dir():
        return []
    # Sanitize actor name for file searching robustness, though exact match might be better if users are precise
    # Let's assume the actor_name from characters.json IS the base filename part
    # pattern = str(source_actors_dir / f"{sanitize_name(actor_name)}_*.[jp][pn]g") # Sanitized version
    pattern = str(source_actors_dir / f"{actor_name}_*.[jp][pn]g") # Exact name version
    log_step(f" Searching for source images with pattern: {pattern}", level="debug")
    try:
        files = glob.glob(pattern)
        log_step(f" Glob found: {files}", level="debug")
        return [Path(f) for f in files] # Return list of Path objects
    except Exception as e:
        log_step(f"Error during glob search for '{actor_name}': {e}", level="warning")
        return []

# --- Main Image Generation Logic ---
def run_image_generation(project_path: Path):
    log_step(f"--- Starting Scene Image Generation for Project: {project_path.name} ---", important=True)

    # --- Define expected file paths ---
    scene_prompts_path = project_path / IMAGE_PROMPTS_JSON_FILENAME
    scenes_info_path = project_path / SCENES_JSON_FILENAME
    characters_path = project_path / CHARACTERS_JSON_FILENAME
    project_source_actors_path = project_path / SOURCE_ACTORS_FOLDER_NAME

    # --- Load Scene Prompts (Essential) ---
    if not scene_prompts_path.is_file():
        log_step(f"CRITICAL: Scene prompts file not found: {scene_prompts_path}", level="error"); return False
    try:
        with open(scene_prompts_path, 'r', encoding='utf-8') as f: scene_prompts_data = json.load(f)
        if not isinstance(scene_prompts_data, list): raise ValueError("Scene prompts JSON must be a list.")
        log_step(f"Loaded {len(scene_prompts_data)} scene prompts from {scene_prompts_path.name}", level="success")
    except Exception as e:
        log_step(f"CRITICAL: Error loading or parsing {scene_prompts_path.name}: {e}", level="error"); return False

    # --- Load Scene Details (Optional - for face swap context) ---
    scenes_info_list = []
    if scenes_info_path.is_file():
        try:
            with open(scenes_info_path, 'r', encoding='utf-8') as f: scenes_data = json.load(f)
            # Expecting {"scenes": [...]} structure from Script 1
            if isinstance(scenes_data, dict) and 'scenes' in scenes_data and isinstance(scenes_data['scenes'], list):
                scenes_info_list = scenes_data['scenes']
                log_step(f"Loaded {len(scenes_info_list)} scene details from {scenes_info_path.name}", level="debug")
            else:
                 log_step(f"Warning: Structure of {scenes_info_path.name} is not the expected {{'scenes': [...]}}. Cannot get focus characters.", level="warning")
        except Exception as e:
            log_step(f"Warning: Could not load or parse {scenes_info_path.name}: {e}", level="warning")
    else:
        log_step(f"Info: {scenes_info_path.name} not found. Proceeding without focus character info (face swap based on focus character disabled).", level="info")

    # --- Load Character/Actor Mapping (Optional - for face swap) ---
    character_to_actor_map = {}
    if characters_path.is_file():
        try:
            with open(characters_path, 'r', encoding='utf-8') as f: characters_data = json.load(f)
            # Expecting {"characters": [{"name": "...", "actor_name": "..."}, ...]} structure
            # Or potentially a flat list [{...}, {...}]
            char_list = []
            if isinstance(characters_data, dict) and 'characters' in characters_data and isinstance(characters_data['characters'], list):
                 char_list = characters_data['characters']
            elif isinstance(characters_data, list):
                 char_list = characters_data # Assume flat list if not the dict structure

            for char_info in char_list:
                if isinstance(char_info, dict):
                    char_name = char_info.get('name') # Key from Script 1's output
                    actor_name = char_info.get('actor_name') # *** This key MUST be added manually or by a prior step ***
                    if char_name and actor_name:
                        character_to_actor_map[char_name] = actor_name
                    elif char_name and not actor_name:
                         log_step(f" Character '{char_name}' found in {characters_path.name}, but missing 'actor_name' field needed for face swap.", level="debug")

            if character_to_actor_map:
                 log_step(f"Loaded character-to-actor map for {len(character_to_actor_map)} characters from {characters_path.name}", level="debug")
            else:
                 log_step(f"Warning: No characters with 'actor_name' field found in {characters_path.name}. Face swap based on focus character disabled.", level="warning")

        except Exception as e:
            log_step(f"Warning: Could not load or parse {characters_path.name} for actor mapping: {e}", level="warning")
    else:
        log_step(f"Info: {characters_path.name} not found. Face swap based on focus character disabled.", level="info")


    # --- Prepare for ComfyUI ---
    if not IMAGE_WORKFLOW_TEMPLATE or not Path(IMAGE_WORKFLOW_TEMPLATE).is_file():
        log_step(f"CRITICAL: IMAGE_WORKFLOW_TEMPLATE '{IMAGE_WORKFLOW_TEMPLATE}' not found or not configured.", level="error"); return False

    workflow_template = comfyui_interactions.load_workflow_template(IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template:
        log_step(f"CRITICAL: Failed to load workflow template from {IMAGE_WORKFLOW_TEMPLATE}", level="error"); return False

    # --- Setup ComfyUI Output Directory ---
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Relative path within ComfyUI's main output folder
    comfy_api_run_subfolder_rel = Path(API_OUTPUTS_SUBDIR) / f"{project_path.name}_{run_timestamp}"
    # Absolute path (mainly for logging clarity, ComfyUI uses relative paths in save nodes)
    comfy_api_run_folder_abs = Path(COMFYUI_OUTPUT_DIR) / comfy_api_run_subfolder_rel
    log_step(f"ComfyUI output base directory for this run (absolute): {comfy_api_run_folder_abs}")
    log_step(f"ComfyUI nodes will use relative output path: {comfy_api_run_subfolder_rel}")

    # --- Initialize Counters ---
    generated_scene_count = 0
    failed_scene_count = 0
    at_least_one_comfy_run_started = False

    # --- Main Scene Generation Loop ---
    for scene_prompt_info in scene_prompts_data:
        scene_number = scene_prompt_info.get("scene_number")
        scene_prompt = scene_prompt_info.get("prompt_text")

        if scene_number is None or not scene_prompt:
            log_step(f"Skipping invalid scene entry in prompts file: {scene_prompt_info}", level="warning")
            continue

        log_step(f"\n--- Processing Scene {scene_number} ---")
        at_least_one_comfy_run_started = True # Mark that we are attempting a run
        source_image_filename_for_workflow = None # The filename to pass to the workflow input
        face_swap_actor = None

        # --- Determine Face Swap Target (if possible) ---
        if scenes_info_list and character_to_actor_map and FACE_NODE_TITLE:
            focus_character = None
            # Find scene details matching current scene number
            for scene_detail in scenes_info_list:
                if scene_detail.get('scene_number') == scene_number:
                    focus_character = scene_detail.get('focus_character')
                    break # Found the scene

            if focus_character:
                log_step(f" Scene {scene_number}: Focus character is '{focus_character}'.")
                face_swap_actor = character_to_actor_map.get(focus_character)
                if face_swap_actor:
                    log_step(f"  Mapped to Actor: '{face_swap_actor}'. Searching for source image...")
                    source_images = find_source_images(face_swap_actor, project_source_actors_path)
                    if source_images:
                        source_image_to_use = source_images[0] # Use the first found image
                        log_step(f"  Found source image: {source_image_to_use.name}", level="success")
                        if len(source_images) > 1:
                             log_step(f"  Warning: Found multiple source images for '{face_swap_actor}'. Using the first one found.", level="warning")

                        # Try to copy to ComfyUI input dir
                        if copy_to_comfyui_input(source_image_to_use):
                            source_image_filename_for_workflow = source_image_to_use.name # Use only filename for workflow input
                            log_step(f"  Successfully copied. Will attempt face swap with {source_image_filename_for_workflow}.")
                        else:
                             log_step(f"  Failed to copy source image. Skipping face swap for this scene.", level="warning")
                    else:
                        log_step(f"  Source image pattern NOT FOUND for Actor '{face_swap_actor}' in {project_source_actors_path}. Skipping face swap.", level='warning')
                else:
                     log_step(f"  Focus character '{focus_character}' is not mapped to an actor in {characters_path.name}. Skipping face swap.", level="warning")
            else:
                log_step(f" Scene {scene_number}: No 'focus_character' specified in {scenes_info_path.name}. Skipping face swap.", level="debug")
        elif not FACE_NODE_TITLE:
             log_step(f" Scene {scene_number}: FACE_NODE_TITLE not configured. Face swap disabled.", level="debug")
        elif not scenes_info_list or not character_to_actor_map :
             log_step(f" Scene {scene_number}: Missing scene details or character map. Face swap disabled.", level="debug")


        # --- Generate Image with ComfyUI ---
        try:
            seed = random.randint(0, 2**32 - 1)
            current_workflow = copy.deepcopy(workflow_template)
            if not current_workflow: raise ValueError("Workflow deepcopy failed.")

            # --- Define Output Path and Filename for Save Nodes ---
            # This is the path *relative* to ComfyUI's output directory that the Save node will use
            output_dir_for_node = comfy_api_run_subfolder_rel / GENERATED_SCENES_SUBFOLDER
            filename_prefix_for_node = f"{SCENE_IMAGE_PREFIX}{scene_number:03d}_" # e.g., scene_001_

            # --- Prepare Inputs for Workflow Modification ---
            inputs_to_set = {}

            # --- Basic Inputs (Prompt, Seed) ---
            if PROMPT_NODE_TITLE:
                inputs_to_set[PROMPT_NODE_TITLE] = {"text": scene_prompt}
            else: log_step("Warning: PROMPT_NODE_TITLE not configured. Cannot set prompt.", level="warning")

            if SEED_NODE_TITLE:
                 inputs_to_set[SEED_NODE_TITLE] = {"noise_seed": seed, "seed": seed} # Set common seed parameters
            else: log_step("Warning: SEED_NODE_TITLE not configured. Cannot set seed.", level="warning")

            # --- Face Input (Conditional) ---
            if FACE_NODE_TITLE and source_image_filename_for_workflow:
                inputs_to_set[FACE_NODE_TITLE] = {"image": source_image_filename_for_workflow}
                log_step(f" Setting face input '{FACE_NODE_TITLE}' to image: {source_image_filename_for_workflow}", level="debug")
            elif FACE_NODE_TITLE:
                 # If face node exists but we have no image, ensure workflow handles it
                 # (e.g., provide a default/null input if needed, or ensure ReActor/IPAdapter is bypassed)
                 log_step(f" Face node '{FACE_NODE_TITLE}' exists but no source image provided/copied for this scene.", level="debug")
                 # Example: provide None if the node takes it, or modify workflow differently
                 # inputs_to_set[FACE_NODE_TITLE] = {"image": None} # <-- This depends heavily on the node

            # --- Output Configuration for Save Nodes ---
            # Configure the primary save node
            if SAVE_BASE_IMAGE_NODE_TITLE:
                save_node_config = {
                    "filename_prefix": filename_prefix_for_node,
                    "directory": output_dir_for_node.as_posix(), # Pass relative path
                    # Add other common save node params if needed (check your node)
                    # "output_path": output_dir_for_node.as_posix(), # Some nodes use this
                    # "save_image": True, # Ensure saving is enabled
                }
                inputs_to_set[SAVE_BASE_IMAGE_NODE_TITLE] = save_node_config
                log_step(f" Setting output for node '{SAVE_BASE_IMAGE_NODE_TITLE}': prefix='{filename_prefix_for_node}', dir='{output_dir_for_node.as_posix()}'", level="debug")
            else:
                log_step("Warning: SAVE_BASE_IMAGE_NODE_TITLE not configured. Output may not be saved as expected.", level="warning")

            # Configure the secondary/swapped save node (if face swap occurred and node exists)
            # Usually, you configure BOTH save nodes regardless, and the workflow routes the correct image.
            if SAVE_SWAPPED_IMAGE_NODE_TITLE and SAVE_SWAPPED_IMAGE_NODE_TITLE != SAVE_BASE_IMAGE_NODE_TITLE:
                 # Often uses the same prefix/dir, or maybe a slightly different prefix
                 swapped_prefix = f"swapped_{filename_prefix_for_node}" # Example prefix diff
                 save_node_config_swapped = {
                     "filename_prefix": filename_prefix_for_node, # Or swapped_prefix
                     "directory": output_dir_for_node.as_posix(),
                 }
                 inputs_to_set[SAVE_SWAPPED_IMAGE_NODE_TITLE] = save_node_config_swapped
                 log_step(f" Setting output for node '{SAVE_SWAPPED_IMAGE_NODE_TITLE}': prefix='{filename_prefix_for_node}', dir='{output_dir_for_node.as_posix()}'", level="debug")


            # --- Modify and Run Workflow ---
            log_step(f" Modifying workflow inputs for Scene {scene_number}...");
            if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                raise ValueError("Failed to modify workflow inputs via comfyui_interactions.")

            log_step(f" Queueing workflow for Scene {scene_number} (Seed: {seed}). Prompt: '{scene_prompt[:80]}...'");
            # Adjust timeout as needed, scene generation can be longer than character portraits
            comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=900)

            if not comfyui_result_history:
                # Check comfyui_interactions for specific error details if possible
                raise RuntimeError("ComfyUI workflow failed or timed out. Check ComfyUI console/logs.")
            else:
                # Optional: You could try and parse comfyui_result_history to find the exact output filenames if needed
                log_step(f" ComfyUI workflow COMPLETED for Scene {scene_number}.", level="success")
                generated_scene_count += 1

        except Exception as e:
            log_step(f"ERROR processing Scene {scene_number}: {e}", level="error")
            log.error(traceback.format_exc()) # Log full traceback for debugging
            failed_scene_count += 1

    # --- End Scene Loop ---

    # --- Final Summary ---
    log_step("\n--- Finished Scene Image Generation Phase ---", important=True)
    if not at_least_one_comfy_run_started:
        log_step("No ComfyUI runs were attempted (e.g., missing prompts file or invalid entries).", level="warning")
        return False # Indicate nothing significant happened

    total_scenes_processed = generated_scene_count + failed_scene_count
    total_prompts_available = len(scene_prompts_data)

    log_step(f"Attempted generation for {total_scenes_processed} out of {total_prompts_available} scenes found in prompts file.")
    log_step(f"Successfully generated: {generated_scene_count} scenes.")
    log_step(f"Failed to generate:    {failed_scene_count} scenes.")
    log_step(f"Outputs saved within subfolders of (absolute path): {comfy_api_run_folder_abs}")
    log_step(f"-> Check relative path in ComfyUI output: {comfy_api_run_subfolder_rel / GENERATED_SCENES_SUBFOLDER}")

    if failed_scene_count > 0 and generated_scene_count > 0:
        log_step("Completed with PARTIAL success (some scenes failed).", level="warning")
        return True # Indicate partial success
    elif generated_scene_count > 0 and failed_scene_count == 0:
        log_step("Completed successfully: All attempted scenes generated.", level="success")
        return True
    elif failed_scene_count > 0 and generated_scene_count == 0:
         log_step("Completed with ERRORS: All attempted scenes failed.", level="error")
         return False # Indicate failure
    elif total_scenes_processed == 0 and total_prompts_available > 0:
         log_step("Warning: No scenes were processed, though prompts were available. Check logs for earlier critical errors.", level="warning")
         return False
    else: # Should only happen if total_prompts_available was 0
         log_step("No scene prompts were found to process.", level="info")
         return True # Technically successful, just nothing to do.


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate scene images for a mythic_movie project using ComfyUI.")
    parser.add_argument(
        "-p", "--project_path",
        help="Path to the specific project folder (e.g., 'mythic_movie_project/mythic_movie_Icarus_20231027_100000'). Omit to use the latest.")
    args = parser.parse_args()

    log_step("===== Generate Scene Images Script Started =====", important=True)

    # --- Load Environment Variables (Optional) ---
    try:
        dotenv_path = getattr(config, 'DOTENV_PATH', None)
        if dotenv_path and Path(dotenv_path).is_file():
            load_dotenv(dotenv_path=dotenv_path)
            log_step(f"Loaded environment variables from {dotenv_path}", level="debug")
        elif dotenv_path:
             log_step(f".env file specified but not found at {dotenv_path}", level="debug")
    except Exception as e:
        log_step(f"Note: Error loading .env file: {e}", level="debug")

    # --- Validate Essential ComfyUI Configuration ---
    essential_configs = {
        "COMFYUI_INPUT_DIR": COMFYUI_INPUT_DIR,
        "COMFYUI_OUTPUT_DIR": COMFYUI_OUTPUT_DIR,
        "IMAGE_WORKFLOW_TEMPLATE": IMAGE_WORKFLOW_TEMPLATE
    }
    missing_essentials = False
    for name, value in essential_configs.items():
        if not value:
            log_step(f"CRITICAL Error: '{name}' is not configured in config_v2.py or defaults.", level="error", important=True)
            missing_essentials = True
        elif name.endswith("_DIR") and not Path(value).is_dir():
             log_step(f"CRITICAL Error: Configured {name} path '{value}' is not a valid directory.", level="error", important=True)
             missing_essentials = True
        elif name.endswith("_TEMPLATE") and not Path(value).is_file():
             log_step(f"CRITICAL Error: Configured {name} file '{value}' was not found.", level="error", important=True)
             missing_essentials = True

    if not SAVE_BASE_IMAGE_NODE_TITLE:
         log_step("CRITICAL Error: SAVE_BASE_IMAGE_NODE_TITLE must be configured.", level="error", important=True)
         missing_essentials = True


    if missing_essentials:
        exit(1)

    # --- Determine Project Path ---
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        # Basic validation if path is provided
        if not project_to_process.is_dir():
             log_step(f"Error: Provided project path does not exist or is not a directory: {project_to_process}", level="error", important=True)
             exit(1)
        # Check if it seems to be inside the base projects dir (optional but good sanity check)
        try:
            if not project_to_process.parent.samefile(PROJECTS_BASE_DIR):
                log_step(f"Warning: Provided project path '{project_to_process}' is not directly inside the configured PROJECTS_BASE_DIR '{PROJECTS_BASE_DIR}'.", level="warning")
        except FileNotFoundError:
             log_step(f"Warning: Cannot compare project path parent with PROJECTS_BASE_DIR ('{PROJECTS_BASE_DIR}' might be incorrect or inaccessible).", level="warning")

    if not project_to_process:
        log_step(f"No project path provided. Finding latest project in {PROJECTS_BASE_DIR}...")
        # Ensure the base directory exists before searching
        if not PROJECTS_BASE_DIR.is_dir():
             log_step(f"Error: PROJECTS_BASE_DIR '{PROJECTS_BASE_DIR}' does not exist or is not a directory.", level="error", important=True); exit(1)
        project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR, project_prefix="mythic_movie") # Use prefix from Script 1 if consistent
        if not project_to_process:
            log_step(f"Error: No suitable project directory found in {PROJECTS_BASE_DIR}.", level="error", important=True); exit(1)

    log_step(f"Using project: {project_to_process.name}")
    log_step(f"Full path: {project_to_process.resolve()}")


    # --- Validate Helper Modules Availability ---
    # Check if essential functions from comfyui_interactions are present
    required_comfy_funcs = ['load_workflow_template', 'modify_workflow_inputs', 'run_comfyui_workflow']
    missing_funcs = [f for f in required_comfy_funcs if not hasattr(comfyui_interactions, f)]
    if missing_funcs:
        log_step(f"CRITICAL Error: Essential function(s) missing from comfyui_interactions module: {', '.join(missing_funcs)}", level="error", important=True)
        exit(1)

    # --- Run Main Logic ---
    success = False
    try:
        success = run_image_generation(project_to_process)
    except Exception as e:
        log_step(f"An unexpected critical error occurred in the main execution: {e}", level="error", important=True)
        log.error(traceback.format_exc())
        success = False # Ensure failure state

    # --- Exit ---
    if success:
        log_step("Script finished successfully or partially.", level="success", important=True)
        exit(0)
    else:
        log_step("Script finished with critical errors or no generation completed.", level="error", important=True)
        exit(1)