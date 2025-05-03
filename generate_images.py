# generate_images.py
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

# Import project modules
try:
    import config
    import comfyui_interactions
    # Import only the needed function from generate_prompts
    from generate_prompts import extract_theme_from_project_path, find_latest_project_dir # Import both helpers
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules: {e}")
    print("Make sure config.py, comfyui_interactions.py, and generate_prompts.py exist and are correct.")
    exit(1)

# --- Logging Setup ---
log_file_path = Path(config.LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True) # Ensure log directory exists
    log_file_path.touch(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, mode='a'),
            logging.StreamHandler()
        ]
    )
except Exception as e:
    print(f"ERROR setting up logging to {log_file_path}: {e}")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[logging.StreamHandler()]
    )
log = logging.getLogger(__name__)

# --- Helper: Copy Source Face to ComfyUI Input ---
def copy_to_comfyui_input(source_file_path: Path):
    """Copies a file to the configured ComfyUI input directory."""
    if not config.COMFYUI_INPUT_DIR or not config.COMFYUI_INPUT_DIR.is_dir():
        log.error(f"ComfyUI Input Dir not configured/found: {config.COMFYUI_INPUT_DIR}")
        return False
    try:
        destination_path = config.COMFYUI_INPUT_DIR / source_file_path.name
        shutil.copy2(source_file_path, destination_path)
        log.info(f"Copied '{source_file_path.name}' to ComfyUI input: {destination_path}")
        return True
    except Exception as e:
        log.error(f"Error copying '{source_file_path.name}' to ComfyUI input: {e}", exc_info=True)
        return False

# --- Main Logic ---
def run_image_generation(project_path: Path, theme: str):
    log.info(f"--- Starting Image Generation for Project: {project_path.name} ---")
    log.info(f"Using Theme: {theme}")

    metadata_path = project_path / "metadata.json"
    if not metadata_path.is_file(): log.error(f"Metadata not found: {metadata_path}"); return False
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
    except Exception as e: log.error(f"Error loading metadata.json: {e}"); return False

    characters_base_path = project_path / "characters"
    source_actors_path = project_path / config.SOURCE_ACTORS_FOLDER_NAME
    medium_images_succeeded = []
    full_images_succeeded = []
    all_actors_processed = []

    # --- Define Unique Run ID for ComfyUI Subfolder ---
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    comfy_run_base_dir = Path(f"API_OUTPUTS/{project_path.name}_{run_timestamp}")
    log.info(f"ComfyUI output base directory for this run: {config.COMFYUI_OUTPUT_DIR / comfy_run_base_dir}")

    # Load the workflow template
    workflow_template = comfyui_interactions.load_workflow_template(config.IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template: log.error("Failed to load workflow template."); return False

    # Loop through characters
    for char_info in movie_info.get('main_characters_actors', []):
        character_name = char_info.get('character_name')
        actor_name = char_info.get('actor_name')
        if not character_name or not actor_name: continue

        all_actors_processed.append(actor_name)
        log.info(f"\nProcessing Character: {character_name} ({actor_name})")
        char_name_sanitized = "".join(c for c in character_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        actor_name_sanitized = "".join(c for c in actor_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        character_dir = characters_base_path / char_name_sanitized
        character_dir.mkdir(parents=True, exist_ok=True)

        # --- Define OUR project's output directories for this character ---
        project_base_img_dir = character_dir / "generated_base_images" # Folder for pre-swap images
        project_swapped_candidates_dir = character_dir / config.GENERATED_SWAPPED_CANDIDATES_FOLDER_NAME # For approval

        # 1. Find the selected source actor image (Check Character name FIRST)
        source_image_found = None
        log.info(f"Looking for source image for Character '{character_name}' (Sanitized: '{char_name_sanitized}') in {source_actors_path}")

        found_by = None

        # Check multiple extensions using CHARACTER name
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            candidate_char = source_actors_path / f"{char_name_sanitized}{ext}" # Use CHARACTER name
            log.debug(f"  Checking by CHAR name: {candidate_char} (Exists: {candidate_char.is_file()})")
            if candidate_char.is_file():
                source_image_found = candidate_char
                found_by = "Character Name"
                break # Found it

        # --- Fallback to Actor Name (Optional - keep commented out unless needed) ---
        # if not source_image_found:
        #     log.info(f"Character name file not found. Checking by Actor name '{actor_name}' (Sanitized: '{actor_name_sanitized}')")
        #     for ext in [".jpg", ".jpeg", ".png", ".webp"]:
        #         candidate_actor = source_actors_path / f"{actor_name_sanitized}{ext}"
        #         log.debug(f"  Checking by ACTOR name: {candidate_actor} (Exists: {candidate_actor.is_file()})")
        #         if candidate_actor.is_file():
        #             source_image_found = candidate_actor
        #             found_by = "Actor Name"
        #             break
        # --- End Fallback ---

        if not source_image_found:
            log.warning(f"Source image NOT FOUND for Character '{character_name}' (or Actor '{actor_name}') in {source_actors_path}. Skipping.")
            continue # Skip this actor entirely
        else:
            log.info(f"Found source image: {source_image_found} (matched by {found_by})")

        # Sub-Loop for Medium and Full Body
        for shot_type in ["medium", "full"]:
            prompt_filename = f"flux_prompt_{shot_type}.txt"
            prompt_file_path = character_dir / prompt_filename

            # Check if final SWAPPED image exists in PROJECT structure
            # Construct prefix based on how ComfyUI *will* name it
            comfy_output_prefix_swapped = f"{config.SWAPPED_IMAGE_PREFIX}{shot_type}_"
            # Check for files starting with this prefix in the candidates dir
            output_check_pattern = f"{comfy_output_prefix_swapped}*.png" # Assume PNG output
            existing_outputs = list(project_swapped_candidates_dir.glob(output_check_pattern))

            if existing_outputs:
                log.info(f"{shot_type.capitalize()} swapped image already exists in project for {character_name}. Skipping ComfyUI run.")
                if shot_type == "medium": medium_images_succeeded.append(actor_name)
                else: full_images_succeeded.append(actor_name)
                continue

            if not prompt_file_path.is_file():
                log.warning(f"{shot_type.capitalize()} prompt not found: {prompt_file_path}. Skipping {shot_type} image generation.")
                continue

            log.info(f"--- Generating {shot_type.capitalize()} Body Image ---")
            try:
                with open(prompt_file_path, 'r', encoding='utf-8') as f: flux_prompt = f.read().strip()

                if not copy_to_comfyui_input(source_image_found):
                    raise IOError("Failed copy source image")

                current_workflow = copy.deepcopy(workflow_template)
                if not current_workflow: raise ValueError("Workflow template copy failed.")

                # --- DEFINE **SEPARATE** COMFYUI SUBFOLDERS & PREFIXES ---
                comfy_char_base_path = comfy_run_base_dir / f"{char_name_sanitized}__{actor_name_sanitized}"

                # *** Specific subfolder for BASE images ***
                comfy_base_image_output_dir = comfy_char_base_path / "base_images"
                # *** Specific subfolder for SWAPPED images ***
                comfy_swapped_image_output_dir = comfy_char_base_path / "swapped_images"

                # Prefixes for the filenames themselves
                base_prefix = f"{config.BASE_IMAGE_PREFIX}{shot_type}_" # e.g., base_medium_
                swapped_prefix = f"{config.SWAPPED_IMAGE_PREFIX}{shot_type}_" # e.g., swapped_medium_
                seed = random.randint(0, 2**32 - 1)
                # ---

                # --- MODIFY WORKFLOW with SEPARATE Directories ---
                inputs_to_set = {
                    config.PROMPT_NODE_TITLE: {"text": flux_prompt},
                    config.SEED_NODE_TITLE: {"noise_seed": seed},
                    config.FACE_NODE_TITLE: {"image": source_image_found.name},
                    # Configure BASE output prefix node to save in its subfolder
                    config.IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE: {
                        "custom_text": base_prefix,
                        "custom_directory": comfy_base_image_output_dir.as_posix(), # Path to *base* subfolder
                        "date_directory": "false"
                    },
                    # Configure SWAPPED output prefix node to save in its subfolder
                    config.IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE: {
                        "custom_text": swapped_prefix,
                        "custom_directory": comfy_swapped_image_output_dir.as_posix(), # Path to *swapped* subfolder
                        "date_directory": "false"
                    }
                }
                if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                    raise ValueError("Failed modify workflow inputs")
                # --- End Modify ---

                log.info(f"Queueing ComfyUI {shot_type} workflow...")
                comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=600) # Increased timeout

                if not comfyui_result_history:
                     raise TimeoutError("ComfyUI workflow failed/timed out.")

                # Success is now determined by workflow completion only
                if comfyui_result_history:
                    if shot_type == "medium": medium_images_succeeded.append(actor_name)
                    else: full_images_succeeded.append(actor_name)
                else:
                    raise ValueError("ComfyUI workflow failed/timed out.")

            except Exception as e:
                 log.error(f"Error during {shot_type} image generation for {character_name}: {e}", exc_info=True)
                 # Do not add to failed lists here, check succeeded lists at the end

        # --- End Sub-Loop ---
    # --- End Character Loop ---

    # --- Final Summary ---
    log.info("--- Finished Image Generation ---")
    unique_medium_success = set(medium_images_succeeded)
    unique_full_success = set(full_images_succeeded)
    log.info(f"Successfully generated/found medium images for {len(unique_medium_success)} actors.")
    log.info(f"Successfully generated/found full body images for {len(unique_full_success)} actors.")

    failed_actors = []
    for actor in all_actors_processed:
        if actor not in unique_medium_success and actor not in unique_full_success:
            failed_actors.append(actor)

    if failed_actors:
         log.warning(f"Failed to generate *any* image type for actors: {', '.join(failed_actors)}")
         return False
    else:
        # Check if *both* types succeeded for *all* actors attempted (stricter check)
        all_succeeded_both = all( (actor in unique_medium_success and actor in unique_full_success) for actor in all_actors_processed )
        if all_succeeded_both:
             log.info("Image generation completed: Both medium and full body images generated/found for all actors.")
             return True
        else:
             log.warning("Image generation completed, but some actors might be missing one image type.")
             return True # Still consider run successful if at least one type worked per actor


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate futuristic character images using ComfyUI.")
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder. If not provided, uses the latest.")
    args = parser.parse_args()

    load_dotenv(dotenv_path=config.DOTENV_PATH)

    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        if not project_to_process.is_dir(): log.error(f"Path not found: {project_to_process}"); exit(1)
    else:
        log.info("Finding latest project...")
        project_to_process = find_latest_project_dir(config.PROJECTS_BASE_DIR)
        if not project_to_process: log.error(f"No project found in {config.PROJECTS_BASE_DIR}"); exit(1)
    log.info(f"Using project: {project_to_process}")

    project_theme = extract_theme_from_project_path(project_to_process)
    if not project_theme or project_theme == "Default Theme": log.error("Cannot determine theme."); exit(1)

    # --- Make sure comfyui_interactions is implemented! ---
    if not hasattr(comfyui_interactions, 'load_workflow_template') or \
       not hasattr(comfyui_interactions, 'modify_workflow_inputs') or \
       not hasattr(comfyui_interactions, 'run_comfyui_workflow') or \
       not hasattr(comfyui_interactions, 'get_output_details_from_history'):
        log.critical("Essential functions seem missing from comfyui_interactions.py! Please implement them.")
        exit(1)
    # --- End Check ---

    success = run_image_generation(project_to_process, project_theme)

    if success: log.info("Script finished successfully."); exit(0)
    else: log.error("Script finished with errors."); exit(1)