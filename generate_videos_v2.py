# generate_videos_v2.py
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
import glob # Keep glob for potential pattern matching if needed, though direct iteration is preferred now

# Import project modules
try:
    import config_v2 as config # Use v2
    import comfyui_interactions_v2 as comfyui_interactions # Use v2
    # Use v2 helpers, alias find_latest_project_dir to avoid name clash if needed elsewhere
    from generate_prompts_v2 import find_latest_project_dir as find_latest_project_dir_in_projects
except ImportError as e:
    print(f"ERROR: Failed to import necessary v2 modules: {e}")
    print("Ensure config_v2.py, comfyui_interactions_v2.py, generate_prompts_v2.py exist.")
    exit(1)

# --- Logging Setup ---
log_file_path = Path(config.LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[logging.FileHandler(log_file_path, mode='a'), logging.StreamHandler()]
    )
except Exception as e:
    print(f"ERROR setting up logging: {e}")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()])
    # exit(1) # Let script continue with stream logging if file fails
log = logging.getLogger(__name__)

# --- Helper: Sanitize Name ---
def sanitize_name(name):
    """Sanitizes a name for file/folder usage."""
    return "".join(c for c in name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')

# --- Helper: Copy Input Files to ComfyUI Input --- (Consolidated)
def copy_files_to_comfyui_input(file_paths: list):
    """Copies a list of files to the configured ComfyUI input directory."""
    if not config.COMFYUI_INPUT_DIR or not config.COMFYUI_INPUT_DIR.is_dir():
        log.error(f"ComfyUI Input Dir not configured/found: {config.COMFYUI_INPUT_DIR}")
        return False
    success = True
    copied_filenames = []
    for file_path in file_paths:
        if not isinstance(file_path, Path): file_path = Path(file_path) # Ensure Path object
        if not file_path.is_file():
            log.warning(f"Input file not found, cannot copy: {file_path}")
            success = False
            continue # Skip this file
        try:
            destination_path = config.COMFYUI_INPUT_DIR / file_path.name
            # Optional: Check if file already exists and maybe skip copy? For now, overwrite.
            # if destination_path.exists():
            #     log.debug(f"File '{file_path.name}' already exists in ComfyUI input. Skipping copy.")
            #     copied_filenames.append(file_path.name)
            #     continue
            shutil.copy2(file_path, destination_path)
            log.info(f"Copied '{file_path.name}' to ComfyUI input: {destination_path}")
            copied_filenames.append(file_path.name) # Keep track of filenames passed to ComfyUI
        except Exception as e:
            log.error(f"Error copying file '{file_path.name}' to ComfyUI input: {e}", exc_info=True)
            success = False # Mark overall copy as failed if any file fails
    # Return success status and the list of filenames intended for ComfyUI nodes
    return success, copied_filenames


# --- Helper: Process ComfyUI Video Output ---
def process_and_copy_video_output(
    comfyui_result_history,
    output_node_title, # Title of the VHS_VideoCombine node (or similar)
    api_run_final_video_save_dir, # Specific destination directory within *API Run* structure
    project_filename_prefix # e.g., "final_clip_Amitabh_Bachchan_approved_medium_1_"
    ):
    """
    Finds the video output from history, copies it to the API run's final_videos folder,
    and returns the path to the copied file or None.
    """
    # Get the output details for the specified node title
    output_details = comfyui_interactions.get_output_details_from_history(
        comfyui_result_history, output_node_title
    )

    # --- Extract filename and subfolder from history ---
    # This part depends HEAVILY on the structure of the output node's data in history.
    # Inspect the JSON output of `get_history(prompt_id)` for your video workflow.
    output_filename = None
    output_subfolder = "" # Relative to ComfyUI base output dir (e.g., H:/dancers_content)

    if output_details and isinstance(output_details, dict):
        # Common patterns: Check for 'videos' list or direct 'filename' key
        if 'videos' in output_details and isinstance(output_details['videos'], list) and output_details['videos']:
             video_info = output_details['videos'][0] # Assume first video in list
             if isinstance(video_info, dict):
                 output_filename = video_info.get('filename')
                 output_subfolder = video_info.get('subfolder', '')
                 # type = video_info.get('type', 'output') # Usually 'output' or 'temp'
        elif 'filename' in output_details: # Direct key (less common for video nodes?)
             output_filename = output_details['filename']
             output_subfolder = output_details.get('subfolder', '')

        if output_filename:
             log.info(f"Found ComfyUI Node '{output_node_title}' output file: '{output_filename}' (Subfolder: '{output_subfolder}')")
        else:
             log.error(f"Could not find 'filename' key within expected structure in output details for node '{output_node_title}'. Details: {output_details}")
             return None
    else:
        log.error(f"Could not find valid output details dict for node '{output_node_title}' in history. History might be incomplete or node title mismatch.")
        log.debug(f"Full history dump (partial): {str(comfyui_result_history)[:1000]}")
        return None
    # --- End Extraction ---

    # Construct the full path to the video file within ComfyUI's output directory
    comfy_output_video_path = (config.COMFYUI_OUTPUT_DIR.resolve() / output_subfolder / output_filename).resolve()

    # Define the final destination path within the API RUN folder
    # Use a descriptive name combining the prefix and parts of the original filename
    # Example prefix: final_clip_Amitabh_Bachchan_approved_medium_1_
    # Example comfy output: VIDEO_Amitabh_Bachchan_approved_medium_1_00001_.mp4
    # Example final name: final_clip_Amitabh_Bachchan_approved_medium_1_00001_.mp4 (or keep prefix only)

    # Option 1: Use prefix + comfy filename stem
    # comfy_base_name, _ = os.path.splitext(output_filename)
    # project_final_filename = f"{project_filename_prefix}{comfy_base_name}.mp4" # Assumes mp4

    # Option 2: Simpler - just use prefix + comfy filename
    project_final_filename = f"{project_filename_prefix}{output_filename}"

    # Option 3: Maybe just use the prefix + simple counter? Less informative.

    # Let's use Option 2 for now.
    api_run_final_video_path = api_run_final_video_save_dir / project_final_filename

    if comfy_output_video_path.is_file():
        try:
            # Ensure the character-specific final video directory exists within the API run folder
            api_run_final_video_save_dir.mkdir(parents=True, exist_ok=True)

            shutil.copy2(comfy_output_video_path, api_run_final_video_path)
            log.info(f"Copied ComfyUI video output '{output_filename}' to API run folder:")
            log.info(f"  -> {api_run_final_video_path}")
            return api_run_final_video_path # Return the destination path object
        except Exception as e:
            log.error(f"Failed copy output video '{comfy_output_video_path.name}' for node '{output_node_title}': {e}", exc_info=True)
            return None
    else:
        log.error(f"ComfyUI output video not found at expected location: {comfy_output_video_path}")
        return None


# --- Helper: Find Latest API_OUTPUTS Run Folder ---
# (Copied from approve_images_v2 for consistency)
def find_latest_api_output_run_dir(api_outputs_base: Path) -> Path | None:
    api_outputs_dir = api_outputs_base / config.API_OUTPUTS_SUBDIR
    if not api_outputs_dir.is_dir(): return None
    subdirs = [d for d in api_outputs_dir.iterdir() if d.is_dir()]
    if not subdirs: return None
    try: return max(subdirs, key=lambda d: d.stat().st_mtime)
    except: return None

# --- Main Logic ---
def run_video_generation(project_path: Path, api_run_path: Path):
    """
    Generates final video clips using ComfyUI based on approved images and video prompts.
    Reads approvals, approved images, and video prompts from the API Run Folder.
    Reads the source actor face image from the Project Folder.
    Writes final video outputs to the API Run Folder structure.
    """
    log.info("--- Starting Video Generation (V2) ---")
    log.info(f"Using Project Folder: {project_path}")
    log.info(f"Using API Run Folder: {api_run_path}")

    # --- Define Paths ---
    # Project Folder Paths
    project_metadata_path = project_path / "metadata.json"
    project_source_actors_path = project_path / config.SOURCE_ACTORS_FOLDER_NAME

    # API Run Folder Paths
    api_approval_log_path = api_run_path / "approved_selections.json"
    api_characters_prompt_dir = api_run_path / config.API_CHARACTERS_FOLDER_NAME # Contains Wan/LTX prompts
    api_final_videos_base_dir = api_run_path / config.FINAL_VIDEOS_FOLDER_NAME # Base for all final videos

    # --- Validate Paths ---
    if not project_metadata_path.is_file():
        log.error(f"Project metadata not found: {project_metadata_path}. Run main orchestrator first."); return False
    if not project_source_actors_path.is_dir():
        log.error(f"Project source actors folder not found: {project_source_actors_path}. Run main orchestrator first."); return False
    if not api_approval_log_path.is_file():
        log.error(f"Approval log not found in API run folder: {api_approval_log_path}. Run approve_images_v2.py first."); return False
    if not api_characters_prompt_dir.is_dir():
        log.warning(f"API run characters prompt folder not found: {api_characters_prompt_dir}. Run generate_video_prompts_v2.py first. Cannot generate videos.")
        return False # Need prompts to generate videos

    # --- Load Data ---
    try:
        with open(project_metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
        with open(api_approval_log_path, 'r', encoding='utf-8') as f: approved_selections = json.load(f)
        log.info(f"Loaded project metadata and approval selections.")
    except Exception as e:
        log.error(f"Error loading metadata ({project_metadata_path}) or approval file ({api_approval_log_path}): {e}"); return False

    # Build Actor -> Character map from project metadata
    actor_char_map = {
        item['actor_name']: item['character_name']
        for item in movie_info.get('main_characters_actors', [])
        if item.get('actor_name') and item.get('character_name')
    }
    if not actor_char_map:
        log.error("Could not build Actor->Character map from project metadata."); return False

    # Load the VIDEO workflow template ONCE
    workflow_template = comfyui_interactions.load_workflow_template(config.VIDEO_WORKFLOW_TEMPLATE)
    if not workflow_template:
        log.error(f"Failed to load VIDEO workflow template: {config.VIDEO_WORKFLOW_TEMPLATE}"); return False

    # --- Process Approved Selections for Video Generation ---
    videos_generated_count = 0
    actors_processed_count = 0
    actors_failed = []
    total_approved_images = sum(len(paths) for paths in approved_selections.values() if isinstance(paths, list))
    log.info(f"Found {total_approved_images} approved image(s) to process for video generation.")

    # Iterate through the actors and their approved image paths from the JSON
    for actor_name, approved_img_paths_list in approved_selections.items():

        # Skip if actor was skipped or had no images approved
        if not isinstance(approved_img_paths_list, list) or not approved_img_paths_list:
            log.debug(f"Skipping video generation for actor '{actor_name}' (skipped or no approved images).")
            continue

        actors_processed_count += 1

        # Get Character Name from map
        character_name = actor_char_map.get(actor_name)
        if not character_name:
            log.warning(f"Could not find character name for actor '{actor_name}'. Skipping video generation for this actor.")
            actors_failed.append(actor_name)
            continue

        log.info(f"\nProcessing Actor: '{actor_name}' (Character: '{character_name}') for video generation.")
        char_name_sanitized = sanitize_name(character_name)
        actor_name_sanitized = sanitize_name(actor_name)

        # --- Find the Source Actor Face image (in Project Folder) ---
        source_actor_face_path = None
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            candidate_path = project_source_actors_path / f"{actor_name_sanitized}{ext}"
            if candidate_path.is_file():
                source_actor_face_path = candidate_path
                log.info(f"  Found source actor face image: {source_actor_face_path}")
                break
        if not source_actor_face_path:
            log.error(f"  Original source face image for actor '{actor_name}' not found in {project_source_actors_path} (using name '{actor_name_sanitized}'). Cannot generate videos for this actor. Skipping.")
            actors_failed.append(actor_name)
            continue # Skip this actor

        # Define paths for this character within the API Run Folder
        # Video prompts location
        api_char_prompt_dir = api_characters_prompt_dir / char_name_sanitized
        # Final video output location (character specific)
        api_final_video_char_dir = api_final_videos_base_dir / char_name_sanitized
        # Don't create api_final_video_char_dir here, let the copy helper do it if needed.

        # --- Loop through each approved image for this actor ---
        for approved_img_path_str in approved_img_paths_list:
            try:
                approved_img_path = Path(approved_img_path_str)
                if not approved_img_path.is_file():
                    log.warning(f"  Approved image file path does not exist: {approved_img_path}. Skipping this video.")
                    continue

                log.info(f"  Generating video for approved image: {approved_img_path.name}")
                approved_img_stem = approved_img_path.stem # e.g., Amitabh_Bachchan_approved_medium_1

                # --- Find Corresponding Video Prompts (in API Run Folder) ---
                # Using WanVideo prompts for this example workflow
                wan_pos_prompt_path = api_char_prompt_dir / f"wan_video_prompt_positive_{approved_img_stem}.txt"
                wan_neg_prompt_path = api_char_prompt_dir / f"wan_video_prompt_negative_{approved_img_stem}.txt"

                if not wan_pos_prompt_path.is_file() or not wan_neg_prompt_path.is_file():
                    log.warning(f"    WanVideo prompts not found for image stem '{approved_img_stem}' in {api_char_prompt_dir}. Skipping this video.")
                    # Optionally check for LTX prompts and use those if Wan are missing
                    continue # Skip this specific image/video

                # Load the prompts
                try:
                    with open(wan_pos_prompt_path, 'r', encoding='utf-8') as f: pos_prompt = f.read().strip()
                    with open(wan_neg_prompt_path, 'r', encoding='utf-8') as f: neg_prompt = f.read().strip()
                    log.debug(f"    Loaded Wan prompts: Positive='{pos_prompt[:50]}...', Negative='{neg_prompt[:50]}...'")
                except Exception as e:
                    log.error(f"    Error reading prompt files for {approved_img_stem}: {e}")
                    continue # Skip this video if prompts can't be read

                # --- Check if Final Video Already Exists (in API Run Folder) ---
                # Define final video output filename pattern IN THE API RUN FOLDER
                final_video_filename_prefix = f"{config.FINAL_VIDEO_CLIP_PREFIX}{approved_img_stem}_" # e.g., final_clip_Amitabh_Bachchan_approved_medium_1_
                # Check for files starting with this prefix in the character's final video dir
                output_check_pattern = f"{final_video_filename_prefix}*" # Check for any extension initially, refine if needed
                existing_outputs = list(api_final_video_char_dir.glob(output_check_pattern))

                if existing_outputs:
                     log.info(f"    Final video clip matching '{output_check_pattern}' already exists in {api_final_video_char_dir}. Skipping generation.")
                     # Make sure to count existing as success if needed for stats
                     videos_generated_count += 1 # Count existing as success
                     continue # Skip to next approved image

                # --- Prepare and Run ComfyUI Video Workflow ---
                # Copy required files (approved image, source face) to ComfyUI Input
                copy_ok, comfy_input_filenames = copy_files_to_comfyui_input([approved_img_path, source_actor_face_path])
                if not copy_ok:
                    raise IOError("Failed to copy required input files to ComfyUI input directory.")
                # Ensure filenames used in workflow match those potentially copied
                approved_img_filename_for_node = approved_img_path.name
                source_face_filename_for_node = source_actor_face_path.name
                if approved_img_filename_for_node not in comfy_input_filenames or \
                   source_face_filename_for_node not in comfy_input_filenames:
                    log.warning("Mismatch between expected filenames and copied filenames for ComfyUI input. Workflow might fail.")
                    # Decide whether to continue or raise error

                # Prepare workflow modifications
                current_workflow = copy.deepcopy(workflow_template)
                if not current_workflow: raise ValueError("Workflow template deepcopy failed.")

                # Define unique prefix for ComfyUI *intermediate* output file naming (helps debugging)
                # This prefix is used by the ComfyUI Save node, not the final copied filename
                comfy_output_prefix = f"VIDEO_{approved_img_stem}_" # Link ComfyUI output to input image
                seed = random.randint(0, 2**32 - 1)
                log.debug(f"    Using Seed: {seed}")

                # Modify workflow inputs using titles from config_v2.py
                inputs_to_set = {
                    # Assumes node 161 (WanVideoTextEncode) takes dict with these keys
                    config.VIDEO_PROMPT_NODE_TITLE: {
                        "positive_prompt": pos_prompt,
                        "negative_prompt": neg_prompt # Or use config.DEFAULT_VIDEO_NEGATIVE_PROMPT if neg_prompt is empty/bad
                    },
                    config.VIDEO_SEED_NODE_TITLE: {"seed": seed},
                    config.VIDEO_START_IMAGE_NODE_TITLE: {"image": approved_img_filename_for_node},
                    config.VIDEO_FACE_NODE_TITLE: {"image": source_face_filename_for_node},
                    config.VIDEO_OUTPUT_PREFIX_NODE_TITLE: {
                        "custom_text": comfy_output_prefix,
                        # Save intermediate to ComfyUI main output dir for simplicity
                        # Subfolder will be empty unless specified by the Save node itself
                        "custom_directory": "",
                        "date_directory": "false" # Disable ComfyUI adding date folders
                    }
                    # Add other parameters here if needed (e.g., steps, cfg, frame rate) by finding their node titles
                }

                log.info(f"    Modifying video workflow inputs...")
                if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                    raise ValueError("Failed modify video workflow inputs via comfyui_interactions.")

                log.info(f"    Queueing ComfyUI video workflow for {approved_img_path.name}...")
                # Use a potentially longer timeout for video generation
                comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=1800) # 30 min timeout? Adjust as needed

                if not comfyui_result_history:
                     raise TimeoutError("ComfyUI video workflow execution failed or timed out.")

                # Process and copy the final video output from ComfyUI's dir to API Run's dir
                log.info(f"    Processing ComfyUI output video...")
                final_video_path_in_api_run = process_and_copy_video_output(
                    comfyui_result_history,
                    config.VIDEO_OUTPUT_SAVE_NODE_TITLE, # Use the correct title for the final video save/combine node
                    api_final_video_char_dir,         # Destination: API Run's final video folder for this character
                    final_video_filename_prefix       # Prefix for the filename in the destination folder
                )

                if final_video_path_in_api_run:
                    videos_generated_count += 1
                    log.info(f"    Successfully generated and saved video: {final_video_path_in_api_run.name}")
                else:
                    # Error logged within process_and_copy_video_output
                    raise ValueError(f"Failed process/copy video output for {approved_img_path.name}.")

            except (IOError, ValueError, TimeoutError, TypeError) as e:
                 log.error(f"  Error during video generation for {approved_img_path_str}: {e}")
                 actors_failed.append(actor_name) # Mark actor as failed if any video gen fails for them
            except Exception as e_gen:
                 log.error(f"  Unexpected error during video generation for {approved_img_path_str}: {e_gen}", exc_info=True)
                 actors_failed.append(actor_name) # Mark actor as failed

        # --- End loop through approved images for this actor ---
    # --- End loop through actors ---

    log.info("\n--- Finished Video Generation ---")
    log.info(f"Attempted processing for {actors_processed_count} actors.")
    log.info(f"Generated/Found {videos_generated_count} final video clips.")
    log.info(f"Final videos saved within API Run Folder: {api_final_videos_base_dir}")

    unique_failed = sorted(list(set(actors_failed)))
    if unique_failed:
         log.warning(f"Video generation encountered errors for actors: {', '.join(unique_failed)}")
         # Return True if some videos were generated, False only if critical failure occurred
         return videos_generated_count > 0 # True if at least one video succeeded
    elif actors_processed_count == 0 and total_approved_images > 0:
        log.error("Processed 0 actors, but approval log showed approved images. Check Actor->Character mapping or source face issues.")
        return False # Likely a configuration error
    elif actors_processed_count == 0 and total_approved_images == 0:
         log.info("No actors had approved images to process. Video generation step skipped.")
         return True # Successful because there was nothing to do
    else:
         log.info("Video generation completed successfully for all processed images.")
         return True


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate video clips for approved character images (V2).")
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder (in Movie_Projects). If not provided, uses the latest.")
    parser.add_argument("-r", "--run_folder_path", help="Path to the specific API run folder (in API_OUTPUTS). If not provided, uses the latest.")
    args = parser.parse_args()

    load_dotenv(dotenv_path=config.DOTENV_PATH)

    # --- Determine Project Path ---
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        if not project_to_process.is_dir() or not project_to_process.parent.samefile(config.PROJECTS_BASE_DIR):
             log.error(f"Provided project path is not valid: {project_to_process}"); exit(1)
    else:
        log.info(f"Finding latest project directory in {config.PROJECTS_BASE_DIR}...")
        project_to_process = find_latest_project_dir_in_projects(config.PROJECTS_BASE_DIR)
        if not project_to_process:
             log.error(f"No project found in {config.PROJECTS_BASE_DIR}."); exit(1)
    log.info(f"Using project folder: {project_to_process}")

    # --- Determine API Run Folder Path ---
    api_run_folder_to_process = None
    api_outputs_base = config.COMFYUI_OUTPUT_DIR
    if args.run_folder_path:
        provided_path = Path(args.run_folder_path)
        if provided_path.is_dir() and str(provided_path.resolve()).startswith(str(api_outputs_base.resolve() / config.API_OUTPUTS_SUBDIR)):
            api_run_folder_to_process = provided_path.resolve()
        else:
             log.error(f"Provided API run path '{args.run_folder_path}' is not valid."); exit(1)
    else:
        log.info(f"Finding latest API run folder in {api_outputs_base / config.API_OUTPUTS_SUBDIR}...")
        api_run_folder_to_process = find_latest_api_output_run_dir(api_outputs_base)
        if not api_run_folder_to_process:
             log.error(f"No API run folder found. Run previous steps first."); exit(1)
    log.info(f"Using API run folder: {api_run_folder_to_process}")

    # --- Check ComfyUI Interactions Implementation ---
    if not hasattr(comfyui_interactions, 'run_comfyui_workflow') or \
       not hasattr(comfyui_interactions, 'modify_workflow_inputs') or \
       not hasattr(comfyui_interactions, 'get_output_details_from_history'):
         log.critical("Essential functions missing from comfyui_interactions_v2.py!")
         exit(1)
    # ---

    success = run_video_generation(project_to_process, api_run_folder_to_process)

    if success:
        log.info("generate_videos_v2.py finished successfully (or partially successfully).")
        exit(0)
    else:
        log.error("generate_videos_v2.py finished with critical errors or no videos generated.")
        exit(1)