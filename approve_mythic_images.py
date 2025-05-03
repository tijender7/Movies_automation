# approve_mythic_images.py
# Allows user selection of generated Mythic Movie scene images via a web UI.

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
import glob
import time

# --- Web Server Imports ---
try:
    from flask import Flask, request, render_template_string, send_from_directory, abort
except ImportError as e:
    print(f"ERROR: Missing Flask library. Please install it: pip install Flask")
    sys.exit(1)

# --- Import project config ---
try:
    # Import the specific config for the Mythic Movie project
    import config_mythic as config
except ImportError:
    print(f"ERROR: Failed to import config_mythic.py. Ensure it exists.")
    exit(1)

# --- Global variable for Flask communication ---
web_approval_results = None
web_approval_event = threading.Event()

# --- Logging Setup ---
log_file_path = config.LOG_DIR / f"{config.LOG_FILE_BASENAME_APPROVE}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
    log.log(getattr(logging, level.upper(), logging.INFO), final_msg)

# --- Project Path Helpers ---
def find_latest_project_dir(base_dir):
    """Finds the most recently created directory in base_dir matching the project pattern."""
    log_step(f"Searching for latest project directory in: {base_dir}", level="debug")
    latest_dir = None
    latest_time = 0
    try:
        # Ensure base_dir is a Path object
        base_path = Path(base_dir)
        if not base_path.is_dir():
            log_step(f"Base project directory not found: {base_dir}", level="error")
            return None
        # Iterate and find directories starting with 'mythic_movie_'
        subdirs = [d for d in base_path.iterdir() if d.is_dir() and d.name.startswith("mythic_movie_")]
        if not subdirs:
            log_step(f"No project directories found matching pattern in {base_dir}", level="warning")
            return None
        # Find the latest based on modification time
        for item in subdirs:
            try:
                mod_time = item.stat().st_mtime
                if mod_time > latest_time:
                    latest_time = mod_time
                    latest_dir = item
            except OSError as e:
                log_step(f"Could not stat directory {item.name}: {e}", level="warning")
    except Exception as e:
        log_step(f"Error searching for latest project: {e}", level="error")
        return None

    if latest_dir:
        log_step(f"Found latest project directory: {latest_dir.name}", level="success")
    else:
        # This case should be caught earlier if subdirs is empty
        log_step(f"No project directories found matching pattern in {base_dir}", level="warning")
    return latest_dir

# --- ComfyUI Output Path Helper ---
def find_latest_api_run_dir(base_api_output_path, project_name_base):
    """Finds the latest ComfyUI API output run folder matching the project base name."""
    log_step(f"Searching for latest API run folder starting with '{project_name_base}' in: {base_api_output_path}")
    try:
        base = Path(base_api_output_path)
        if not base.is_dir():
            log_step(f"Base API output dir not found: {base_api_output_path}", level="error")
            return None
        # Find directories starting with the project name base (e.g., mythic_movie_shiva)
        # Note: The generation script *doesn't* add a timestamp to this folder name,
        # it uses the project_path.name directly inside API_OUTPUTS_MYTHIC.
        # We need to find the directory named *exactly* project_name_base inside base_api_output_path.
        matching_dirs = [d for d in base.iterdir() if d.is_dir() and d.name == project_name_base]

        if not matching_dirs:
            log_step(f"No API run folder found named '{project_name_base}' in {base_api_output_path}", level="warning")
            # Fallback: Maybe the generation script *did* add a timestamp? Let's check that pattern too.
            log_step(f"Checking pattern '{project_name_base}_*'", level="debug")
            matching_dirs = [d for d in base.iterdir() if d.is_dir() and d.name.startswith(project_name_base + '_')]
            if not matching_dirs:
                 log_step(f"No matching API run folders found using pattern either.", level="error")
                 return None
            # If found via timestamp pattern, sort by time
            matching_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

        latest_run_dir = matching_dirs[0] # Take the exact match or the latest timestamped one
        log_step(f"Found API run folder: {latest_run_dir.name}", level="success")
        return latest_run_dir
    except Exception as e:
        log_step(f"Error finding latest API run folder: {e}", level="error")
        return None

# --- Flask App ---
# Updated HTML Template for simple image list
APPROVAL_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mythic Scene Image Approval</title>
    <style>
        body { font-family: sans-serif; margin: 20px; background-color: #f8f9fa; }
        h1, h2 { text-align: center; color: #343a40; margin-bottom: 20px; }
        .image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 20px; margin-bottom: 15px; padding: 20px; background-color: #fff; border: 1px solid #dee2e6; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
        .image-item { text-align: center; border: 1px solid #ced4da; padding: 10px; border-radius: 5px; background-color:#fdfdfd; }
        .image-item img { max-width: 100%; height: 200px; object-fit: contain; border: 1px solid #e0e0e0; margin-bottom: 10px; cursor: pointer; }
        .image-item input[type="checkbox"] { margin-right: 8px; transform: scale(1.2); cursor: pointer; }
        .image-item label { display: flex; align-items: center; justify-content: center; flex-direction: column; cursor: pointer; }
        .filename { font-size: 0.9em; color: #6c757d; word-break: break-all; margin-top: 5px; }
        .submit-button { display: block; width: 250px; margin: 40px auto 20px auto; padding: 12px 25px; background-color: #198754; color: white; border: none; border-radius: 5px; font-size: 1.1em; cursor: pointer; transition: background-color 0.2s; }
        .submit-button:hover { background-color: #157347; }
        .no-images { color: #6c757d; font-style: italic; padding: 30px; text-align: center; font-size: 1.2em; }
        /* Style checked items */
        .image-item input[type="checkbox"]:checked + label img { border: 3px solid #0d6efd; box-shadow: 0 0 8px rgba(13,110,253,0.4); }
    </style>
</head>
<body>
    <h1>Mythic Scene Image Approval</h1>
    <h2>Project: {{ project_name }}</h2>
    <form action="/submit_approval" method="post">
        {% if candidate_images %}
            <div class="image-grid">
                {% for image_info in candidate_images %}
                <div class="image-item">
                     <label for="img_{{ loop.index }}">
                        <input type="checkbox"
                               id="img_{{ loop.index }}"
                               name="approved_image"
                               value="{{ image_info.full_path }}">
                        <img src="{{ url_for('serve_comfyui_image', filepath=image_info.relative_path) }}"
                             alt="{{ image_info.filename }}"
                             title="{{ image_info.filename }}">
                        <div class="filename">{{ image_info.filename }}</div>
                    </label>
                </div>
                {% endfor %}
            </div>
            <button type="submit" class="submit-button">Submit Approved Images</button>
        {% else %}
            <p class="no-images">No scene images found in the output folder for this project run.</p>
        {% endif %}
    </form>
</body>
</html>
"""

flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = os.urandom(24)

@flask_app.route('/comfyui_images/<path:filepath>')
def serve_comfyui_image(filepath):
    """Serves images relative to the COMFYUI_OUTPUT_DIR."""
    # log_step(f"Flask: Request image /comfyui_images/{filepath}", level="debug")
    try:
        # Use the COMFYUI_OUTPUT_DIR from config
        base_path = Path(config.COMFYUI_OUTPUT_DIR).resolve()
        # Sanitize filepath - convert to posix, remove leading slashes if any
        safe_filepath = Path(filepath).as_posix().lstrip('/')
        absolute_req_path = (base_path / safe_filepath).resolve()

        # Security check: Ensure requested path is within the COMFYUI_OUTPUT_DIR
        if not absolute_req_path.is_relative_to(base_path):
            log_step(f"Flask: Forbidden path traversal attempt: {filepath}", level="warning")
            abort(403) # Forbidden

        if not absolute_req_path.is_file():
             log_step(f"Flask: Image file not found: {absolute_req_path}", level="warning")
             abort(404) # Not found

        # Serve file relative to the actual base output directory
        log_step(f"Flask: Serving file: {safe_filepath} from base: {base_path}", level="debug")
        return send_from_directory(base_path, safe_filepath)
    except Exception as e:
         log_step(f"Flask: Error serving comfyui image {filepath}: {e}", level="error")
         abort(500) # Internal server error

@flask_app.route('/')
def approval_page():
    """Renders the main approval template."""
    global candidate_list_for_flask, project_name_for_flask # Use updated global var name
    try:
        # Check if the list exists and pass it to the template
        if candidate_list_for_flask is None or project_name_for_flask is None:
            log_step("Flask: Template data missing (candidate_list or project_name).", level="error")
            return "Server Error: Data missing.", 500
        return render_template_string(APPROVAL_HTML_TEMPLATE, candidate_images=candidate_list_for_flask, project_name=project_name_for_flask)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log_step(f"Flask: Template render error: {e}\n{tb}", level="error")
        return f"<pre>Template Error: {e}</pre>", 500

@flask_app.route('/submit_approval', methods=['POST'])
def submit_approval():
    """Handles approval form submission."""
    global web_approval_results, web_approval_event
    # Get the list of selected full paths directly
    web_approval_results = request.form.getlist("approved_image") # Use the generic name

    log_step(f"Received {len(web_approval_results)} approvals from web UI.", level="success")
    log_step(f"Approved paths: {json.dumps(web_approval_results, indent=2)}", level="debug") # Log the list
    web_approval_event.set() # Signal that submission occurred

    # Attempt graceful shutdown
    try:
        shutdown_func = request.environ.get('werkzeug.server.shutdown')
        if shutdown_func:
            log_step('Flask: Shutting down server...', level="info")
            shutdown_func()
        else:
            log_step('Flask: werkzeug.server.shutdown function not found. Cannot self-shutdown.', level="warning")
    except Exception as e:
         log_step(f'Flask: Error during shutdown attempt: {e}', level="warning")

    return "Approvals received! You can close this window."

def run_flask_app(host, port, image_list, project_name):
    """Runs the Flask app in a separate thread."""
    global candidate_list_for_flask, project_name_for_flask # Use updated global var names
    candidate_list_for_flask = image_list
    project_name_for_flask = project_name
    log_step(f"Starting Flask server on http://{host}:{port}")
    try:
        # Disable Flask reloader and debug for stability in thread
        flask_app.run(host=host, port=port, debug=False, use_reloader=False)
    except SystemExit:
        log_step("Flask server shut down.", level="info")
    except Exception as e:
        log_step(f"Flask server encountered an error: {e}", level="error")
        web_approval_event.set() # Signal failure if server crashes

def present_web_approval_page(candidate_image_list, project_name):
    """Starts Flask server and waits for approval submission."""
    global web_approval_results, web_approval_event
    web_approval_results = None # Reset results
    web_approval_event.clear() # Reset event

    # Start Flask in a daemon thread so it doesn't block main script exit
    flask_thread = threading.Thread(
        target=run_flask_app,
        args=(config.FLASK_HOST, config.FLASK_PORT, candidate_image_list, project_name),
        daemon=True
    )
    flask_thread.start()
    time.sleep(2) # Give server a moment to start

    log_step(f"Approval UI running at http://{config.FLASK_HOST}:{config.FLASK_PORT}", important=True)
    log_step("Open the URL in your browser, check the images you want to approve, and click 'Submit Approved Images'.")
    log_step(f"Waiting for submission (Timeout: {config.WEB_APPROVER_TIMEOUT}s)...")

    # Wait for the event to be set by the /submit_approval route, with timeout
    event_set = web_approval_event.wait(timeout=config.WEB_APPROVER_TIMEOUT)

    if not event_set:
        log_step(f"Timeout ({config.WEB_APPROVER_TIMEOUT}s) waiting for approval submission.", level="error")
        # Attempt to stop Flask thread? Difficult without explicit shutdown mechanism from outside.
        # Since it's a daemon, it should exit when the main thread finishes.
        return None # Indicate timeout
    else:
        # Event was set, check if results were captured (they should be)
        if web_approval_results is None:
            # This might happen if Flask crashed before setting results but after setting event
            log_step("Approval event was set, but no results were captured. Check Flask logs.", level="error")
            return None
        log_step("Approval submitted via web UI.", level="success")
        return web_approval_results # Return the list of approved paths

# --- Main Logic ---
def run_approval(project_path: Path):
    """Finds generated images, presents UI, copies approved files."""
    log_step(f"--- Step 1: Find Project and API Output ---", important=True)

    if not project_path or not project_path.is_dir():
        log_step(f"Invalid project path provided: {project_path}", level="error")
        return False
    log_step(f"Using project path: {project_path}")

    # Validate ComfyUI Output Dir from config
    comfy_base_output = Path(config.COMFYUI_OUTPUT_DIR)
    if not comfy_base_output.is_dir():
        log_step(f"ComfyUI Output directory not found: {comfy_base_output}", level="error")
        return False
    comfy_api_outputs_path = comfy_base_output / config.API_OUTPUTS_SUBDIR
    if not comfy_api_outputs_path.is_dir():
        log_step(f"ComfyUI API Outputs sub-directory not found: {comfy_api_outputs_path}", level="error")
        return False

    # Use the project folder name directly to find the corresponding API output folder
    project_folder_name = project_path.name
    log_step(f"Searching for API run folder named: {project_folder_name}")

    api_run_folder = find_latest_api_run_dir(comfy_api_outputs_path, project_folder_name)
    if not api_run_folder:
        log_step(f"No matching ComfyUI API run folder found for project '{project_folder_name}' in {comfy_api_outputs_path}", level="error")
        return False

    # Define the source directory where generated images are expected
    image_source_dir = api_run_folder / config.OUTPUT_IMAGES_SUBFOLDER
    log_step(f"Looking for images in: {image_source_dir}")
    if not image_source_dir.is_dir():
        log_step(f"Generated images subfolder ('{config.OUTPUT_IMAGES_SUBFOLDER}') not found within API run folder: {api_run_folder.name}", level="warning")
        # Allow continuing, UI will show "no images" message
        image_source_dir = None # Flag that the source dir doesn't exist

    # Define target directory for approved images within the project folder
    approval_target_dir = project_path / config.MYTHIC_APPROVED_FOLDER_NAME
    log_step(f"Approved images will be saved to: {approval_target_dir}")

    # --- Step 2: Gather Candidate Images ---
    log_step(f"--- Step 2: Gathering Scene Images from {api_run_folder.name} ---", important=True)
    candidate_image_list = [] # Simple list for Flask

    if image_source_dir: # Only proceed if the source directory exists
        # Find JPG and PNG images matching the scene pattern
        # Using Path.glob for potentially better cross-platform compatibility
        found_image_paths = list(image_source_dir.glob('*_scene_*__*.[jp][pn]g'))
        log_step(f" Found {len(found_image_paths)} potential scene images in {image_source_dir.relative_to(comfy_base_output)}", level="info")

        for image_path in found_image_paths:
            try:
                full_path = image_path.resolve()
                # Relative path from COMFYUI_OUTPUT_DIR for Flask serving URL
                relative_path = full_path.relative_to(comfy_base_output.resolve())
                candidate_image_list.append({
                    "filename": image_path.name,
                    "full_path": str(full_path), # Store as string for JSON/form value later
                    "relative_path": relative_path.as_posix() # Use forward slashes for URL
                })
            except ValueError as e:
                log_step(f" Error creating relative path for {image_path}. Base: {comfy_base_output}. Error: {e}", level="warning")
            except Exception as e:
                 log_step(f" Error processing image path {image_path}: {e}", level="warning")

        # Sort the list for consistent display
        candidate_image_list.sort(key=lambda x: x['filename'])

    # --- Step 3: Launch UI ---
    log_step(f"--- Step 3: Launching Web Approval UI ---", important=True)
    if not candidate_image_list:
        log_step("No generated scene images found to approve.", level="warning")
        return True # Nothing to do, consider it success

    # Pass the simple list to the Flask UI
    selections = present_web_approval_page(candidate_image_list, project_path.name)

    # --- Step 4: Process Approvals ---
    log_step(f"--- Step 4: Processing Approvals ---", important=True)
    if selections is None:
        log_step("Approval UI failed or timed out. No images were approved.", level="error")
        return False # Indicate failure or timeout

    if not selections:
         log_step("No images were selected for approval.", level="info")
         return True # Nothing selected is not an error state

    # Ensure the target directory exists
    try:
        approval_target_dir.mkdir(parents=True, exist_ok=True)
        log_step(f"Ensured approval target directory exists: {approval_target_dir}")
    except OSError as e:
        log_step(f"Failed to create approval directory {approval_target_dir}: {e}", level="error")
        return False

    approved_count = 0
    copy_errors = 0
    # 'selections' is now just a list of full source paths
    for source_path_str in selections:
         try:
             source_path = Path(source_path_str)
             if not source_path.is_file():
                 log_step(f"  ERROR: Selected file path not found: {source_path_str}", level="error")
                 copy_errors += 1
                 continue

             # Destination path uses the original filename
             destination_path = approval_target_dir / source_path.name

             # Avoid overwriting existing files in target (optional, uncomment if needed)
             # if destination_path.exists():
             #     log_step(f"  Skipping copy: File already exists in target: {destination_path.name}", level="warning")
             #     continue

             shutil.copy2(source_path, destination_path) # Copy, preserving metadata
             log_step(f"  Approved and Copied: {source_path.name}", level="success")
             approved_count += 1
         except Exception as e:
             log_step(f"  ERROR copying file {source_path_str}: {e}", level="error")
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
    parser = argparse.ArgumentParser(description="Approve generated Mythic Movie scene images via Web UI.")
    parser.add_argument(
        "-p", "--project_path",
        help="Path to the specific project folder (e.g., 'mythic_movie_project/mythic_movie_shiva_...'). Omit to use the latest project found."
    )
    args = parser.parse_args()
    log_step("===== Approve Mythic Images Script Started =====", important=True)

    # Validate crucial config paths
    if not config.COMFYUI_OUTPUT_DIR or not Path(config.COMFYUI_OUTPUT_DIR).is_dir():
        log_step(f"Critical Error: COMFYUI_OUTPUT_DIR ('{config.COMFYUI_OUTPUT_DIR}') not configured or not found.", level="error", important=True)
        exit(1)
    if not config.PROJECTS_BASE_DIR or not Path(config.PROJECTS_BASE_DIR).is_dir():
         log_step(f"Critical Error: PROJECTS_BASE_DIR ('{config.PROJECTS_BASE_DIR}') not configured or not found.", level="error", important=True)
         exit(1)

    # Determine project path
    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path).resolve()
        if not project_to_process.is_dir():
             log_step(f"Error: Provided project path not found or not a directory: {project_to_process}", level="error", important=True)
             exit(1)
        # Check if it's inside the configured PROJECTS_BASE_DIR
        try:
            project_to_process.relative_to(config.PROJECTS_BASE_DIR)
        except ValueError:
             log_step(f"Warning: Provided project path '{project_to_process}' is not inside the configured PROJECTS_BASE_DIR '{config.PROJECTS_BASE_DIR}'.", level="warning")
    else:
        log_step(f"No specific project path provided. Finding latest project in {config.PROJECTS_BASE_DIR}...")
        project_to_process = find_latest_project_dir(config.PROJECTS_BASE_DIR)
        if not project_to_process:
            log_step(f"No project directory found in {config.PROJECTS_BASE_DIR}.", level="error", important=True)
            sys.exit(1)

    log_step(f"Using project: {project_to_process.name}")

    # Run the main approval logic
    success = run_approval(project_to_process)

    # Exit with appropriate status code
    if success:
        log_step("===== Approve Mythic Images Script Finished Successfully =====", level="success", important=True)
        sys.exit(0)
    else:
        log_step("===== Approve Mythic Images Script Finished with Errors =====", level="error", important=True)
        sys.exit(1)