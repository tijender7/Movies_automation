# apply_face_swap.py
import logging
import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import argparse
import copy
from datetime import datetime
import sys
import re
import time

# --- Import project config and helpers ---
try:
    # Ensure these point to your V2 config/interactions files if using those names
    import config_v2 as config
    import comfyui_interactions as comfyui_interactions # Assumes V2 helpers are in this file
    sys.path.append(str(Path(__file__).parent))
    # Import helper from generate_prompts for finding latest project dir
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir
except ImportError as e: print(f"ERROR: Import failed: {e}"); exit(1)
except FileNotFoundError as e: print(f"ERROR: Cannot find helpers script: {e}"); exit(1)

# --- Constants from Config ---
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "face_swap.log") # Specific log
PROJECTS_BASE_DIR = config.PROJECTS_BASE_DIR
SOURCE_ACTORS_FOLDER_NAME = config.SOURCE_ACTORS_FOLDER_NAME
COMFYUI_INPUT_DIR = Path(config.COMFYUI_INPUT_DIR) if config.COMFYUI_INPUT_DIR else None
COMFYUI_OUTPUT_DIR = Path(config.COMFYUI_OUTPUT_DIR) if config.COMFYUI_OUTPUT_DIR else None
API_OUTPUTS_SUBDIR = config.API_OUTPUTS_SUBDIR
REACTOR_WORKFLOW_TEMPLATE = Path(config.REACTOR_WORKFLOW_TEMPLATE) if config.REACTOR_WORKFLOW_TEMPLATE else None
FINAL_VIDEO_SWAPPED_FOLDER_NAME = config.FINAL_VIDEO_SWAPPED_FOLDER_NAME # New config var

# --- Node Titles from Config ---
REACTOR_SOURCE_FACE_NODE_TITLE = config.REACTOR_SOURCE_FACE_NODE_TITLE
REACTOR_TARGET_VIDEO_NODE_TITLE = config.REACTOR_TARGET_VIDEO_NODE_TITLE
REACTOR_OUTPUT_PREFIX_NODE_TITLE = config.REACTOR_OUTPUT_PREFIX_NODE_TITLE

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-[%(filename)s:%(lineno)d]-%(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING); logging.getLogger('werkzeug').setLevel(logging.WARNING); logging.getLogger('tensorflow').setLevel(logging.ERROR)
except Exception as e: print(f"ERROR logging setup: {e}"); logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-%(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Functions ---
def log_step(m, l="info", i=False, exc_info=None): prefix = f"--- " if i else ""; suffix = f" ---" if i else ""; log.log(getattr(logging, l.upper(), logging.INFO), f"{prefix}[{l[0].upper()}] {m}{suffix}", exc_info=exc_info)
def sanitize_name(n): return re.sub(r'[_]+', '_', "".join(c for c in re.sub(r'[<>:"/\\|?*\']', '_', str(n)) if c.isalnum() or c in (' ','_','-')).strip().replace(' ','_')).strip('_-')[:60] or "invalid"
def copy_files_to_comfyui_input(file_paths: list[Path]):
    """Copies a list of Path objects to the configured ComfyUI input directory."""
    if not COMFYUI_INPUT_DIR or not COMFYUI_INPUT_DIR.is_dir():
        log_step(f"ComfyUI Input Dir invalid: {COMFYUI_INPUT_DIR}", l="error"); return False, []
    copied_filenames = []
    all_success = True
    for file_path in file_paths:
        if not file_path.is_file():
            log_step(f"Source file missing, cannot copy: {file_path}", l="warning"); all_success = False; continue
        try:
            dest = COMFYUI_INPUT_DIR / file_path.name; shutil.copy2(file_path, dest)
            log_step(f" Copied '{file_path.name}' to ComfyUI input.", l="debug")
            copied_filenames.append(file_path.name)
        except Exception as e:
            log_step(f"Error copying '{file_path.name}' to ComfyUI input: {e}", l="error"); all_success = False
    return all_success, copied_filenames

def find_latest_video_run_dir(api_outputs_base: Path) -> Path | None:
    """Finds the latest timestamped *VIDEO* run folder within API_OUTPUTS."""
    api_outputs_dir = api_outputs_base / config.API_OUTPUTS_SUBDIR
    if not api_outputs_dir.is_dir(): return None
    # Look for folders ending with _VIDEO_<timestamp> pattern
    video_run_folders = [d for d in api_outputs_dir.iterdir() if d.is_dir() and "_VIDEO_" in d.name]
    if not video_run_folders: return None
    try:
        # Sort by name which includes the timestamp at the end
        return max(video_run_folders, key=os.path.getmtime) # Sort by modification time is usually reliable
    except Exception as e:
        log_step(f"Error finding latest video run dir: {e}", l="warning")
        return None

# --- Main Logic ---
def run_face_swapping(project_path: Path, video_run_output_path: Path):
    log_step(f"--- Step 1: Setup & Validation ---", l="info", i=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", l="error"); return False
    if not video_run_output_path or not video_run_output_path.is_dir(): log_step(f"Invalid video run output path: {video_run_output_path}", l="error"); return False

    project_source_actors_dir = project_path / SOURCE_ACTORS_FOLDER_NAME
    if not project_source_actors_dir.is_dir(): log_step(f"Project source actors folder missing: {project_source_actors_dir}", l="error"); return False

    workflow_template = comfyui_interactions.load_workflow_template(REACTOR_WORKFLOW_TEMPLATE)
    if not workflow_template: log_step(f"Failed load REACTOR workflow: {REACTOR_WORKFLOW_TEMPLATE}", l="error"); return False
    log_step(f"Loaded workflow: {REACTOR_WORKFLOW_TEMPLATE.name}")

    log_step(f"--- Step 2: Finding Videos and Applying Face Swap ---", l="info", i=True)
    success_count = 0
    fail_count = 0
    processed_count = 0
    at_least_one_swap_started = False

    # Find character/actor subfolders in the video run output path
    char_actor_folders = [d for d in video_run_output_path.iterdir() if d.is_dir() and "__" in d.name]
    if not char_actor_folders:
        log_step(f"No Character__Actor folders found in {video_run_output_path}", l="warning")
        return True # No work to do is considered success

    for char_actor_folder in char_actor_folders:
        try:
            parts = char_actor_folder.name.split("__")
            if len(parts) != 2:
                log_step(f"Skipping folder with unexpected name format: {char_actor_folder.name}", l="warning")
                continue
            char_name, actor_name = parts[0], parts[1]
            char_name_san, actor_name_san = sanitize_name(char_name), sanitize_name(actor_name)
            log_step(f"--- Processing Character: {char_name}, Actor: {actor_name} ---", l="info", i=True)

            video_input_dir = char_actor_folder / "final_video_output" # Where generated videos are
            video_output_dir = char_actor_folder / FINAL_VIDEO_SWAPPED_FOLDER_NAME # Where swapped videos will go

            if not video_input_dir.is_dir():
                log_step(f" Input video folder not found: {video_input_dir}. Skipping.", l="warning")
                continue

            videos_to_process = sorted(list(video_input_dir.glob('*.mp4')))
            if not videos_to_process:
                log_step(f" No .mp4 videos found in {video_input_dir}. Skipping.", l="debug")
                continue

            # --- Find the single source actor face ---
            source_actor_face_path = None
            # Use sanitized actor name for matching
            # Search for files starting with the actor name, allowing for suffixes like _1, _2 etc.
            search_pattern = f"{actor_name_san}*" # Look for files STARTING with the name
            possible_faces = list(project_source_actors_dir.glob(search_pattern))

            # Filter for actual image files (basic check)
            image_extensions = {".jpg", ".jpeg", ".png", ".webp"}
            found_faces = [f for f in possible_faces if f.is_file() and f.suffix.lower() in image_extensions]

            if found_faces:
                # If multiple faces are found starting with the name (e.g., _1.jpg, _2.jpg),
                # just take the first one found for simplicity.
                # You could add sorting logic here if needed (e.g., sort by name, take lowest number).
                source_actor_face_path = sorted(found_faces)[0] # Take the first one alphabetically
                log_step(f" Found source face: {source_actor_face_path.name}", l="info")
            else:
                # If still not found after glob search
                log_step(f" Source face image for actor '{actor_name}' (searched as '{actor_name_san}*') not found in {project_source_actors_dir}. Cannot face swap for this character.", l="error")
                fail_count += len(videos_to_process) # Count all potential videos as failed for this actor
                continue # Skip this whole character/actor folder

            # --- Process each video for this character ---
            for video_path in videos_to_process:
                processed_count += 1
                log_step(f" Processing video {processed_count}: {video_path.name}", l="info")

                try:
                    at_least_one_swap_started = True
                    video_stem = video_path.stem

                    # Define swapped output filename and path
                    swapped_filename_stem = f"swapped_{video_stem}"
                    # Ensure output directory exists
                    video_output_dir.mkdir(parents=True, exist_ok=True)
                    # Full path prefix for ComfyUI node (Comfy adds counter+extension)
                    output_path_prefix = video_output_dir / swapped_filename_stem

                    # Check if already swapped
                    # Note: Checking based on prefix before Comfy adds counter
                    existing_swapped = list(video_output_dir.glob(f"{swapped_filename_stem}*.*"))
                    if existing_swapped:
                        log_step(f" Swapped video already exists: {existing_swapped[0].name}. Skipping.", l="warning")
                        # Optionally count as success if needed
                        success_count +=1 # Count existing as success
                        continue

                    # Copy inputs to ComfyUI
                    copy_ok, comfy_filenames = copy_files_to_comfyui_input([video_path, source_actor_face_path])
                    if not copy_ok or len(comfy_filenames) != 2:
                        raise IOError("Failed to copy required files (video/face) to ComfyUI input.")
                    # Get filenames as used by ComfyUI
                    comfy_video_filename = video_path.name
                    comfy_face_filename = source_actor_face_path.name
                    if comfy_video_filename not in comfy_filenames or comfy_face_filename not in comfy_filenames:
                         log_step(" Mismatch between expected filenames and copied filenames. Using actual copied names.", l="warning")
                         # Find the names from the list if needed, though usually they match Path.name
                         comfy_video_filename = next((f for f in comfy_filenames if f == video_path.name), None)
                         comfy_face_filename = next((f for f in comfy_filenames if f == source_actor_face_path.name), None)
                         if not comfy_face_filename or not comfy_video_filename:
                             raise ValueError("Could not determine correct filenames copied to ComfyUI.")


                    # Modify workflow
                    current_workflow = copy.deepcopy(workflow_template)
                    if not current_workflow: raise ValueError("Workflow copy failed.")

                    inputs_to_set = {
                        REACTOR_SOURCE_FACE_NODE_TITLE: {"image": comfy_face_filename},
                        REACTOR_TARGET_VIDEO_NODE_TITLE: {"video": comfy_video_filename}, # Assuming VHS_LoadVideo takes 'video' key
                        REACTOR_OUTPUT_PREFIX_NODE_TITLE: {
                            "filename_prefix": output_path_prefix.as_posix(),
                            "format": "video/h264-mp4" # <<< IMPORTANT: Set to video output format
                        }
                    }
                    log_step(f" Modifying REACTOR workflow inputs...", l="info")
                    log_step(f" Inputs to set: {json.dumps(inputs_to_set, indent=2)}", l="debug")
                    if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                         raise ValueError("Failed to modify reactor workflow inputs.")

                    # Run workflow
                    log_step(f" Queueing REACTOR workflow for {video_path.name}...")
                    # Face swap can be slow, use a long timeout
                    comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=3600) # 1 hour timeout

                    if not comfyui_result_history:
                        raise TimeoutError("ComfyUI REACTOR workflow timeout/fail.")

                    # Verify output (simple check if file exists)
                    # ComfyUI adds counter, so check with glob
                    expected_output_pattern = video_output_dir / f"{swapped_filename_stem}_*"
                    time.sleep(2) # Give filesystem a moment
                    final_files = list(video_output_dir.glob(f"{swapped_filename_stem}*.mp4")) # Check for mp4 specifically

                    if final_files:
                        log_step(f" ComfyUI REACTOR workflow COMPLETED. Output verified: {final_files[0].name}", l="success")
                        success_count += 1
                    else:
                        log_step(f" ComfyUI REACTOR workflow finished, but expected output file matching '{expected_output_pattern}.mp4' not found!", l="error")
                        # Check history for clues if needed
                        output_details = comfyui_interactions.get_output_details_from_history(comfyui_result_history, REACTOR_OUTPUT_PREFIX_NODE_TITLE)
                        log_step(f" Output node details from history: {output_details}", l="debug")
                        fail_count += 1

                except Exception as e_video:
                    log_step(f" Error during face swap for {video_path.name}: {e_video}", l="error", exc_info=True)
                    fail_count += 1

        except Exception as e_char:
            log_step(f" Unexpected error processing folder {char_actor_folder.name}: {e_char}", l="error", exc_info=True)
            # Can't easily count fails here, maybe mark character as failed

    log_step("\n--- Finished Face Swap Phase ---", l="info", i=True)
    if not at_least_one_swap_started: log_step("No ComfyUI face swap runs attempted.", l="warning"); return True # No work = success
    log_step(f"Attempted face swap for {processed_count} videos.")
    log_step(f"Success: {success_count}")
    log_step(f"Failed: {fail_count}")
    log_step(f"Swapped outputs should be in '{FINAL_VIDEO_SWAPPED_FOLDER_NAME}' subfolders within {video_run_output_path}")
    return fail_count == 0


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply Face Swap to generated videos using ComfyUI Reactor.")
    parser.add_argument("-p", "--project_path", help="Path to the movie project folder (containing source_actors). Omit for latest.")
    parser.add_argument("-v", "--video_run_path", help="Path to the specific VIDEO run output folder (containing Character__Actor/final_video_output). Omit for latest VIDEO run.")
    args = parser.parse_args()
    log_step("===== Apply Face Swap Script Started =====", l="info", i=True)

    try: load_dotenv(dotenv_path=config.DOTENV_PATH)
    except Exception as e: log_step(f"Note: .env load error: {e}", l="debug")

    # --- Validate Core Paths ---
    if not all([COMFYUI_INPUT_DIR, COMFYUI_OUTPUT_DIR, REACTOR_WORKFLOW_TEMPLATE]):
        log_step("CRITICAL Error: ComfyUI paths or REACTOR template missing in config.", l="error", i=True); exit(1)
    if not COMFYUI_INPUT_DIR.is_dir(): log_step(f"Error: COMFYUI_INPUT_DIR invalid: {COMFYUI_INPUT_DIR}", l="error"); exit(1)
    if not COMFYUI_OUTPUT_DIR.is_dir(): log_step(f"Error: COMFYUI_OUTPUT_DIR invalid: {COMFYUI_OUTPUT_DIR}", l="error"); exit(1)
    if not REACTOR_WORKFLOW_TEMPLATE.is_file(): log_step(f"Error: REACTOR_WORKFLOW_TEMPLATE not found: {REACTOR_WORKFLOW_TEMPLATE}", l="error"); exit(1)
    required_titles = [REACTOR_SOURCE_FACE_NODE_TITLE, REACTOR_TARGET_VIDEO_NODE_TITLE, REACTOR_OUTPUT_PREFIX_NODE_TITLE]
    if not all(required_titles): log_step(f"CRITICAL Error: Required REACTOR node titles missing in config: Need {required_titles}", l="error", i=True); exit(1)
    log_step("Config validation passed.", l="debug")

    # --- Determine Project Path ---
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path).resolve()
        if not project_to_process.is_dir() or not project_to_process.parent.samefile(PROJECTS_BASE_DIR):
             log_step(f"Provided project path is not valid: {project_to_process}", l="error"); exit(1)
    else:
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", l="error", i=True); sys.exit(1)
    log_step(f"Using project: {project_to_process.name}");

    # --- Determine Video Run Path ---
    video_run_to_process = None
    api_outputs_base = COMFYUI_OUTPUT_DIR
    if args.video_run_path:
        video_run_to_process = Path(args.video_run_path).resolve()
        # Basic validation: check if it's inside the expected base API output area
        if not video_run_to_process.is_dir() or not str(video_run_to_process).startswith(str(api_outputs_base / API_OUTPUTS_SUBDIR)):
            log_step(f"Provided video run path is not valid or not in API outputs: {video_run_to_process}", l="error"); exit(1)
    else:
        log_step(f"Finding latest VIDEO run folder in {api_outputs_base / API_OUTPUTS_SUBDIR}...");
        video_run_to_process = find_latest_video_run_dir(api_outputs_base) # Use the specific finder
        if not video_run_to_process: log_step(f"No VIDEO run folder found.", l="error", i=True); sys.exit(1)
    log_step(f"Using video run output folder: {video_run_to_process.name}");

    # --- Run Main Logic ---
    success = run_face_swapping(project_to_process, video_run_to_process)

    if success: log_step("Script finished successfully.", l="success", i=True); sys.exit(0)
    else: log_step("Script finished with errors.", l="error", i=True); sys.exit(1)