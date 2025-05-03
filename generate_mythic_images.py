# generate_mythic_images.py
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
import traceback
import time # Import time for potential delays

# --- Import Configuration and Interaction Modules ---
try:
    import config_mythic as config # Import the new config file
    # Assuming comfyui_interactions.py is in the same directory or Python path
    import comfyui_interactions
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules (config_mythic, comfyui_interactions): {e}")
    print("Ensure config_mythic.py and comfyui_interactions.py are accessible.")
    exit(1)

# --- Logging Setup ---
# CORRECTED LINE:
log_file_path = config.LOG_DIR / f"{config.LOG_FILE_BASENAME_GEN}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
try:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, mode='a', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Reduce noise from libraries if needed
    logging.getLogger('urllib3').setLevel(logging.WARNING)
except Exception as e:
    print(f"ERROR setting up logging: {e}")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# --- Helper Function: Logging Wrapper ---
def log_step(message, level="info", important=False):
    prefix_map = {"info": "[*]", "warning": "[!]", "error": "[X]", "success": "[+]", "debug": "[D]"}
    final_msg = f"{prefix_map.get(level, '[?]')} {message}"
    if important: final_msg = f"\n--- {final_msg} ---"
    log.log(getattr(logging, level.upper(), logging.INFO), final_msg)

# --- Helper Function: Find Latest Project Directory ---
# (Copied from previous context - ensure it matches your needs)
def find_latest_project_dir(base_dir):
    """Finds the most recently created directory in base_dir matching the project pattern."""
    log_step(f"Searching for latest project directory in: {base_dir}", level="debug")
    latest_dir = None
    latest_time = 0
    try:
        for item in base_dir.iterdir():
            # Adjust pattern if needed, e.g., 'mythic_movie_*'
            if item.is_dir() and item.name.startswith("mythic_movie_"):
                try:
                    # Use modification time as a proxy for creation time if creation time is unavailable
                    mod_time = item.stat().st_mtime
                    if mod_time > latest_time:
                        latest_time = mod_time
                        latest_dir = item
                except OSError as e:
                    log_step(f"Could not stat directory {item.name}: {e}", level="warning")
    except FileNotFoundError:
        log_step(f"Base project directory not found: {base_dir}", level="error")
        return None
    except Exception as e:
        log_step(f"Error searching for latest project: {e}", level="error")
        return None

    if latest_dir:
        log_step(f"Found latest project directory: {latest_dir.name}", level="success")
    else:
        log_step(f"No project directories found matching pattern in {base_dir}", level="warning")
    return latest_dir


# --- Main Logic Function ---
def run_mythic_image_generation(project_path: Path):
    log_step(f"--- Starting Image Generation for Project: {project_path.name} ---", important=True)

    # 1. Load Workflow Template
    log_step(f"Loading workflow template from: {config.FLUX_WORKFLOW_TEMPLATE_PATH}")
    workflow_template = comfyui_interactions.load_workflow_template(config.FLUX_WORKFLOW_TEMPLATE_PATH)
    if not workflow_template:
        log_step("Failed to load workflow template. Aborting.", level="error", important=True)
        return False

    # 2. Load Prompts JSON
    prompt_json_path = project_path / config.PROMPT_JSON_FILENAME
    if not prompt_json_path.is_file():
        log_step(f"Prompt JSON file not found: {prompt_json_path}", level="error", important=True)
        return False

    try:
        with open(prompt_json_path, 'r', encoding='utf-8') as f:
            prompts_data = json.load(f)
        if not isinstance(prompts_data, list):
             raise ValueError("Prompt JSON should contain a list of scene objects.")
        log_step(f"Successfully loaded {len(prompts_data)} prompts from {prompt_json_path.name}")
    except Exception as e:
        log_step(f"Error loading or parsing {prompt_json_path.name}: {e}", level="error", important=True)
        return False

    # 3. Prepare for ComfyUI Interaction
    total_scenes = len(prompts_data)
    success_count = 0
    failed_scenes = []

    # 4. Iterate Through Scenes and Generate Images
    for index, scene_data in enumerate(prompts_data):
        scene_number = scene_data.get("scene_number", f"index_{index}") # Use index if number missing
        prompt_text = scene_data.get("prompt_text")

        log_step(f"\n--- Processing Scene {scene_number}/{total_scenes} ---", important=True)

        if not prompt_text:
            log_step(f"Skipping Scene {scene_number}: Missing 'prompt_text'.", level="warning")
            failed_scenes.append(str(scene_number))
            continue

        try:
            # Generate a random seed for this scene
            seed = random.randint(0, 2**32 - 1)
            log_step(f"Using Seed: {seed}")
            log_step(f"Using Prompt: {prompt_text[:150]}...") # Log snippet

            # Create a deep copy of the workflow for modification
            current_workflow = copy.deepcopy(workflow_template)
            if not current_workflow:
                raise ValueError("Failed to deep copy workflow template.")

            # --- Calculate Relative Output Path for ComfyUI ---
            # This path is relative to COMFYUI_OUTPUT_DIR and tells the Save node where to put files
            # Example: API_OUTPUTS_MYTHIC/mythic_movie_shiva_2023.../generated_images
            relative_output_dir = Path(config.API_OUTPUTS_SUBDIR) / project_path.name / config.OUTPUT_IMAGES_SUBFOLDER
            # Convert to posix style for JSON compatibility, even on Windows
            relative_output_dir_str = relative_output_dir.as_posix()
            log_step(f"Calculated ComfyUI relative output directory: {relative_output_dir_str}", level="debug")

            # Define the filename prefix for this scene
            filename_prefix = f"scene_{scene_number}_"

            # --- Prepare modifications for comfyui_interactions ---
            title_value_map = {
                config.FLUX_PROMPT_NODE_TITLE: {
                    "text": prompt_text # Modify the 'text' input of the "Text Multiline" node
                },
                config.FLUX_SEED_NODE_TITLE: {
                    "noise_seed": seed # Modify the 'noise_seed' input of the "RandomNoise" node
                },
                config.FLUX_PREFIX_NODE_TITLE: {
                    "custom_directory": relative_output_dir_str, # Set the custom directory
                    "custom_text": filename_prefix,           # Set the filename prefix
                    "date_directory": "false"                 # Disable date-based subfolders
                }
            }

            # Modify the workflow
            log_step("Modifying workflow inputs...")
            if not comfyui_interactions.modify_workflow_inputs(current_workflow, title_value_map):
                raise ValueError("Failed to modify workflow inputs using comfyui_interactions.")

            # Run the workflow via ComfyUI API
            log_step("Queueing workflow in ComfyUI...")
            comfyui_result_history = comfyui_interactions.run_comfyui_workflow(
                current_workflow,
                timeout=config.COMFYUI_TIMEOUT
            )

            if comfyui_result_history:
                # Basic success check: History object received
                log_step(f"ComfyUI workflow COMPLETED for Scene {scene_number}.", level="success")
                success_count += 1
                # Optional: Add more detailed checks on history if needed (e.g., check output node)
            else:
                # run_comfyui_workflow logs errors internally
                log_step(f"ComfyUI workflow FAILED or timed out for Scene {scene_number}.", level="error")
                failed_scenes.append(str(scene_number))

            # Optional: Add a small delay between API calls if needed
            # time.sleep(1)

        except Exception as e:
            log_step(f"Unexpected error processing Scene {scene_number}: {e}", level="error")
            log.debug(traceback.format_exc()) # Log full traceback for debugging
            failed_scenes.append(str(scene_number))

    # 5. Final Summary
    log_step("\n--- Image Generation Phase Finished ---", important=True)
    log_step(f"Total scenes to process: {total_scenes}")
    log_step(f"Successfully generated images for: {success_count} scenes")
    log_step(f"Failed to generate images for: {len(failed_scenes)} scenes")
    if failed_scenes:
        log_step(f"Failed scene numbers: {', '.join(failed_scenes)}", level="warning")

    # Determine overall success
    if success_count == total_scenes:
        log_step("All scenes processed successfully!", level="success")
        return True
    elif success_count > 0:
        log_step("Partial success: Some scenes failed.", level="warning")
        return True # Still return True for partial success
    else:
        log_step("No scenes were processed successfully.", level="error")
        return False


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate images for a Mythic Movie project using ComfyUI.")
    parser.add_argument(
        "-p", "--project_path",
        help="Path to the specific project folder (e.g., 'mythic_movie_project/mythic_movie_shiva_...') . Omit to use the latest project found."
    )
    args = parser.parse_args()
    log_step("===== Generate Mythic Images Script Started =====", important=True)

    # Load .env file if it exists
    try:
        if config.DOTENV_PATH.is_file():
            load_dotenv(dotenv_path=config.DOTENV_PATH)
            log_step(f"Loaded environment variables from {config.DOTENV_PATH}")
        else:
             log_step(f".env file not found at {config.DOTENV_PATH}, continuing without it.", level="debug")
    except Exception as e:
        log_step(f"Warning: Error loading .env file: {e}", level="warning")

    # Validate essential configurations from config_mythic.py
    essential_configs = {
        "COMFYUI_URL": config.COMFYUI_URL,
        "COMFYUI_OUTPUT_DIR": config.COMFYUI_OUTPUT_DIR,
        "FLUX_WORKFLOW_TEMPLATE_PATH": config.FLUX_WORKFLOW_TEMPLATE_PATH,
        "PROJECTS_BASE_DIR": config.PROJECTS_BASE_DIR
    }
    valid_config = True
    for name, value in essential_configs.items():
        if not value:
            log_step(f"Critical Error: Configuration variable '{name}' is not set in config_mythic.py.", level="error", important=True)
            valid_config = False
        if "DIR" in name or "PATH" in name:
             # Basic check if path-like configs point to something existing
             path_value = Path(value)
             if ("DIR" in name and not path_value.is_dir()) or ("PATH" in name and not path_value.is_file()):
                  # Allow COMFYUI_OUTPUT_DIR to not exist initially, ComfyUI might create it.
                  # Check PROJECTS_BASE_DIR and FLUX_WORKFLOW_TEMPLATE_PATH existence.
                  if name == "PROJECTS_BASE_DIR" and not path_value.is_dir():
                      log_step(f"Critical Error: {name} directory not found: '{value}'", level="error", important=True)
                      valid_config = False
                  elif name == "FLUX_WORKFLOW_TEMPLATE_PATH" and not path_value.is_file():
                      log_step(f"Critical Error: {name} file not found: '{value}'", level="error", important=True)
                      valid_config = False

    if not valid_config:
        exit(1)

    # Determine project path
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path).resolve()
        if not project_to_process.is_dir():
             log_step(f"Error: Provided project path not found or not a directory: {project_to_process}", level="error", important=True)
             exit(1)
        # Optional: Check if it's inside the configured PROJECTS_BASE_DIR
        try:
            project_to_process.relative_to(config.PROJECTS_BASE_DIR)
        except ValueError:
             log_step(f"Warning: Provided project path '{project_to_process}' is not inside the configured PROJECTS_BASE_DIR '{config.PROJECTS_BASE_DIR}'.", level="warning")
    else:
        log_step(f"No specific project path provided. Finding latest project in {config.PROJECTS_BASE_DIR}...")
        project_to_process = find_latest_project_dir(config.PROJECTS_BASE_DIR)
        if not project_to_process:
            log_step(f"No project directory found in {config.PROJECTS_BASE_DIR}.", level="error", important=True)
            exit(1)

    log_step(f"Using project: {project_to_process}")

    # Ensure comfyui_interactions has the required functions
    required_funcs = ['load_workflow_template', 'modify_workflow_inputs', 'run_comfyui_workflow']
    if not all(hasattr(comfyui_interactions, func) for func in required_funcs):
        log_step("Critical Error: Essential functions missing from comfyui_interactions.py!", level="error", important=True)
        log_step(f"Required: {', '.join(required_funcs)}", level="error")
        exit(1)

    # Run the main generation logic
    overall_success = run_mythic_image_generation(project_to_process)

    # Exit with appropriate status code
    if overall_success:
        log_step("===== Generate Mythic Images Script Finished Successfully =====", level="success", important=True)
        exit(0)
    else:
        log_step("===== Generate Mythic Images Script Finished with Errors =====", level="error", important=True)
        exit(1)