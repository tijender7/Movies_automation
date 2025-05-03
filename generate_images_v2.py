# generate_images_v2.py
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
    import config_v2 as config # Use v2 config
    import comfyui_interactions_v2 as comfyui_interactions # Use v2 interactions
    from generate_prompts_v2 import extract_theme_from_project_path, find_latest_project_dir # Use v2 helpers
except ImportError as e:
    print(f"ERROR: Failed to import necessary v2 modules: {e}")
    print("Make sure config_v2.py, comfyui_interactions_v2.py, and generate_prompts_v2.py exist and are correct.")
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
    if not source_file_path.is_file():
        log.error(f"Source file to copy not found: {source_file_path}")
        return False
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

# --- Helper: Sanitize Name ---
def sanitize_name(name):
    """Sanitizes a name for file/folder usage."""
    return "".join(c for c in name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')

# --- Main Logic ---
def run_image_generation(project_path: Path, theme: str):
    """
    Generates base and swapped images using ComfyUI.
    Reads prompts from the Project Folder.
    Finds the selected source face (by Actor Name) in the Project Folder.
    Writes ALL image outputs to the API Output Folder structure.
    """
    log.info(f"--- Starting Image Generation for Project: {project_path.name} ---")
    log.info(f"Using Theme: {theme}")

    metadata_path = project_path / "metadata.json"
    if not metadata_path.is_file(): log.error(f"Metadata not found: {metadata_path}"); return False
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
    except Exception as e: log.error(f"Error loading metadata.json: {e}"); return False

    # Define paths within the PROJECT structure
    project_characters_base_path = project_path / config.CHARACTERS_FOLDER_NAME
    project_source_actors_path = project_path / config.SOURCE_ACTORS_FOLDER_NAME

    medium_images_succeeded = []
    full_images_succeeded = []
    all_actors_processed = []
    at_least_one_comfy_run_started = False

    # --- Define Unique Run ID for ComfyUI API Output Subfolder ---
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Use Path object directly for the relative path within API_OUTPUTS
    comfy_api_run_subfolder_rel = Path(f"{project_path.name}_{run_timestamp}")
    comfy_api_run_folder_abs = config.COMFYUI_OUTPUT_DIR / config.API_OUTPUTS_SUBDIR / comfy_api_run_subfolder_rel
    log.info(f"ComfyUI output base directory for this run: {comfy_api_run_folder_abs}")
    # Ensure the base API run directory exists (though ComfyUI might create subdirs)
    try:
        (config.COMFYUI_OUTPUT_DIR / config.API_OUTPUTS_SUBDIR).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error(f"Could not create API_OUTPUTS base directory: {e}")
        # Decide if this is fatal - probably yes if ComfyUI relies on it.
        # return False

    # Load the workflow template ONCE
    workflow_template = comfyui_interactions.load_workflow_template(config.IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template:
        log.error("Failed to load workflow template. Cannot proceed."); return False

    # Loop through characters defined in project metadata
    for char_info in movie_info.get('main_characters_actors', []):
        character_name = char_info.get('character_name')
        actor_name = char_info.get('actor_name')
        if not character_name or not actor_name:
            log.warning("Skipping entry in metadata missing character or actor name.")
            continue

        all_actors_processed.append(actor_name)
        log.info(f"\nProcessing Character: {character_name} ({actor_name})")
        char_name_sanitized = sanitize_name(character_name)
        actor_name_sanitized = sanitize_name(actor_name)

        # Path to character data within the PROJECT folder
        project_character_dir = project_characters_base_path / char_name_sanitized
        # This directory should exist from previous steps (prompt generation)

        # 1. Find the selected source actor image IN THE PROJECT FOLDER
        #    The file MUST be named using the Actor Name as per convention.
        source_image_found = None
        log.info(f"Looking for source image for Actor '{actor_name}' (Sanitized: '{actor_name_sanitized}') in {project_source_actors_path}")
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            candidate_actor_path = project_source_actors_path / f"{actor_name_sanitized}{ext}"
            log.debug(f"  Checking for: {candidate_actor_path}")
            if candidate_actor_path.is_file():
                source_image_found = candidate_actor_path
                log.info(f"Found source image: {source_image_found}")
                break # Found it

        if not source_image_found:
            log.error(f"Source image for Actor '{actor_name}' NOT FOUND in {project_source_actors_path} using sanitized name '{actor_name_sanitized}'. Skipping actor.")
            continue # Skip this actor entirely

        # Sub-Loop for Medium and Full Body image generation
        for shot_type in ["medium", "full"]:
            prompt_filename = f"flux_prompt_{shot_type}.txt"
            # Prompt file location is in the PROJECT character directory
            prompt_file_path = project_character_dir / prompt_filename

            # --- REMOVED CHECK FOR EXISTING *SWAPPED* IMAGES IN *PROJECT* STRUCTURE ---
            # This script's responsibility is to generate into API_OUTPUTS.
            # Checking for existing outputs should happen in the APPROVAL script
            # or by inspecting the API_OUTPUTS folder if needed elsewhere.

            if not prompt_file_path.is_file():
                log.warning(f"{shot_type.capitalize()} prompt not found: {prompt_file_path}. Skipping {shot_type} image generation for {character_name}.")
                continue

            log.info(f"--- Generating {shot_type.capitalize()} Body Image for {character_name} ---")
            try:
                at_least_one_comfy_run_started = True # Mark that we attempted a run
                with open(prompt_file_path, 'r', encoding='utf-8') as f: flux_prompt = f.read().strip()
                log.debug(f"Loaded {shot_type} prompt: {flux_prompt[:100]}...")

                # Copy the required source face image to ComfyUI's input directory
                if not copy_to_comfyui_input(source_image_found):
                    # Raise specific error to be caught below
                    raise IOError(f"Failed copy source image {source_image_found.name} to ComfyUI input.")

                # --- DEFINE COMFYUI **API OUTPUT** SUBFOLDERS & FILENAME PREFIXES ---
                # Use Path objects and relative paths for clarity
                comfy_char_actor_rel_path = Path(f"{char_name_sanitized}__{actor_name_sanitized}")

                # Specific relative subfolder for BASE images within API run -> char__actor folder
                comfy_base_img_output_rel_path = comfy_char_actor_rel_path / config.COMFYUI_BASE_IMAGES_SUBFOLDER
                # Specific relative subfolder for SWAPPED images within API run -> char__actor folder
                comfy_swapped_img_output_rel_path = comfy_char_actor_rel_path / config.COMFYUI_SWAPPED_IMAGES_SUBFOLDER

                # Combine with the overall run folder path for the prefix node setting
                # These paths MUST be relative to `API_OUTPUTS` base dir for the prefix node if it works that way,
                # OR relative to the ComfyUI output base if that's how the node interprets it.
                # Let's assume relative to the ComfyUI output base (H:/dancers_content) is safer.
                # If Prefix node needs relative to API_OUTPUTS base, adjust here.

                # Path relative to COMFYUI_OUTPUT_DIR/API_OUTPUTS/ for the prefix node
                # e.g., "Sholay_cyberphunk_..._RunID/Jai__Amitabh_Bachchan/base_images"
                prefix_node_base_dir = comfy_api_run_subfolder_rel / comfy_base_img_output_rel_path
                prefix_node_swapped_dir = comfy_api_run_subfolder_rel / comfy_swapped_img_output_rel_path

                # Filename prefixes themselves
                base_filename_prefix = f"{config.BASE_IMAGE_PREFIX}{shot_type}_" # e.g., base_medium_
                swapped_filename_prefix = f"{config.SWAPPED_IMAGE_PREFIX}{shot_type}_" # e.g., swapped_medium_
                seed = random.randint(0, 2**32 - 1)
                log.debug(f"Seed for {shot_type}: {seed}")
                log.debug(f"ComfyUI Base Output Dir (for prefix node): {prefix_node_base_dir.as_posix()}")
                log.debug(f"ComfyUI Swapped Output Dir (for prefix node): {prefix_node_swapped_dir.as_posix()}")
                # ---

                # --- PREPARE AND MODIFY WORKFLOW ---
                current_workflow = copy.deepcopy(workflow_template)
                if not current_workflow:
                    raise ValueError("Workflow template deepcopy failed.")

                # Define the inputs to set in the workflow JSON
                inputs_to_set = {
                    config.PROMPT_NODE_TITLE: {"text": flux_prompt}, # Assuming 'text' input
                    config.SEED_NODE_TITLE: {"noise_seed": seed},     # Assuming 'noise_seed' input
                    config.FACE_NODE_TITLE: {"image": source_image_found.name}, # Pass filename only
                    # Configure BASE output prefix node
                    config.IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE: {
                        "custom_text": base_filename_prefix,
                        # Provide path POSIX style, relative to API_OUTPUTS base
                        "custom_directory": prefix_node_base_dir.as_posix(),
                        "date_directory": "false" # Disable ComfyUI adding date folders
                    },
                    # Configure SWAPPED output prefix node
                    config.IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE: {
                        "custom_text": swapped_filename_prefix,
                         # Provide path POSIX style, relative to API_OUTPUTS base
                        "custom_directory": prefix_node_swapped_dir.as_posix(),
                        "date_directory": "false" # Disable ComfyUI adding date folders
                    }
                }

                log.info(f"Modifying workflow inputs for {shot_type}...")
                if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                    raise ValueError("Failed to modify workflow inputs using comfyui_interactions.")
                # --- End Modify ---

                # --- EXECUTE WORKFLOW ---
                log.info(f"Queueing ComfyUI {shot_type} workflow for {character_name}...")
                # Increased timeout for potentially complex workflows
                comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=600)

                if not comfyui_result_history:
                    # Specific timeout error might be helpful if run_comfyui_workflow can distinguish
                    raise TimeoutError("ComfyUI workflow execution failed or timed out.")
                else:
                    # Log success based on getting *any* history back.
                    # Further checks could parse history for specific node outputs if needed.
                    log.info(f"ComfyUI {shot_type} workflow completed successfully for {character_name}.")
                    if shot_type == "medium": medium_images_succeeded.append(actor_name)
                    else: full_images_succeeded.append(actor_name)
                # --- End Execute ---

            except (IOError, ValueError, TimeoutError, TypeError) as e: # Catch specific errors
                 log.error(f"Error during {shot_type} image generation for {character_name}: {e}")
                 # Don't add to failed list here, check succeeded lists at the end
            except Exception as e_gen: # Catch any other unexpected errors
                 log.error(f"Unexpected error during {shot_type} image generation for {character_name}: {e_gen}", exc_info=True)

        # --- End Sub-Loop (medium/full) ---
    # --- End Character Loop ---

    # --- Final Summary ---
    log.info("\n--- Finished Image Generation Phase ---")
    if not at_least_one_comfy_run_started:
        log.warning("No ComfyUI image generation runs were attempted (check for missing prompts or source images).")
        return False # Indicate nothing happened

    unique_medium_success = set(medium_images_succeeded)
    unique_full_success = set(full_images_succeeded)
    log.info(f"Successfully generated medium images for {len(unique_medium_success)} out of {len(all_actors_processed)} processed actors.")
    log.info(f"Successfully generated full body images for {len(unique_full_success)} out of {len(all_actors_processed)} processed actors.")
    log.info(f"Outputs saved within ComfyUI API Run Folder: {comfy_api_run_folder_abs}")


    # Determine overall success based on whether *at least one* image type was generated for each actor *attempted*
    all_attempted_actors_had_some_success = True
    failed_actors = []
    for actor in all_actors_processed:
        # Check if we attempted generation for this actor (i.e., source image was found)
        # A simple check is if they are in the processed list but not successful in either category
        if actor not in unique_medium_success and actor not in unique_full_success:
             # Double-check if a source image was actually found for them
             actor_name_sanitized = sanitize_name(actor)
             found_source = any((project_source_actors_path / f"{actor_name_sanitized}{ext}").is_file() for ext in [".jpg", ".jpeg", ".png", ".webp"])
             if found_source: # Only count as failed if we found source but generated nothing
                failed_actors.append(actor)
                all_attempted_actors_had_some_success = False

    if failed_actors:
         log.warning(f"Failed to generate *any* image type for actors (where source image existed): {', '.join(failed_actors)}")
         # Still return True if *some* actors succeeded, but log clearly.
         # If failure for *any* actor should halt the pipeline, return False here.
         # Let's consider it a partial success if anything was generated.
         return True
    elif not all_actors_processed:
         log.warning("No actors were processed (check metadata).")
         return False
    else:
        # Check if *both* types succeeded for *all* actors successfully processed
        all_succeeded_both = all( (actor in unique_medium_success and actor in unique_full_success) for actor in unique_medium_success.union(unique_full_success) )
        if all_succeeded_both:
             log.info("Image generation completed: Both medium and full body images generated for all processed actors.")
        else:
             log.warning("Image generation completed, but some actors might be missing one image type.")
        return True # Overall success

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate character images using ComfyUI (V2).")
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder (in Movie_Projects). If not provided, uses the latest.")
    args = parser.parse_args()

    load_dotenv(dotenv_path=config.DOTENV_PATH)

    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        if not project_to_process.is_dir() or not project_to_process.parent.samefile(config.PROJECTS_BASE_DIR):
             log.error(f"Provided project path is not a valid directory within {config.PROJECTS_BASE_DIR}: {project_to_process}"); exit(1)
    else:
        log.info(f"Finding latest project directory in {config.PROJECTS_BASE_DIR}...")
        project_to_process = find_latest_project_dir(config.PROJECTS_BASE_DIR)
        if not project_to_process:
             log.error(f"No project found in {config.PROJECTS_BASE_DIR}. Run main orchestrator first."); exit(1)
    log.info(f"Using project: {project_to_process}")

    project_theme = extract_theme_from_project_path(project_to_process)
    if not project_theme or project_theme == "Default Theme":
        log.error(f"Could not determine theme from project path: {project_to_process}. Ensure folder name format is correct (Movie_Theme_Timestamp)."); exit(1)

    # --- Check comfyui_interactions ---
    if not hasattr(comfyui_interactions, 'load_workflow_template') or \
       not hasattr(comfyui_interactions, 'modify_workflow_inputs') or \
       not hasattr(comfyui_interactions, 'run_comfyui_workflow'):
        log.critical("Essential functions seem missing from comfyui_interactions_v2.py! Please ensure it is correct.")
        exit(1)
    # --- End Check ---

    success = run_image_generation(project_to_process, project_theme)

    if success:
        log.info("generate_images_v2.py finished successfully (or partially successfully).")
        exit(0)
    else:
        log.error("generate_images_v2.py finished with critical errors or no generation attempted.")
        exit(1)