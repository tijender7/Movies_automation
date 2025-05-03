# approve_images.py
import logging
import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import argparse
import threading
import time

# Import project modules
try:
    import config
    import web_approver # Imports the Flask app and interaction functions
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules: {e}")
    print("Make sure config.py and web_approver.py exist and are correct.")
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

# --- Helper Function to Find Latest API_OUTPUTS Folder ---
def find_latest_api_output_dir() -> Path:
    """Find the most recently modified subdirectory in COMFYUI_OUTPUT_DIR/API_OUTPUTS."""
    api_outputs_dir = config.COMFYUI_OUTPUT_DIR / "API_OUTPUTS"
    if not api_outputs_dir.exists() or not api_outputs_dir.is_dir():
        log.error(f"API_OUTPUTS directory does not exist: {api_outputs_dir}")
        return None
    subdirs = [d for d in api_outputs_dir.iterdir() if d.is_dir()]
    if not subdirs:
        log.error(f"No subdirectories found in {api_outputs_dir}")
        return None
    latest_subdir = max(subdirs, key=lambda d: d.stat().st_mtime)
    return latest_subdir

# --- Helper Function to Find Latest ComfyUI Run Folder ---
def find_latest_comfyui_run_dir(comfy_output_base: Path, project_name_prefix: str):
    """Finds the latest run directory within ComfyUI output for a specific project prefix."""
    try:
        api_outputs_dir = comfy_output_base / "API_OUTPUTS" # Look inside API_OUTPUTS
        if not api_outputs_dir.is_dir():
            log.error(f"ComfyUI API_OUTPUTS directory not found: {api_outputs_dir}")
            return None
        # Find directories starting with the project name prefix
        project_run_dirs = [d for d in api_outputs_dir.iterdir() if d.is_dir() and d.name.startswith(project_name_prefix)]
        if not project_run_dirs:
            log.error(f"No run directories found for project prefix '{project_name_prefix}' in {api_outputs_dir}")
            return None
        latest_run_dir = max(project_run_dirs, key=lambda d: d.stat().st_mtime)
        return latest_run_dir
    except Exception as e:
        log.error(f"Error finding latest ComfyUI run directory for {project_name_prefix}: {e}", exc_info=True)
        return None

# --- Function to Scan for Candidate Swapped Images ---
def find_candidate_swapped_images(comfy_run_dir: Path, movie_name: str):
    """
    Scans the ComfyUI run directory for character folders and their swapped images.
    Returns a dict: {actor_name: {character_name: str, candidates: [dicts], movie_name: str}}
    Candidate dict: {filename, relative_path, full_path}
    """
    all_candidates = {}
    if not comfy_run_dir or not comfy_run_dir.is_dir():
        log.error(f"Invalid ComfyUI run directory provided: {comfy_run_dir}")
        return all_candidates

    log.info(f"Scanning ComfyUI run directory for swapped images: {comfy_run_dir}")
    for char_actor_dir in comfy_run_dir.iterdir(): # e.g., Jai__Amitabh_Bachchan
        if not char_actor_dir.is_dir(): continue

        # Try parsing actor and character name (assuming format CharName__ActorName)
        parts = char_actor_dir.name.split('__')
        if len(parts) == 2:
             char_name_approx = parts[0].replace('_', ' ')
             actor_name = parts[1].replace('_', ' ')
        else:
             # Fallback if parsing fails
             actor_name = char_actor_dir.name
             char_name_approx = actor_name
             log.warning(f"Could not parse char/actor name from folder '{char_actor_dir.name}', using folder name as actor.")

        swapped_images_dir = char_actor_dir / "swapped_images" # Look in the correct subfolder
        actor_data = {"character_name": char_name_approx, "candidates": [], "movie_name": movie_name}

        if swapped_images_dir.exists() and swapped_images_dir.is_dir():
            log.debug(f"Scanning swapped images in: {swapped_images_dir}")
            for img_file in swapped_images_dir.iterdir():
                if img_file.is_file() and img_file.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                    try:
                        # Path relative to COMFYUI_OUTPUT_DIR for the web route
                        # e.g., API_OUTPUTS/Sholay...Run/Char__Actor/swapped_images/swapped_medium_001.png
                        relative_path_for_web = img_file.relative_to(config.COMFYUI_OUTPUT_DIR.resolve()).as_posix() # Relative to the absolute base

                        actor_data["candidates"].append({
                            "filename": img_file.name,
                            "relative_path": relative_path_for_web, # Pass this to template
                            "full_path": str(img_file.resolve())
                        })
                    except ValueError as e:
                         log.error(f"PATH ERROR: Cannot make {img_file} relative to {config.COMFYUI_OUTPUT_DIR}. Check config. Error: {e}")
                    except Exception as e_gen:
                         log.warning(f"Error processing image file {img_file}: {e_gen}")

            if actor_data["candidates"]:
                 log.info(f"Found {len(actor_data['candidates'])} candidate swapped images for {actor_name}")
            else:
                 log.warning(f"No swapped images found in {swapped_images_dir}")
        else:
             log.warning(f"Swapped images directory not found: {swapped_images_dir}")

        all_candidates[actor_name] = actor_data # Use Actor Name as the key

    return all_candidates

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Approve generated character images via web UI.")
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder in Movie_Projects. If not provided, uses the latest.")
    args = parser.parse_args()

    # --- Determine Target Project Path (in Movie_Projects) ---
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        if not project_to_process.is_dir():
             log.error(f"Provided project path does not exist: {project_to_process}"); exit(1)
    else:
        log.info("Finding latest API_OUTPUTS folder...")
        project_to_process = find_latest_api_output_dir()
        if not project_to_process: log.error(f"No project found in {config.COMFYUI_OUTPUT_DIR / 'API_OUTPUTS'}"); exit(1)
    log.info(f"Target project folder: {project_to_process}")

    # --- Find Corresponding Run Folder in ComfyUI Output ---
    # We need the prefix (MovieName_Theme) to find the right run folder
    project_name_theme_prefix = "_".join(project_to_process.name.split('_')[:-1]) # Extract "Sholay_cyberphunk"
    log.info(f"Searching for latest ComfyUI run folder starting with: '{project_name_theme_prefix}' in {config.COMFYUI_OUTPUT_DIR}")
    latest_comfy_run_dir = find_latest_comfyui_run_dir(config.COMFYUI_OUTPUT_DIR, project_name_theme_prefix)

    if not latest_comfy_run_dir:
        log.error(f"Could not find a corresponding run folder in ComfyUI output for project {project_to_process.name}. Please ensure generate_images.py ran successfully.")
        exit(1)
    log.info(f"Found latest ComfyUI run directory: {latest_comfy_run_dir}")

    # --- Scan for Candidate Images in ComfyUI Output ---
    movie_name_from_folder = project_to_process.name.split('_')[0] # Get movie name for display
    all_candidates = find_candidate_swapped_images(latest_comfy_run_dir, movie_name_from_folder)
    if not all_candidates:
        log.error("No candidate swapped images found in the ComfyUI run directory. Cannot start approval.")
        exit(1)

    # --- Start Flask Approver App Thread ---
    flask_thread = threading.Thread(target=web_approver.run_flask_approver_app, daemon=True)
    flask_thread.start()
    log.info(f"Flask server starting in background thread on port {config.FLASK_PORT}...")
    time.sleep(2)

    # --- Present Approval Page and Wait ---
    approvals = web_approver.present_image_approval_page(all_candidates) # Pass candidates found in Comfy output

    # --- Process Approvals ---
    if approvals is None:
        log.error("No approvals received (timeout or error). Exiting.")
        exit(1)

    log.info("\n--- Processing Approvals ---")
    log.debug(f"Raw approvals received: {approvals}")

    # --- CORRECT DESTINATION ---
    project_approved_dir = project_to_process / config.APPROVED_IMAGES_FOLDER_NAME # Use project_to_process path
    # --- END CORRECTION ---
    project_approved_dir.mkdir(parents=True, exist_ok=True)
    approved_files_map = {} # Track which files were copied TO THE PROJECT

    for actor_name, selection_list in approvals.items():
        if isinstance(selection_list, str) and selection_list == "SKIP":
            log.info(f"User skipped actor: {actor_name}")
            approved_files_map[actor_name] = "SKIPPED"
            continue
        elif not isinstance(selection_list, list):
            # Handle cases where maybe only one item was selected and not returned as list
            if selection_list and selection_list != "SKIP":
                 selection_list = [selection_list]
            else:
                 log.info(f"No images approved for actor: {actor_name}")
                 approved_files_map[actor_name] = []
                 continue # Go to next actor

        approved_paths_for_actor = []
        log.info(f"Processing {len(selection_list)} approved image(s) for {actor_name}...")
        for idx, img_path_str in enumerate(selection_list):
            try:
                img_path_comfy = Path(img_path_str) # Path in ComfyUI Output
                if not img_path_comfy.is_file():
                    log.warning(f"Approved image not found at source path: {img_path_comfy}. Skipping.")
                    continue

                # Define final filename in the project's approved folder
                actor_name_sanitized = "".join(c for c in actor_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
                shot_type = "unknown"
                if "_medium_" in img_path_comfy.name: shot_type = "medium"
                elif "_full_" in img_path_comfy.name: shot_type = "full"
                dest_filename = f"{actor_name_sanitized}_{config.APPROVED_IMAGE_PREFIX}{shot_type}_{idx+1}{img_path_comfy.suffix}"

                # --- Use CORRECT Destination Path ---
                dest_path = project_approved_dir / dest_filename
                # --- END CORRECTION ---

                shutil.copy2(img_path_comfy, dest_path)
                log.info(f"  Copied approved image {idx+1} for {actor_name}: {img_path_comfy.name} -> {dest_path.name} (in project folder)")
                approved_paths_for_actor.append(str(dest_path)) # Store the path IN THE PROJECT

            except Exception as e:
                log.error(f"Error copying approved image {img_path_str} for {actor_name}: {e}", exc_info=True)

        approved_files_map[actor_name] = approved_paths_for_actor

    # --- Save approvals JSON (in project folder) ---
    approval_log_path = project_to_process / "approved_selections.json" # Save in project
    try:
        with open(approval_log_path, "w", encoding="utf-8") as f:
            json.dump(approved_files_map, f, indent=2)
        log.info(f"Saved approval results log to {approval_log_path}")
    except IOError as e:
        log.error(f"Failed to save approval log: {e}")

    print("\n" + "="*50)
    log.info("Approval process complete.")
    log.info(f"Approved images copied to: {project_approved_dir}")
    print("You may now proceed to the next step (e.g., video prompt generation).")
    print(f"Stop the Flask server (if needed) by pressing Ctrl+C in this window.")
    print("="*50)
    exit(0)