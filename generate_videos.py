# generate_videos.py
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
import glob

# Import project modules
try:
    import config
    import comfyui_interactions
    from generate_prompts import find_latest_project_dir, extract_theme_from_project_path
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules: {e}")
    exit(1)

# --- Logging Setup ---
log_file_path = Path(config.LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[logging.FileHandler(log_file_path, mode='a'), logging.StreamHandler()])
except Exception as e: print(f"ERROR setting up logging: {e}"); exit(1)
log = logging.getLogger(__name__)

# --- Helper: Copy Input Files --- (Consolidated)
def copy_files_to_comfyui_input(file_paths: list):
    if not config.COMFYUI_INPUT_DIR or not config.COMFYUI_INPUT_DIR.is_dir():
        log.error(f"ComfyUI Input Dir not configured/found: {config.COMFYUI_INPUT_DIR}")
        return False
    success = True
    for file_path in file_paths:
        if not isinstance(file_path, Path): file_path = Path(file_path) # Ensure Path object
        if not file_path.is_file(): log.warning(f"Input file not found: {file_path}"); success = False; continue
        try:
            destination_path = config.COMFYUI_INPUT_DIR / file_path.name
            shutil.copy2(file_path, destination_path)
            log.info(f"Copied '{file_path.name}' to ComfyUI input: {destination_path}")
        except Exception as e:
            log.error(f"Error copying file '{file_path.name}' to ComfyUI input: {e}", exc_info=True)
            success = False
    return success

# --- Helper: Process ComfyUI Video Output --- (Adapted from image version)
def process_and_copy_video_output(
    comfyui_result_history,
    output_node_title, # Title of the VHS_VideoCombine node
    project_save_dir, # Specific destination directory within *our* project structure
    project_filename_prefix # e.g., "final_clip_medium_1_"
    ):
    # NOTE: VHS_VideoCombine output structure in history might differ from SaveImage.
    # Need to inspect the actual history JSON from a run.
    # Assuming it might have a 'videos' key similar to 'images' or use the generic check
    output_details = comfyui_interactions.get_output_details_from_history(
        comfyui_result_history, output_node_title
    )

    # Check common video node output formats (adjust based on actual history)
    filename_key = None
    output_filename = None
    output_subfolder = ""
    output_type = "output" # Default

    if output_details and isinstance(output_details, dict):
        if 'filename' in output_details: # Direct dict or list item from SaveImage/VHS
             filename_key = 'filename'
        elif 'videos' in output_details and output_details['videos']: # Maybe nested under 'videos'
             output_details = output_details['videos'][0]
             if 'filename' in output_details: filename_key = 'filename'

        if filename_key:
            output_filename = output_details[filename_key]
            output_subfolder = output_details.get('subfolder', '')
            output_type = output_details.get('type', 'output')
            log.info(f"ComfyUI Node '{output_node_title}' output file: {output_filename} (Subfolder: '{output_subfolder}')")
        else:
             log.error(f"Could not find 'filename' key in output details for node '{output_node_title}'. Details: {output_details}")
             return None
    else:
        log.error(f"Could not find valid output details dict for node '{output_node_title}' in history.")
        return None

    # Construct paths and copy
    comfy_output_file_path = config.COMFYUI_OUTPUT_DIR.resolve() / output_subfolder / output_filename
    # Use a more descriptive name combining prefix and original filename parts
    base_name, _ = os.path.splitext(output_filename)
    project_final_filename = f"{project_filename_prefix}{base_name}.mp4" # Assume mp4 output
    project_final_path = project_save_dir / project_final_filename

    if comfy_output_file_path.is_file():
        try:
            project_save_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(comfy_output_file_path, project_final_path)
            log.info(f"Copied ComfyUI video output '{output_filename}' to project: {project_final_path}")
            return project_final_path # Return the destination path object
        except Exception as e:
            log.error(f"Failed copy output video from ComfyUI node '{output_node_title}': {e}", exc_info=True)
    else:
        log.error(f"ComfyUI output video not found at expected location: {comfy_output_file_path}")

    return None # Indicate failure

# --- Helper: Find Latest API Outputs Dir ---
def find_latest_api_outputs_dir(api_outputs_base_dir):
    # List all subdirectories and pick the one with the latest timestamp (by name)
    subdirs = [d for d in Path(api_outputs_base_dir).iterdir() if d.is_dir()]
    if not subdirs:
        return None
    # Sort by folder name descending (assuming timestamp in name)
    sorted_dirs = sorted(subdirs, key=lambda d: d.name, reverse=True)
    return sorted_dirs[0]

# --- Main Logic ---
def run_video_generation():
    log.info("--- Starting Video Generation ---")

    # --- Load latest project and character list from metadata.json ---
    movie_projects_dir = config.PROJECTS_BASE_DIR
    latest_project_dir = max([d for d in movie_projects_dir.iterdir() if d.is_dir()], key=lambda d: d.stat().st_ctime)
    metadata_path = latest_project_dir / "metadata.json"
    if not metadata_path.exists():
        log.error(f"metadata.json not found in {latest_project_dir}")
        return False
    with open(metadata_path, 'r', encoding='utf-8') as f:
        metadata = json.load(f)
    character_name_map = {}
    if "main_characters_actors" in metadata:
        for entry in metadata["main_characters_actors"]:
            char = entry.get("character_name")
            if char:
                # Normalize folder name (replace spaces with underscores)
                char_folder = char.replace(' ', '_')
                character_name_map[char_folder] = char
                # Also add actor name as fallback (normalize spaces)
                actor = entry.get("actor_name")
                if actor:
                    actor_folder = actor.replace(' ', '_')
                    character_name_map[actor_folder] = char
    else:
        log.error("No main_characters_actors in metadata.json")
        return False

    # Load metadata from project folder
    metadata_path = latest_project_dir / "metadata.json"
    if not metadata_path.is_file():
        log.error(f"Metadata not found in {latest_project_dir}. Run previous steps."); return False
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
    except Exception as e:
        log.error(f"Failed to load metadata.json: {e}"); return False

    # Find latest API_OUTPUTS folder for this project
    api_outputs_base_dir = Path(r"H:/dancers_content/API_OUTPUTS")
    latest_api_outputs_dir = find_latest_api_outputs_dir(api_outputs_base_dir)
    if not latest_api_outputs_dir:
        log.error(f"No API_OUTPUTS subdirectory found in {api_outputs_base_dir}")
        return False
    api_characters_dir = latest_api_outputs_dir / "characters"

    # --- Find available character folders in API_OUTPUTS ---
    available_char_folders = set()
    if api_characters_dir.exists():
        available_char_folders = {f.name for f in api_characters_dir.iterdir() if f.is_dir()}
    log.info(f"Available character prompt folders: {sorted(available_char_folders)}")

    # --- Find approved images in API_OUTPUTS ---
    approved_images_dir = latest_api_outputs_dir / "approved_images"
    if not approved_images_dir.exists():
        log.error(f"No approved_images directory found in {latest_api_outputs_dir}")
        return False
    approved_images_list = sorted([f for f in approved_images_dir.iterdir() if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]])

    # For each approved image, match to correct character and prompt
    characters_base_path = latest_project_dir / "characters"
    source_actors_path = latest_project_dir / config.SOURCE_ACTORS_FOLDER_NAME
    videos_generated_count = 0
    actors_failed = []

    for approved_img_path in approved_images_list:
        approved_img_stem = approved_img_path.stem  # e.g. Jaya_Bachchan_approved_medium_1
        # Extract character folder from stem (everything before first _approved_)
        char_folder = approved_img_stem.split('_approved_')[0]
        if char_folder not in character_name_map:
            log.info(f"Character {char_folder} not in metadata.json. Skipping image {approved_img_path.name}.")
            continue
        if char_folder not in available_char_folders:
            log.info(f"No prompt folder for {char_folder}. Skipping image {approved_img_path.name}.")
            continue
        char_api_dir = api_characters_dir / char_folder
        # Find corresponding prompt files
        wan_pos_prompt_path = char_api_dir / f"wan_video_prompt_positive_{approved_img_stem}.txt"
        wan_neg_prompt_path = char_api_dir / f"wan_video_prompt_negative_{approved_img_stem}.txt"
        if not wan_pos_prompt_path.exists() or not wan_neg_prompt_path.exists():
            log.info(f"WanVideo prompt not found for {approved_img_stem} (missing prompt files). Skipping this image.")
            continue
        with open(wan_pos_prompt_path, 'r', encoding='utf-8') as f:
            pos_prompt = f.read().strip()
        with open(wan_neg_prompt_path, 'r', encoding='utf-8') as f:
            neg_prompt = f.read().strip()

        # Find source face image as before (existing logic)
        character_dir = characters_base_path / char_folder
        project_final_videos_dir = character_dir / config.FINAL_VIDEOS_FOLDER_NAME
        source_actor_face_path = source_actors_path / f"{char_folder}.jpg"
        if not source_actor_face_path.is_file():
            log.error(f"Original source face image not found for {char_folder} in {source_actors_path}. Cannot generate videos. Skipping actor.")
            actors_failed.append(char_folder)
            continue

        # Define final video output filename pattern in project folder for checking existence
        final_video_prefix_in_proj = f"{config.FINAL_VIDEO_CLIP_PREFIX}{approved_img_path.stem}_" # e.g., final_clip_Amitabh_approved_medium_1_
        output_check_pattern = f"{final_video_prefix_in_proj}*.mp4"
        existing_outputs = list(project_final_videos_dir.glob(output_check_pattern))
        if existing_outputs:
             log.info(f"    Final video clip already exists for {approved_img_path.name}. Skipping.")
             videos_generated_count += 1 # Count existing as success
             continue

        # --- Prepare and Run ComfyUI ---
        try:
            # Copy BOTH approved image AND source face to ComfyUI Input
            if not copy_files_to_comfyui_input([approved_img_path, source_actor_face_path]):
                raise IOError("Failed to copy input files to ComfyUI.")

            # Load VIDEO workflow template (ensure this is defined before the actor loop)
            workflow_template = comfyui_interactions.load_workflow_template(config.VIDEO_WORKFLOW_TEMPLATE)
            if not workflow_template:
                log.error("Failed to load VIDEO workflow template.")
                return False

            current_workflow = copy.deepcopy(workflow_template)
            if not current_workflow: raise ValueError("Workflow template copy failed.")

            # Define unique prefix for ComfyUI output file naming
            comfy_output_prefix = f"VIDEO_{approved_img_path.stem}_" # Link output to input image
            seed = random.randint(0, 2**32 - 1)

            # Modify workflow inputs
            inputs_to_set = {
                # Assumes node 161 takes positive/negative. Adjust if different.
                config.VIDEO_PROMPT_NODE_TITLE: {
                    "positive_prompt": pos_prompt,
                    "negative_prompt": neg_prompt
                },
                config.VIDEO_SEED_NODE_TITLE: {"seed": seed}, # Use correct title from config
                config.VIDEO_START_IMAGE_NODE_TITLE: {"image": approved_img_path.name},
                config.VIDEO_FACE_NODE_TITLE: {"image": source_actor_face_path.name}, # Use correct title
                config.VIDEO_OUTPUT_PREFIX_NODE_TITLE: { # Use correct title
                    "custom_text": comfy_output_prefix,
                    "custom_directory": "", # Save to ComfyUI main output for simplicity
                    "date_directory": "false"
                }
                # Modify other params like steps, cfg etc. if needed
            }

            if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                raise ValueError("Failed modify video workflow inputs")

            log.info(f"    Queueing ComfyUI video workflow for {approved_img_path.name}...")
            # Potentially longer timeout for video
            comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=1200) # 20 min timeout?

            if not comfyui_result_history:
                 raise TimeoutError("ComfyUI video workflow failed/timed out.")

            # Process and copy video output
            final_video_path = process_and_copy_video_output(
                comfyui_result_history,
                config.VIDEO_OUTPUT_SAVE_NODE_TITLE, # Use correct title
                project_final_videos_dir, # Save to final videos dir in project
                final_video_prefix_in_proj # Use prefix for final name in project
            )

            if final_video_path:
                videos_generated_count += 1
            else:
                raise ValueError("Failed process/copy video output.")

        except Exception as e:
             log.error(f"  Error during video generation for {approved_img_path.name}: {e}", exc_info=True)
             actors_failed.append(char_folder) # Mark actor as failed if any video gen fails

    log.info("--- Finished Video Generation ---")
    log.info(f"Generated/Found {videos_generated_count} final video clips.")
    unique_failed = list(set(actors_failed))
    if unique_failed:
         log.warning(f"Failed video generation for actors: {', '.join(unique_failed)}")
         return False
    else:
         log.info("Video generation completed successfully.")
         return True


# --- Script Execution ---
if __name__ == "__main__":
    # ... (Argument parsing for project_path as before) ...
    parser = argparse.ArgumentParser(description="Generate video clips for approved character images.")
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder. If not provided, uses the latest.")
    args = parser.parse_args()

    load_dotenv(dotenv_path=config.DOTENV_PATH)

    project_to_process = None
    if args.project_path:
        project_to_process = args.project_path
    else:
        project_to_process = find_latest_project_dir(config.PROJECTS_BASE_DIR)
    if not project_to_process: exit(1)
    log.info(f"Using project: {project_to_process}")

    # --- Add Default Negative Prompt to Config ---
    if not hasattr(config, 'DEFAULT_VIDEO_NEGATIVE_PROMPT'):
         log.warning("DEFAULT_VIDEO_NEGATIVE_PROMPT not found in config.py, using empty.")
         config.DEFAULT_VIDEO_NEGATIVE_PROMPT = "" # Add it dynamically if missing
    # ---

    # --- Check ComfyUI Interactions Implementation ---
    if not hasattr(comfyui_interactions, 'run_comfyui_workflow'): # Add more checks if needed
         log.critical("Essential function 'run_comfyui_workflow' missing from comfyui_interactions.py!")
         exit(1)

    success = run_video_generation()

    if success: log.info("Script finished successfully."); exit(0)
    else: log.error("Script finished with errors."); exit(1)