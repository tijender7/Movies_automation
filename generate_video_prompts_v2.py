# generate_video_prompts_v2.py
import logging
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import argparse
import re # For parsing filenames if needed later

# Import project modules
try:
    import config_v2 as config # Use v2 config
    import llm_interactions_v2 as llm_interactions # Needs the new generate_video_prompt_from_image (use v2)
    # Reuse helpers from v2 prompt generation script
    from generate_prompts_v2 import extract_theme_from_project_path, find_latest_project_dir as find_latest_project_dir_in_projects
except ImportError as e:
    print(f"ERROR: Failed to import necessary v2 modules: {e}")
    print("Make sure config_v2.py, llm_interactions_v2.py, and generate_prompts_v2.py exist and are correct.")
    exit(1)

# --- Logging Setup ---
log_file_path = Path(config.LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, mode='a'),
            logging.StreamHandler()
        ]
    )
except Exception as e:
    print(f"ERROR setting up logging: {e}")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# --- Helper: Sanitize Name ---
def sanitize_name(name):
    """Sanitizes a name for file/folder usage."""
    return "".join(c for c in name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')

# --- Helper Function to Find Latest API_OUTPUTS Run Folder ---
# (Copied from approve_images_v2 for consistency)
def find_latest_api_output_run_dir(api_outputs_base: Path) -> Path | None:
    """
    Find the most recently modified subdirectory within the API_OUTPUTS base directory.
    Returns the Path object or None.
    """
    api_outputs_dir = api_outputs_base / config.API_OUTPUTS_SUBDIR
    if not api_outputs_dir.exists() or not api_outputs_dir.is_dir():
        log.error(f"{config.API_OUTPUTS_SUBDIR} directory does not exist: {api_outputs_dir}")
        return None
    subdirs = [d for d in api_outputs_dir.iterdir() if d.is_dir()]
    if not subdirs:
        log.error(f"No run subdirectories found in {api_outputs_dir}")
        return None
    try:
        latest_subdir = max(subdirs, key=lambda d: d.stat().st_mtime)
        log.info(f"Found latest API run directory: {latest_subdir}")
        return latest_subdir
    except Exception as e:
        log.error(f"Error finding latest directory in {api_outputs_dir}: {e}", exc_info=True)
        return None

# --- Main Logic ---
def run_video_prompt_generation(project_path: Path, api_run_path: Path):
    """
    Generates video prompts (WanVideo, LTX) based on approved images.
    Reads approvals and approved images from the API Run Folder.
    Reads original Flux prompts and character metadata from the Project Folder.
    Writes generated video prompts into the API Run Folder structure.
    """
    log.info(f"--- Starting Video Prompt Generation ---")
    log.info(f"Using Project Folder: {project_path}")
    log.info(f"Using API Run Folder: {api_run_path}")

    # --- Define Paths ---
    # Project Folder Paths
    project_metadata_path = project_path / "metadata.json"
    project_characters_base_path = project_path / config.CHARACTERS_FOLDER_NAME

    # API Run Folder Paths
    api_approval_log_path = api_run_path / "approved_selections.json"
    api_approved_images_dir = api_run_path / config.APPROVED_IMAGES_FOLDER_NAME
    api_output_characters_dir = api_run_path / config.API_CHARACTERS_FOLDER_NAME # Where video prompts will be saved

    # --- Validate Paths ---
    if not project_metadata_path.is_file():
        log.error(f"Project metadata not found: {project_metadata_path}. Run main orchestrator first."); return False
    if not project_characters_base_path.is_dir():
        log.error(f"Project characters folder not found: {project_characters_base_path}. Run main orchestrator/prompt generation first."); return False
    if not api_approval_log_path.is_file():
        log.error(f"Approval log not found in API run folder: {api_approval_log_path}. Run approve_images_v2.py first."); return False
    if not api_approved_images_dir.is_dir():
        # This is a warning because maybe no images were approved for *any* actor.
        log.warning(f"Approved images folder not found or not a directory: {api_approved_images_dir}. Proceeding, but no video prompts will be generated.")
        # Allow script to continue and potentially succeed if no actors were approved/processed.
        # If this folder *should* exist, make it an error.

    log.info(f"Project metadata path: {project_metadata_path}")
    log.info(f"Approval log path: {api_approval_log_path}")
    log.info(f"Approved images folder: {api_approved_images_dir}")
    log.info(f"Video prompt output base: {api_output_characters_dir}")

    # --- Load Data ---
    try:
        with open(project_metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
        with open(api_approval_log_path, 'r', encoding='utf-8') as f: approved_selections = json.load(f)

        movie_name = movie_info.get("movie_name", project_path.name.split('_')[0]) # Get from metadata or project folder
        # Extract theme from the *Project Path* as that's the canonical source
        theme = extract_theme_from_project_path(project_path)
        if not theme or theme == "Default Theme":
             # Try extracting from API run path as a fallback
             theme = extract_theme_from_project_path(api_run_path)
             if not theme or theme == "Default Theme":
                  log.error("Could not determine theme from project path or API run path.")
                  return False
             else:
                  log.warning(f"Used theme '{theme}' extracted from API run path as fallback.")
        log.info(f"Loaded metadata and approvals for '{movie_name}' ('{theme}' theme).")

    except Exception as e:
        log.error(f"Error loading metadata ({project_metadata_path}) or approval file ({api_approval_log_path}): {e}"); return False

    # Build Actor -> Character map from project metadata for easy lookup
    actor_char_map = {
        item['actor_name']: item['character_name']
        for item in movie_info.get('main_characters_actors', [])
        if item.get('actor_name') and item.get('character_name')
    }
    if not actor_char_map:
        log.error("Could not build Actor->Character map from project metadata.")
        return False
    log.debug(f"Actor to Character Map: {actor_char_map}")

    # --- Process Approved Selections ---
    video_prompts_generated_count = 0
    actors_processed_count = 0
    actors_failed = []
    total_approved_images = sum(len(paths) for paths in approved_selections.values() if isinstance(paths, list))
    log.info(f"Found {total_approved_images} approved image(s) across {len(approved_selections)} actors in {api_approval_log_path}")

    # Loop through ACTORS (keys) in the approval dictionary
    for actor_name, approved_img_paths_list in approved_selections.items():

        # Check if the actor was skipped or had no images approved
        if not isinstance(approved_img_paths_list, list) or not approved_img_paths_list:
            if approved_img_paths_list == "SKIPPED":
                 log.info(f"Actor '{actor_name}' was explicitly skipped. Skipping video prompt generation.")
            else:
                 log.info(f"No images were approved for actor '{actor_name}'. Skipping video prompt generation.")
            continue # Move to the next actor

        actors_processed_count += 1

        # Find the corresponding Character Name using the map
        character_name = actor_char_map.get(actor_name)
        if not character_name:
            log.warning(f"Could not find character name for actor '{actor_name}' in project metadata map. Skipping.")
            actors_failed.append(actor_name)
            continue

        log.info(f"\nProcessing approved images for: Character '{character_name}' (Actor: '{actor_name}')")
        char_name_sanitized = sanitize_name(character_name)

        # Define paths related to this character
        # Source of original prompts and character metadata is the PROJECT folder
        project_character_dir = project_characters_base_path / char_name_sanitized
        project_char_metadata_path = project_character_dir / "metadata.json"

        # Destination for the generated video prompts is the API RUN folder
        output_character_dir_in_api_run = api_output_characters_dir / char_name_sanitized
        output_character_dir_in_api_run.mkdir(parents=True, exist_ok=True) # Ensure it exists

        # Load character details ONCE per character (from Project Folder)
        character_data = {}
        if project_char_metadata_path.is_file():
            try:
                with open(project_char_metadata_path, 'r', encoding='utf-8') as f: character_data = json.load(f)
                log.debug(f"  Loaded character metadata for {character_name} from project.")
            except Exception as e:
                log.warning(f"  Could not load character metadata for {character_name} from {project_char_metadata_path}: {e}")
        else:
            log.warning(f"  Character metadata not found in project folder for {character_name}: {project_char_metadata_path}")

        # --- Loop through EACH approved image path for this actor ---
        # These paths point to files inside the API Run's 'approved_images' folder
        for approved_img_path_str in approved_img_paths_list:
            try:
                approved_img_path = Path(approved_img_path_str)

                if not approved_img_path.is_file():
                    log.warning(f"  Approved image file path does not exist: {approved_img_path}. Skipping this image.")
                    continue

                # Determine shot type from filename (heuristic based on approval filename structure)
                shot_type = "unknown"
                if "_medium_" in approved_img_path.name.lower(): shot_type = "medium"
                elif "_full_" in approved_img_path.name.lower(): shot_type = "full"
                log.info(f"  Processing Approved Image ({shot_type}): {approved_img_path.name}")

                # Load the ORIGINAL Flux prompt from the PROJECT folder (used for LTX context)
                original_flux_prompt_path = project_character_dir / f"flux_prompt_{shot_type}.txt"
                original_flux_prompt = ""
                if original_flux_prompt_path.is_file():
                    try:
                        with open(original_flux_prompt_path, 'r', encoding='utf-8') as f:
                            original_flux_prompt = f.read().strip()
                        log.debug(f"    Loaded original Flux prompt ({shot_type}) from project.")
                    except Exception as e:
                        log.warning(f"    Could not read original flux prompt {original_flux_prompt_path}: {e}")
                else:
                    log.warning(f"    Original flux prompt ({shot_type}) not found in project: {original_flux_prompt_path}")

                # --- Generate WanVideo Prompts (Simpler action prompt) ---
                # Filename based on the approved image stem (e.g., wan_video_prompt_positive_Amitabh_Bachchan_approved_medium_1.txt)
                wan_pos_filename = f"wan_video_prompt_positive_{approved_img_path.stem}.txt"
                wan_neg_filename = f"wan_video_prompt_negative_{approved_img_path.stem}.txt"
                # Save path is within the API RUN folder's character directory
                wan_pos_save_path = output_character_dir_in_api_run / wan_pos_filename
                wan_neg_save_path = output_character_dir_in_api_run / wan_neg_filename

                # Check if BOTH prompts already exist
                if wan_pos_save_path.exists() and wan_neg_save_path.exists():
                     log.info(f"    WanVideo prompts (pos/neg) already exist for {approved_img_path.name}. Skipping generation.")
                     # Increment count here if skipping means success for this image
                     video_prompts_generated_count += 1 # Count existing as success for Wan part
                else:
                     log.info(f"    Generating WanVideo prompts (Action Focused)...")
                     # Call the LLM function (using v2)
                     wan_pos_prompt, wan_neg_prompt = llm_interactions.generate_wan_video_prompt_from_image(
                         approved_image_path, # Pass the Path object
                         movie_name,
                         theme,
                         character_name,
                         actor_name,
                         character_data, # Pass character details loaded earlier
                         config.OLLAMA_MODEL # Use the configured model
                     )
                     if wan_pos_prompt and wan_neg_prompt:
                          prompts_saved_ok = True
                          try: # Save Positive
                               with open(wan_pos_save_path, 'w', encoding='utf-8') as f: f.write(wan_pos_prompt)
                               log.info(f"      Saved Wan Positive prompt: {wan_pos_save_path.name}")
                          except IOError as e: log.error(f"      Failed save Wan Positive prompt: {e}"); prompts_saved_ok = False; actors_failed.append(actor_name)
                          try: # Save Negative
                               with open(wan_neg_save_path, 'w', encoding='utf-8') as f: f.write(wan_neg_prompt)
                               log.info(f"      Saved Wan Negative prompt: {wan_neg_save_path.name}")
                          except IOError as e: log.error(f"      Failed save Wan Negative prompt: {e}"); prompts_saved_ok = False; actors_failed.append(actor_name)

                          if prompts_saved_ok:
                              video_prompts_generated_count += 1 # Count successful generation for Wan part
                     else:
                          log.error(f"    LLM failed to generate WanVideo prompt pair for {approved_img_path.name}.")
                          actors_failed.append(actor_name) # Mark actor as failed for this image

                # --- Generate LTX Video Prompt (More descriptive) ---
                # Filename based on the approved image stem
                ltx_prompt_filename = f"ltx_video_prompt_{approved_img_path.stem}.txt"
                # Save path is within the API RUN folder's character directory
                ltx_prompt_save_path = output_character_dir_in_api_run / ltx_prompt_filename

                if ltx_prompt_save_path.exists():
                    log.info(f"    LTX video prompt already exists for {approved_img_path.name}. Skipping generation.")
                    # Increment LTX count separately if needed, or assume combined count is fine
                else:
                    log.info(f"    Generating LTX video prompt (Cinematic)...")
                    # Call the LLM function (using v2)
                    ltx_video_prompt = llm_interactions.generate_ltx_prompt_from_image(
                        approved_img_path, # Pass Path object
                        movie_name,
                        theme,
                        character_name,
                        actor_name,
                        character_data,
                        original_flux_prompt, # Pass the original static prompt context
                        config.OLLAMA_MODEL
                    )
                    if ltx_video_prompt:
                        try:
                            with open(ltx_prompt_save_path, 'w', encoding='utf-8') as f: f.write(ltx_video_prompt)
                            log.info(f"      Saved LTX video prompt: {ltx_prompt_save_path.name}")
                            # Increment count here if LTX success is counted separately
                        except IOError as e:
                            log.error(f"      Failed to save LTX video prompt: {e}")
                            actors_failed.append(actor_name) # Mark failure
                    else:
                        log.error(f"    LLM failed to generate LTX video prompt for {approved_img_path.name}.")
                        actors_failed.append(actor_name) # Mark failure

            except Exception as inner_e:
                 log.error(f"  Unexpected error processing image {approved_img_path_str} for {actor_name}: {inner_e}", exc_info=True)
                 actors_failed.append(actor_name) # Mark actor as failed if any image causes exception
        # --- End loop through approved images for one actor ---
    # --- End loop through all actors ---

    log.info("\n--- Finished Video Prompt Generation ---")
    log.info(f"Attempted processing for {actors_processed_count} actors with approved images.")
    # Note: video_prompts_generated_count might double-count if Wan succeeded but LTX didn't, adjust logic if needed.
    # Currently counts successful Wan prompt pair generations.
    log.info(f"Generated/Found WanVideo prompt pairs for {video_prompts_generated_count} approved images.")
    # Add similar count for LTX if tracked separately.

    unique_failed = sorted(list(set(actors_failed)))
    if unique_failed:
        log.warning(f"Video prompt generation encountered errors for actors: {', '.join(unique_failed)}")
        # Decide if this constitutes overall failure. Let's return True if *any* prompts were generated.
        return True # Partial success
    elif actors_processed_count == 0 and total_approved_images > 0:
        log.error("Processed 0 actors, but approval log showed approved images. Check Actor->Character mapping.")
        return False # Likely a mapping error
    elif actors_processed_count == 0 and total_approved_images == 0:
         log.info("No actors had approved images to process. Video prompt generation step skipped.")
         return True # Successful because there was nothing to do
    else:
        log.info("Video prompt generation completed successfully for all processed actors/images.")
        return True


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate video prompts (Wan/LTX) for approved images (V2).")
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder (in Movie_Projects). If not provided, uses the latest.")
    parser.add_argument("-r", "--run_folder_path", help="Path to the specific API run folder (in API_OUTPUTS). If not provided, uses the latest.")
    args = parser.parse_args()

    load_dotenv(dotenv_path=config.DOTENV_PATH)

    # --- Determine Project Path ---
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        if not project_to_process.is_dir() or not project_to_process.parent.samefile(config.PROJECTS_BASE_DIR):
             log.error(f"Provided project path is not a valid directory within {config.PROJECTS_BASE_DIR}: {project_to_process}"); exit(1)
    else:
        log.info(f"Finding latest project directory in {config.PROJECTS_BASE_DIR}...")
        project_to_process = find_latest_project_dir_in_projects(config.PROJECTS_BASE_DIR)
        if not project_to_process:
             log.error(f"No project found in {config.PROJECTS_BASE_DIR}. Run main orchestrator first."); exit(1)
    log.info(f"Using project folder: {project_to_process}")

    # --- Determine API Run Folder Path ---
    api_run_folder_to_process = None
    api_outputs_base = config.COMFYUI_OUTPUT_DIR # Base H:/dancers_content
    if args.run_folder_path:
        provided_path = Path(args.run_folder_path)
        if provided_path.is_dir() and str(provided_path.resolve()).startswith(str(api_outputs_base.resolve() / config.API_OUTPUTS_SUBDIR)):
            api_run_folder_to_process = provided_path.resolve()
        else:
             log.error(f"Provided API run path '{args.run_folder_path}' is not valid or not within '{api_outputs_base / config.API_OUTPUTS_SUBDIR}'. Exiting."); exit(1)
    else:
        log.info(f"Finding latest API run folder in {api_outputs_base / config.API_OUTPUTS_SUBDIR}...")
        api_run_folder_to_process = find_latest_api_output_run_dir(api_outputs_base)
        if not api_run_folder_to_process:
             log.error(f"No API run folder found. Run generate_images and approve_images first. Exiting."); exit(1)
    log.info(f"Using API run folder: {api_run_folder_to_process}")


    # --- Check LLM Interactions ---
    if not hasattr(llm_interactions, 'generate_wan_video_prompt_from_image') or \
       not hasattr(llm_interactions, 'generate_ltx_prompt_from_image'):
        log.critical("Essential video prompt functions missing from llm_interactions_v2.py!")
        exit(1)
    # ---

    success = run_video_prompt_generation(project_to_process, api_run_folder_to_process)

    if success:
        log.info("generate_video_prompts_v2.py finished successfully.")
        exit(0)
    else:
        log.error("generate_video_prompts_v2.py finished with errors.")
        exit(1)