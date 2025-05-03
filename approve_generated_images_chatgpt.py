# approve_generated_images.py
# Allows user selection of generated images via a web UI.

import logging
import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import argparse
from datetime import datetime
import sys
import re
import threading
import glob # For finding source images
import time


# --- Web Server Imports ---
try:
    from flask import Flask, request, render_template_string, send_from_directory, abort
except ImportError as e:
    print(f"ERROR: Missing Flask library. Please install it: pip install Flask")
    sys.exit(1)

# --- Import project config ---
try:
    # Assuming config_v2 contains required paths
    import config_v2 as config
except ImportError:
    print(f"ERROR: Failed to import config_v2.py. Ensure it exists.")
    exit(1)

# --- Constants ---
# Load from config or use defaults
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "approval_log.log") # Separate log file
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects"))
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME', "characters") # Needed? Maybe not directly.
# --- Paths related to ComfyUI Output ---
COMFYUI_OUTPUT_DIR = getattr(config, 'COMFYUI_OUTPUT_DIR', None) # MUST be configured
API_OUTPUTS_SUBDIR = getattr(config, 'API_OUTPUTS_SUBDIR', "API_OUTPUTS") # Subdir within COMFYUI_OUTPUT_DIR
SWAPPED_IMAGES_SUBFOLDER = getattr(config, 'COMFYUI_SWAPPED_IMAGES_SUBFOLDER', 'swapped_images') # Where generated images are
# --- Paths related to Project Output ---
APPROVED_IMAGES_FOLDER_NAME = getattr(config, 'APPROVED_IMAGES_FOLDER_NAME', "Approved_images_for_videos") # Target folder in Project dir

# --- Web UI Settings ---
FLASK_HOST = "127.0.0.1"
FLASK_PORT = getattr(config, 'FLASK_PORT', 5002) # Use port from config if defined, else 5002
WEB_APPROVER_TIMEOUT = getattr(config, 'WEB_APPROVER_TIMEOUT', 1800) # Timeout from config or 30 mins

# --- Global variable for Flask communication ---
web_approval_results = None
web_approval_event = threading.Event()

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
    logging.getLogger('werkzeug').setLevel(logging.WARNING) # Silence Flask dev server logs
except Exception as e:
    print(f"ERROR setting up logging: {e}")
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

# --- Helper Function: Sanitize Name (Keep consistent if used in folder names) ---
def sanitize_name(name):
    if not isinstance(name, str): name = str(name)
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name); sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized); sanitized = re.sub(r'[-]+', '-', sanitized); sanitized = sanitized.strip('_-'); max_len = 60
    if len(sanitized) > max_len: sanitized = sanitized[:max_len].strip('_-')
    return sanitized if sanitized else "invalid_name"

# --- Project Path Helpers ---
def find_latest_project_dir(base_dir):
    log_step(f"Searching for latest project in: {base_dir}")
    try:
        base = Path(base_dir);
        if not base.is_dir(): log_step(f"Base project dir not found: {base_dir}", level="error"); return None
        subdirs = [d for d in base.iterdir() if d.is_dir()]
        if not subdirs: log_step(f"No project subdirs found in: {base_dir}", level="error"); return None
        subdirs_sorted = sorted(subdirs, key=lambda d: d.stat().st_mtime, reverse=True)
        latest_dir = subdirs_sorted[0]; log_step(f"Found latest project: {latest_dir.name}", level="success"); return latest_dir
    except Exception as e: log_step(f"Error finding latest project: {e}", level="error"); return None

# --- ComfyUI Output Path Helper ---
def find_latest_api_run_dir(base_api_output_path, project_name_base):
    """Finds the latest ComfyUI API output run folder matching the project base name."""
    log_step(f"Searching for latest API run folder starting with '{project_name_base}' in: {base_api_output_path}")
    try:
        base = Path(base_api_output_path)
        if not base.is_dir(): log_step(f"Base API output dir not found: {base_api_output_path}", level="error"); return None
        # Find directories starting with the project name base + underscore
        matching_dirs = [d for d in base.iterdir() if d.is_dir() and d.name.startswith(project_name_base + '_')]
        if not matching_dirs: log_step(f"No matching API run folders found for '{project_name_base}'", level="error"); return None
        # Sort by modification time (simplest) or parse timestamp from name if needed
        matching_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        latest_run_dir = matching_dirs[0]
        log_step(f"Found latest API run folder: {latest_run_dir.name}", level="success")
        return latest_run_dir
    except Exception as e: log_step(f"Error finding latest API run folder: {e}", level="error"); return None

# --- Flask App ---
APPROVAL_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Generated Image Approval</title>
    <style>
        body { font-family: sans-serif; margin: 20px; background-color: #f8f9fa; }
        h1 { text-align: center; color: #343a40; margin-bottom: 30px; }
        .character-group { background-color: #fff; border: 1px solid #dee2e6; margin-bottom: 25px; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
        .character-header { font-size: 1.4em; font-weight: bold; color: #495057; border-bottom: 1px solid #e9ecef; padding-bottom: 10px; margin-bottom: 20px; }
        .image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 20px; margin-bottom: 15px; }
        .image-item { text-align: center; border: 1px solid #ced4da; padding: 10px; border-radius: 5px; background-color:#fdfdfd; }
        .image-item img { max-width: 100%; height: 200px; object-fit: contain; border: 1px solid #e0e0e0; margin-bottom: 10px; cursor: pointer; }
        .image-item input[type="checkbox"] { margin-right: 8px; transform: scale(1.2); cursor: pointer; }
        .image-item label { display: flex; align-items: center; justify-content: center; flex-direction: column; cursor: pointer; }
        .filename { font-size: 0.8em; color: #6c757d; word-break: break-all; margin-top: 5px; }
        .submit-button { display: block; width: 250px; margin: 40px auto 20px auto; padding: 12px 25px; background-color: #198754; color: white; border: none; border-radius: 5px; font-size: 1.1em; cursor: pointer; transition: background-color 0.2s; }
        .submit-button:hover { background-color: #157347; }
        .no-images { color: #6c757d; font-style: italic; }
        /* Style checked items */
        .image-item input[type="checkbox"]:checked + label img { border: 3px solid #0d6efd; box-shadow: 0 0 8px rgba(13,110,253,0.4); }
    </style>
</head>
<body>
    <h1>Generated Image Approval</h1>
    <h2>Project: {{ project_name }}</h2>
    <form action="/submit_approval" method="post">
        {% for char_actor_key, data in candidates_data.items() %}
        <div class="character-group">
            <div class="character-header">
                 {{ data.character_name }} ({{ data.actor_name }})
            </div>
            {% if data.candidates %}
                <div class="image-grid">
                    {% for image_info in data.candidates %}
                    <div class="image-item">
                         <label for="img_{{ char_actor_key }}_{{ loop.index }}">
                            <input type="checkbox"
                                   id="img_{{ char_actor_key }}_{{ loop.index }}"
                                   name="{{ char_actor_key }}"
                                   value="{{ image_info.full_path }}">
                            <img src="{{ url_for('serve_comfyui_image', filepath=image_info.relative_path) }}"
                                 alt="{{ image_info.filename }}"
                                 title="{{ image_info.filename }}">
                            <div class="filename">{{ image_info.filename }}</div>
                        </label>
                    </div>
                    {% endfor %}
                </div>
            {% else %}
                <p class="no-images">No swapped images found in API output folder for this character.</p>
            {% endif %}
        </div>
        {% endfor %}
        <button type="submit" class="submit-button">Submit Approved Images</button>
    </form>
</body>
</html>
"""

flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = os.urandom(24)

@flask_app.route('/comfyui_images/<path:filepath>')
def serve_comfyui_image(filepath):
    """Serves images relative to the COMFYUI_OUTPUT_DIR."""
    #log_step(f"Flask: Request image /comfyui_images/{filepath}", level="debug")
    try:
        base_path = Path(COMFYUI_OUTPUT_DIR).resolve()
        safe_filepath = Path(filepath).as_posix()
        absolute_req_path = (base_path / safe_filepath).resolve()
        # Security check: Ensure requested path is within the COMFYUI_OUTPUT_DIR
        if not absolute_req_path.is_relative_to(base_path):
            log_step(f"Flask: Forbidden path traversal attempt: {filepath}", level="warning")
            abort(403) # Forbidden
        if not absolute_req_path.is_file():
             log_step(f"Flask: Image file not found: {absolute_req_path}", level="warning")
             abort(404) # Not found
        # Serve file relative to the actual base output directory
        return send_from_directory(base_path, safe_filepath)
    except Exception as e:
         log_step(f"Flask: Error serving comfyui image {filepath}: {e}", level="error")
         abort(500) # Internal server error

@flask_app.route('/')
def approval_page():
    """Renders the main approval template."""
    global candidates_data_for_flask, project_name_for_flask
    try:
        if candidates_data_for_flask is None or project_name_for_flask is None:
            log_step("Flask: Template data missing.", level="error"); return "Server Error: Data missing.", 500
        return render_template_string(APPROVAL_HTML_TEMPLATE, candidates_data=candidates_data_for_flask, project_name=project_name_for_flask)
    except Exception as e: import traceback; tb=traceback.format_exc(); log_step(f"Flask: Template render error: {e}\n{tb}", level="error"); return f"<pre>Template Error: {e}</pre>", 500

@flask_app.route('/submit_approval', methods=['POST'])
def submit_approval():
    """Handles approval form submission."""
    global web_approval_results, web_approval_event
    # Use getlist to handle multiple checkboxes with the same name
    web_approval_results = {}
    # request.form is a MultiDict, iterate through its keys
    for key in request.form.keys():
         web_approval_results[key] = request.form.getlist(key) # Store list of paths for each key

    log_step("Received approvals from web UI.", level="success")
    # log_step(f"Approvals: {json.dumps(web_approval_results, indent=2)}", level="debug")
    web_approval_event.set()
    try: shutdown_func = request.environ.get('werkzeug.server.shutdown');
    except RuntimeError: shutdown_func = None
    if shutdown_func: log_step('Flask: Shutting down...', level="info"); shutdown_func()
    else: log_step('Flask: Shutdown func not found.', level="warning")
    return "Approvals received! You can close this window."

def run_flask_app(host, port, data, project_name):
    """Runs the Flask app in a separate thread."""
    global candidates_data_for_flask, project_name_for_flask # Make data accessible
    candidates_data_for_flask = data; project_name_for_flask = project_name
    log_step(f"Starting Flask server on http://{host}:{port}")
    try: flask_app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception as e: log_step(f"Flask server error: {e}", level="error"); web_approval_event.set() # Signal failure

def present_web_approval_page(candidates_data, project_name):
    """Starts Flask server and waits for approval submission."""
    global web_approval_results, web_approval_event
    web_approval_results = None; web_approval_event.clear()
    flask_thread = threading.Thread(target=run_flask_app, args=(FLASK_HOST, FLASK_PORT, candidates_data, project_name), daemon=True); flask_thread.start(); time.sleep(2)
    log_step(f"Approval UI running at http://{FLASK_HOST}:{FLASK_PORT}", important=True); log_step("Open URL, check images to approve, click 'Submit'."); log_step(f"Waiting for submission (Timeout: {WEB_APPROVER_TIMEOUT}s)...")
    event_set = web_approval_event.wait(timeout=WEB_APPROVER_TIMEOUT)
    if not event_set: log_step("Timeout waiting for approval.", level="error"); return None
    else:
        if web_approval_results is None: log_step("Approval event set, but no results captured.", level="error"); return None
        log_step("Approval submitted.", level="success"); return web_approval_results

# --- Main Logic ---
def run_approval(project_path: Path):
    """Finds generated images, presents UI, copies approved files."""
    log_step(f"--- Step 1: Find Project and API Output ---", important=True)

    if not project_path or not project_path.is_dir():
        log_step(f"Invalid project path: {project_path}", level="error"); return False
    log_step(f"Using project path: {project_path}")

    # Validate ComfyUI Output Dir
    comfy_base_output = Path(COMFYUI_OUTPUT_DIR)
    if not comfy_base_output.is_dir():
        log_step(f"ComfyUI Output directory not found: {comfy_base_output}", level="error"); return False
    comfy_api_outputs_path = comfy_base_output / API_OUTPUTS_SUBDIR
    if not comfy_api_outputs_path.is_dir():
        log_step(f"ComfyUI API Outputs sub-directory not found: {comfy_api_outputs_path}", level="error"); return False

    project_name_base = "_".join(project_path.name.split('_')[:-1]) # e.g., sholay_Deep_Sea_Hydro-Futurism
    log_step(f"Project name base for searching API runs: {project_name_base}")

    api_run_folder = find_latest_api_run_dir(comfy_api_outputs_path, project_name_base)
    if not api_run_folder:
        log_step(f"No matching ComfyUI API run folder found for project '{project_name_base}' in {comfy_api_outputs_path}", level="error"); return False

    # Define target directory for approved images
    approval_target_dir = project_path / APPROVED_IMAGES_FOLDER_NAME
    log_step(f"Approved images will be saved to: {approval_target_dir}")

    # --- Step 2: Gather Candidate Images ---
    log_step(f"--- Step 2: Gathering Images from {api_run_folder.name} ---", important=True)
    candidates_for_web = {}
    character_folders = [d for d in api_run_folder.iterdir() if d.is_dir()]

    if not character_folders:
         log_step(f"No character subfolders found within API run folder: {api_run_folder}", level="warning")
         # Continue, maybe UI shows nothing

    for char_actor_folder in character_folders:
        # Parse Character and Actor Name from folder name (e.g., Veeru__Dharmendra)
        folder_name = char_actor_folder.name
        if "__" not in folder_name:
             log_step(f" Skipping folder with unexpected name format: {folder_name}", level="warning"); continue
        char_name_san, actor_name_san = folder_name.split("__", 1)
        # Optional: Convert back to original spacing if needed for display?
        # For now, use the sanitized versions for consistency key, actual names for display later if possible
        char_actor_key = folder_name # Use the folder name as the key

        swapped_images_path = char_actor_folder / SWAPPED_IMAGES_SUBFOLDER
        if not swapped_images_path.is_dir():
             log_step(f" Swapped images folder not found for {char_actor_key} at {swapped_images_path}", level="debug"); continue

        image_list_for_actor = []
        # Find JPG and PNG images
        found_image_paths = glob.glob(str(swapped_images_path / '*.[jp][pn]g'))
        log_step(f" Found {len(found_image_paths)} potential images in {swapped_images_path.relative_to(comfy_base_output)}", level="debug")

        for image_path_str in found_image_paths:
            try:
                image_path = Path(image_path_str)
                full_path = image_path.resolve()
                # Relative path from COMFYUI_OUTPUT_DIR for Flask serving
                relative_path = full_path.relative_to(comfy_base_output.resolve())
                image_list_for_actor.append({
                    "filename": image_path.name,
                    "full_path": str(full_path),
                    "relative_path": relative_path.as_posix() # Use forward slashes for URL
                })
            except ValueError as e:
                log_step(f" Error creating relative path for {image_path}. Base: {comfy_base_output}. Error: {e}", level="warning")
            except Exception as e:
                 log_step(f" Error processing image path {image_path}: {e}", level="warning")

        if image_list_for_actor:
             # Try to get "nicer" names for display if possible (e.g., from JSON) - Optional enhancement
             display_char_name = char_name_san.replace('_', ' ')
             display_actor_name = actor_name_san.replace('_', ' ')
             candidates_for_web[char_actor_key] = {
                 "character_name": display_char_name,
                 "actor_name": display_actor_name,
                 "candidates": sorted(image_list_for_actor, key=lambda x: x['filename']) # Sort images
             }

    # --- Step 3: Launch UI ---
    log_step(f"--- Step 3: Launching Web Approval UI ---", important=True)
    if not candidates_for_web:
        log_step("No generated swapped images found to approve.", level="warning")
        return True # Nothing to do, consider it success? Or False? Let's say True.

    selections = present_web_approval_page(candidates_for_web, project_path.name)

    # --- Step 4: Process Approvals ---
    log_step(f"--- Step 4: Processing Approvals ---", important=True)
    if selections is None:
        log_step("Approval UI failed or timed out. No images were approved.", level="error")
        return False # Indicate failure or timeout

    if not selections:
         log_step("No images were selected for approval.", level="info")
         return True # Nothing selected is not an error state

    try:
        approval_target_dir.mkdir(parents=True, exist_ok=True)
        log_step(f"Ensured approval target directory exists: {approval_target_dir}")
    except OSError as e:
        log_step(f"Failed to create approval directory {approval_target_dir}: {e}", level="error")
        return False

    approved_count = 0
    copy_errors = 0
    for char_actor_key, list_of_selected_paths in selections.items():
         log_step(f" Processing approvals for {char_actor_key}: {len(list_of_selected_paths)} image(s)")
         for source_path_str in list_of_selected_paths:
             try:
                 source_path = Path(source_path_str)
                 if not source_path.is_file():
                     log_step(f"  ERROR: Selected file path not found: {source_path_str}", level="error")
                     copy_errors += 1
                     continue

                 # --- NEW RENAMING LOGIC ---
                 original_filename = source_path.name # e.g., swapped_medium_00001_.png
                 # Prepend the character/actor key to the original filename
                 new_filename = f"{char_actor_key}__{original_filename}"
                 # Construct the full destination path
                 destination_path = approval_target_dir / new_filename
                 # --- END RENAMING LOGIC ---

                 shutil.copy2(source_path, destination_path) # Copy to new path/name
                 log_step(f"  Approved and Copied: {original_filename} -> {new_filename}", level="success")
                 approved_count += 1
             except Exception as e:
                 log_step(f"  ERROR copying/renaming file {source_path_str}: {e}", level="error")
                 copy_errors += 1

    # --- Step 5: Summary ---
    log_step(f"--- Step 5: Approval Summary ---", important=True)
    log_step(f"Total images approved and copied: {approved_count}")
    if copy_errors > 0:
        log_step(f"Number of errors during copy: {copy_errors}", level="error")
        return False # Indicate errors occurred
    else:
        log_step(f"Approved images saved in: {approval_target_dir}")
        return True


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Approve generated ComfyUI images via Web UI."); parser.add_argument("-p", "--project_path", help="Path to project folder. Omit for latest.")
    args = parser.parse_args(); log_step("===== Approve Images Script Started =====", important=True)

    # Validate crucial config paths
    if not COMFYUI_OUTPUT_DIR: log_step("Critical Error: COMFYUI_OUTPUT_DIR not configured.", level="error", important=True); exit(1)
    if not Path(COMFYUI_OUTPUT_DIR).is_dir(): log_step(f"Error: COMFYUI_OUTPUT_DIR invalid: {COMFYUI_OUTPUT_DIR}", level="error"); exit(1)

    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not project_to_process.parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", level="error", important=True); sys.exit(1)

    log_step(f"Using project: {project_to_process.name}")
    success = run_approval(project_to_process)

    if success: log_step("Script finished successfully.", level="success", important=True); sys.exit(0)
    else: log_step("Script finished with errors or timeout.", level="error", important=True); sys.exit(1)