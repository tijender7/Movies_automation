# generate_images_comfyui.py
# Adapted from generate_images_v2.py to work with prompts from generate_prompts_chatgpt.py

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

# Import project modules (Ensure these point to your V2 versions or relevant files)
try:
    # Assuming config_v2 contains COMFYUI settings, paths etc.
    import config_v2 as config
    # Assuming comfyui_interactions handles API calls
    import comfyui_interactions as comfyui_interactions
    # Helpers for finding project/theme are still useful
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir # Get helpers from the new script if preferred or keep old import if they exist
except ImportError as e:
    # Use print as logging might not be set up
    print(f"ERROR: Failed to import necessary modules (config_v2, comfyui_interactions, helpers): {e}")
    exit(1)

# --- Constants ---
# Use constants defined in config_v2 if available, otherwise define here
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "comfyui_image_gen.log")
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects"))
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME', "characters")
SOURCE_ACTORS_FOLDER_NAME = getattr(config, 'SOURCE_ACTORS_FOLDER_NAME', "source_actors")
# --- FIXED: Metadata and Prompt Filenames ---
INFO_JSON_FILENAME = "chatgpt_movie_info.json"
PROMPT_JSON_FILENAME = "image_prompts.json"
# --- END FIX ---
COMFYUI_INPUT_DIR = getattr(config, 'COMFYUI_INPUT_DIR', None) # Must be configured
COMFYUI_OUTPUT_DIR = getattr(config, 'COMFYUI_OUTPUT_DIR', None) # Must be configured
API_OUTPUTS_SUBDIR = getattr(config, 'API_OUTPUTS_SUBDIR', "API_OUTPUTS") # Subdir within COMFYUI_OUTPUT_DIR
IMAGE_WORKFLOW_TEMPLATE = getattr(config, 'IMAGE_WORKFLOW_TEMPLATE', None) # Must be configured
print(f"[DEBUG] IMAGE_WORKFLOW_TEMPLATE resolved to: {IMAGE_WORKFLOW_TEMPLATE}")
# Subfolders within the specific Run/Character folder in API_OUTPUTS
COMFYUI_BASE_IMAGES_SUBFOLDER = getattr(config, 'COMFYUI_BASE_IMAGES_SUBFOLDER', 'base_images')
COMFYUI_SWAPPED_IMAGES_SUBFOLDER = getattr(config, 'COMFYUI_SWAPPED_IMAGES_SUBFOLDER', 'swapped_images')
# Filename prefixes
BASE_IMAGE_PREFIX = getattr(config, 'BASE_IMAGE_PREFIX', 'base_')
SWAPPED_IMAGE_PREFIX = getattr(config, 'SWAPPED_IMAGE_PREFIX', 'swapped_')
# Workflow Node Titles (MUST match your workflow template JSON)
PROMPT_NODE_TITLE = getattr(config, 'PROMPT_NODE_TITLE', 'Positive Prompt')
SEED_NODE_TITLE = getattr(config, 'SEED_NODE_TITLE', 'Seed (INT)')
FACE_NODE_TITLE = getattr(config, 'FACE_NODE_TITLE', 'Load Image (Face)')
IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE = getattr(config, 'IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE', 'Save Image Base') # Node that saves pre-swap img
IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE = getattr(config, 'IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE', 'Save Image Swapped') # Node that saves final img


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
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('tensorflow').setLevel(logging.ERROR) # Suppress TF info/warnings further
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
except Exception as e:
    print(f"ERROR setting up logging to {log_file_path}: {e}")
    # Fallback basic logging
    logging.basicConfig( level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}"
    if important: final_msg = f"--- {final_msg} ---"
    if level == "error": log.error(final_msg)
    elif level == "warning": log.warning(final_msg)
    elif level == "debug": log.debug(final_msg)
    else: log.info(final_msg)


# --- Helper: Copy Source Face to ComfyUI Input ---
def copy_to_comfyui_input(source_file_path: Path):
    """Copies a file to the configured ComfyUI input directory."""
    if not source_file_path.is_file():
        log_step(f"Source file to copy not found: {source_file_path}", level="error"); return False
    if not COMFYUI_INPUT_DIR or not Path(COMFYUI_INPUT_DIR).is_dir():
        log_step(f"ComfyUI Input Dir not configured/found: {COMFYUI_INPUT_DIR}", level="error"); return False
    try:
        destination_path = Path(COMFYUI_INPUT_DIR) / source_file_path.name
        shutil.copy2(source_file_path, destination_path)
        log_step(f" Copied '{source_file_path.name}' to ComfyUI input", level="debug")
        return True
    except Exception as e:
        log_step(f"Error copying '{source_file_path.name}' to ComfyUI input: {e}", level="error"); return False

# --- Helper: Sanitize Name ---
def sanitize_name(name):
    """Sanitizes a name for file/folder usage."""
    if not isinstance(name, str): name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name)
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized); sanitized = re.sub(r'[-]+', '-', sanitized)
    sanitized = sanitized.strip('_-'); max_len = 60
    if len(sanitized) > max_len: sanitized = sanitized[:max_len].strip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Helper: Find Source Images ---
def find_source_images(actor_name, source_dir):
    sanitized_name = sanitize_name(actor_name)
    pattern_jpg = str(source_dir / f"{sanitized_name}_*.jpg")
    pattern_png = str(source_dir / f"{sanitized_name}_*.png")
    files = glob.glob(pattern_jpg) + glob.glob(pattern_png)
    return files

# --- Main Logic ---
def run_image_generation(project_path: Path, theme: str):
    """
    Generates base and swapped images using ComfyUI for actors with source images.
    Reads prompts from image_prompts.json within the character's folder.
    Finds the selected source face (by Actor Name) in the project's source_actors folder.
    Writes ALL image outputs to the configured ComfyUI API Output Folder structure.
    """
    log_step(f"--- Starting Image Generation for Project: {project_path.name} ---", important=True)
    log_step(f"Using Theme: {theme}")

    # --- FIXED: Load correct info JSON ---
    info_json_path = project_path / INFO_JSON_FILENAME
    if not info_json_path.is_file(): log_step(f"Info JSON file not found: {info_json_path}", level="error"); return False
    try:
        with open(info_json_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
        if "movie_characters" not in movie_info: raise ValueError("Missing 'movie_characters' key")
    except Exception as e: log_step(f"Error loading {INFO_JSON_FILENAME}: {e}", level="error"); return False

    # Define project paths
    project_characters_base_path = project_path / CHARACTERS_FOLDER_NAME
    project_source_actors_path = project_path / SOURCE_ACTORS_FOLDER_NAME

    medium_images_succeeded = []
    full_images_succeeded = []
    all_actors_processed_with_source = [] # Actors for whom source image was found
    at_least_one_comfy_run_started = False

    # --- Define Unique Run ID for ComfyUI API Output Subfolder ---
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    comfy_api_run_subfolder_rel = Path(f"{project_path.name}_{run_timestamp}") # Relative path for API output structure
    # Absolute path to the base API output dir (e.g., H:/ComfyUI/output/API_OUTPUTS)
    comfy_api_base_folder_abs = Path(COMFYUI_OUTPUT_DIR) / API_OUTPUTS_SUBDIR
    # Absolute path for this specific run's output
    comfy_api_run_folder_abs = comfy_api_base_folder_abs / comfy_api_run_subfolder_rel
    log_step(f"ComfyUI output base directory for this run: {comfy_api_run_folder_abs}")
    try:
        comfy_api_base_folder_abs.mkdir(parents=True, exist_ok=True)
    except OSError as e: log_step(f"Could not create API base output directory: {e}", level="error"); return False

    # Load the workflow template ONCE
    workflow_template = comfyui_interactions.load_workflow_template(IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template: log_step("Failed to load workflow template. Cannot proceed.", level="error"); return False

    # Loop through characters defined in project metadata
    for char_info in movie_info.get('movie_characters', []):
        character_name = char_info.get('character_name')
        actor_name = char_info.get('actor_name')
        if not character_name or not actor_name: continue # Skip if essential info missing

        # --- Step 1: Find Approved Source Image(s) ---
        source_images = find_source_images(actor_name, project_source_actors_path)
        log_step(f"\nProcessing Actor: {actor_name} (Character: {character_name})")
        log_step(f"  Looking for source images '{actor_name}_*.jpg|png' in {project_source_actors_path}")
        if not source_images:
            log_step(f" Source image NOT FOUND for Actor '{actor_name}'. Skipping generation.", level='warning')
            continue # Skip this actor if no approved source image exists
        for source_image_path in source_images:
            source_image_found = Path(source_image_path)
            log_step(f"  Found source image: {source_image_found.name}", level="success")

            # --- Step 2: Load Prompts for this Actor ---
            # --- FIXED: Use actor name for folder path ---
            project_character_dir = project_characters_base_path / sanitize_name(actor_name)
            # --- FIXED: Load JSON prompt file ---
            prompt_file_path = project_character_dir / PROMPT_JSON_FILENAME

            if not prompt_file_path.is_file():
                log_step(f" Prompt JSON file not found: {prompt_file_path}. Skipping generation for {actor_name}.", level="warning")
                continue

            prompt_json_data = None
            try:
                with open(prompt_file_path, 'r', encoding='utf-8') as f:
                    prompt_json_data = json.load(f)
                log_step(f" Loaded prompts from {PROMPT_JSON_FILENAME} for {actor_name}")
            except (IOError, json.JSONDecodeError) as e:
                log_step(f" Error loading or parsing {prompt_file_path}: {e}. Skipping generation for {actor_name}.", level="error")
                continue

            # --- Step 3: Generate Medium and Full Body Images ---
            for shot_type in ["medium", "full"]:
                prompt_key = f"{shot_type}_prompt" # e.g., "medium_prompt"

                # --- FIXED: Extract prompt from loaded JSON ---
                flux_prompt = prompt_json_data.get(prompt_key)
                if not flux_prompt:
                    log_step(f" '{prompt_key}' not found in {PROMPT_JSON_FILENAME} for {actor_name}. Skipping {shot_type} shot.", level="warning")
                    continue
                # --- END FIX ---

                log_step(f"--- Generating {shot_type.capitalize()} Body Image for {character_name} ---")
                try:
                    at_least_one_comfy_run_started = True
                    log_step(f" Using {shot_type} prompt: {flux_prompt[:100]}...", level="debug")

                    # Copy source face image (required for each generation run)
                    if not copy_to_comfyui_input(source_image_found):
                        raise IOError(f"Failed copy source image {source_image_found.name}")

                    # --- Define ComfyUI API Output Paths ---
                    # Relative path within API_OUTPUTS for this character/actor
                    comfy_char_actor_rel_path = Path(f"{sanitize_name(character_name)}__{sanitize_name(actor_name)}") # Include char name for clarity
                    # Relative paths for base/swapped within character folder
                    comfy_base_img_output_rel_path = comfy_char_actor_rel_path / COMFYUI_BASE_IMAGES_SUBFOLDER
                    comfy_swapped_img_output_rel_path = comfy_char_actor_rel_path / COMFYUI_SWAPPED_IMAGES_SUBFOLDER
                    # Path relative to API_OUTPUTS base for the prefix node
                    prefix_node_base_dir = comfy_api_run_subfolder_rel / comfy_base_img_output_rel_path
                    prefix_node_swapped_dir = comfy_api_run_subfolder_rel / comfy_swapped_img_output_rel_path
                    # Filename prefixes
                    base_filename_prefix = f"{BASE_IMAGE_PREFIX}{shot_type}_"
                    swapped_filename_prefix = f"{SWAPPED_IMAGE_PREFIX}{shot_type}_"
                    seed = random.randint(0, 2**32 - 1)
                    # ---

                    # --- Prepare Workflow ---
                    current_workflow = copy.deepcopy(workflow_template)
                    if not current_workflow: raise ValueError("Workflow template copy failed.")
                    inputs_to_set = {
                        PROMPT_NODE_TITLE: {"text": flux_prompt},
                        SEED_NODE_TITLE: {"noise_seed": seed},
                        FACE_NODE_TITLE: {"image": source_image_found.name}, # Filename only
                        IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE: {
                            "custom_text": base_filename_prefix,
                            "custom_directory": prefix_node_base_dir.as_posix(), # Relative to API_OUTPUTS
                            "date_directory": "false" },
                        IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE: {
                            "custom_text": swapped_filename_prefix,
                            "custom_directory": prefix_node_swapped_dir.as_posix(), # Relative to API_OUTPUTS
                            "date_directory": "false" }
                    }
                    log_step(f" Modifying workflow inputs for {shot_type}...")
                    if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                        raise ValueError("Failed modify workflow inputs")
                    # ---

                    # --- Execute Workflow ---
                    log_step(f" Queueing ComfyUI {shot_type} workflow for {character_name} (Seed: {seed})...")
                    comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=600)
                    if not comfyui_result_history: raise TimeoutError("ComfyUI workflow failed/timed out.")
                    else:
                        log_step(f" ComfyUI {shot_type} workflow COMPLETED for {character_name}.", level="success")
                        if shot_type == "medium": medium_images_succeeded.append(actor_name)
                        else: full_images_succeeded.append(actor_name)
                    # ---

                except (IOError, ValueError, TimeoutError, TypeError, KeyError) as e: # Added KeyError
                     log_step(f"Error during {shot_type} generation for {character_name}: {e}", level="error")
                except Exception as e_gen:
                     log_step(f"Unexpected error during {shot_type} generation for {character_name}: {e_gen}", level="error", exc_info=True)
            # --- End Sub-Loop ---
        all_actors_processed_with_source.append(actor_name) # Add to list of actors we will attempt
    # --- End Character Loop ---

    # --- Final Summary ---
    log_step("\n--- Finished Image Generation Phase ---", important=True)
    if not at_least_one_comfy_run_started:
        log_step("No ComfyUI runs attempted (check source images/prompts).", level="warning"); return False

    unique_medium_success = set(medium_images_succeeded)
    unique_full_success = set(full_images_succeeded)
    processed_actors_count = len(all_actors_processed_with_source) # Count only those we attempted
    log_step(f"Attempted generation for {processed_actors_count} actors.")
    log_step(f"Successfully generated medium images for {len(unique_medium_success)} actors.")
    log_step(f"Successfully generated full body images for {len(unique_full_success)} actors.")
    log_step(f"Outputs saved within ComfyUI API Run Folder: {comfy_api_run_folder_abs}")

    failed_actors = []
    for actor in all_actors_processed_with_source: # Check only actors we attempted
        if actor not in unique_medium_success and actor not in unique_full_success:
            failed_actors.append(actor)

    if failed_actors:
         log_step(f"Failed to generate *any* image type for: {', '.join(failed_actors)}", level="warning")
         return True # Still partial success if others worked
    elif processed_actors_count == 0:
         log_step("No actors processed (no source images found?).", level="warning"); return False
    else:
        all_succeeded_both = all( (actor in unique_medium_success and actor in unique_full_success) for actor in all_actors_processed_with_source )
        if all_succeeded_both: log_step("Image generation completed: Both image types generated for all processed actors.", level="success")
        else: log_step("Image generation completed, but some actors might be missing one image type.", level="warning")
        return True

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate character images using ComfyUI."); parser.add_argument("-p", "--project_path", help="Path to project folder. Omit for latest.")
    args = parser.parse_args(); log_step("===== Generate Images Script Started =====", important=True)
    try: load_dotenv(dotenv_path=config.DOTENV_PATH) # Use config_v2 path if defined there
    except Exception as e: log_step(f"Note: .env load error: {e}", level="debug")

    # Validate essential ComfyUI paths from config
    if not COMFYUI_INPUT_DIR or not COMFYUI_OUTPUT_DIR or not IMAGE_WORKFLOW_TEMPLATE:
         log_step("Critical Error: COMFYUI_INPUT_DIR, COMFYUI_OUTPUT_DIR, or IMAGE_WORKFLOW_TEMPLATE not configured in config_v2.py.", level="error", important=True); exit(1)
    if not Path(COMFYUI_INPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_INPUT_DIR is not a valid directory: {COMFYUI_INPUT_DIR}", level="error"); exit(1)
    if not Path(COMFYUI_OUTPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_OUTPUT_DIR is not a valid directory: {COMFYUI_OUTPUT_DIR}", level="error"); exit(1)
    if not Path(IMAGE_WORKFLOW_TEMPLATE).is_file() : log_step(f"Error: IMAGE_WORKFLOW_TEMPLATE not found: {IMAGE_WORKFLOW_TEMPLATE}", level="error"); exit(1)


    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not project_to_process.parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found in {PROJECTS_BASE_DIR}", level="error", important=True); exit(1)
    log_step(f"Using project: {project_to_process}")

    project_theme = extract_theme_from_project_path(project_to_process)
    if not project_theme or project_theme == "Default Futuristic Theme": log_step(f"Cannot determine theme from: {project_to_process}", level="error"); exit(1)

    # Check comfyui_interactions functions exist
    if not hasattr(comfyui_interactions, 'load_workflow_template') or \
       not hasattr(comfyui_interactions, 'modify_workflow_inputs') or \
       not hasattr(comfyui_interactions, 'run_comfyui_workflow'):
        log_step("Critical Error: Essential functions missing from comfyui_interactions.py!", level="error", important=True); exit(1)

    success = run_image_generation(project_to_process, project_theme)

    if success: log_step("Script finished successfully or partially.", level="success", important=True); exit(0)
    else: log_step("Script finished with critical errors or no generation attempted.", level="error", important=True); exit(1)