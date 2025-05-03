# web_approver.py
import logging
import threading
import queue
import socket
import json
import shutil
from pathlib import Path
from flask import Flask, render_template, request, send_from_directory, abort, send_file
import os # Import os for urandom

# Import configuration
try:
    import config
except ModuleNotFoundError:
    print("ERROR: config.py not found. Make sure it's in the same directory.")
    exit()


# --- Basic Logging Setup (for this module) ---
# It's good practice for modules to configure their own logger if needed,
# or rely on the main script's configuration if run together.
# For standalone testing, configure here:
log = logging.getLogger("WebApprover")
if not log.handlers: # Avoid adding handlers multiple times if imported
    log.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    log.addHandler(stream_handler)
    # Optionally add file handler too if needed for standalone testing
    # file_handler = logging.FileHandler(config.LOG_FILE, mode='a')
    # file_handler.setFormatter(formatter)
    # log.addHandler(file_handler)

# --- Shared Data Queues ---
# These queues facilitate communication between the main script thread and the Flask thread
approval_request_queue = queue.Queue(maxsize=1)
approval_result_queue = queue.Queue(maxsize=1)

# --- Flask App Setup ---
template_dir = Path(__file__).parent / 'templates'
flask_approver_app = Flask(__name__, template_folder=str(template_dir))
flask_approver_app.secret_key = os.urandom(24)

# --- Flask Routes ---

@flask_approver_app.route('/')
def index_approval():
    """Displays the page for approving generated images."""
    request_data = None
    error_msg = None
    log.debug("Received request for /")
    if not approval_request_queue.empty():
        try:
            request_data = approval_request_queue.queue[0] # Peek
            log.debug(f"Found request data in queue: {list(request_data.get('all_candidates_to_approve',{}).keys())}")
        except IndexError:
             error_msg = "Approval data temporarily unavailable. Please wait or refresh."
             log.warning("IndexError peeking at approval_request_queue")
    else:
         error_msg = "Waiting for main script to generate candidate images..."
         log.debug("Approval request queue is empty.")

    movie_name_display = "Unknown Movie"
    candidates = None
    if request_data and request_data.get('all_candidates_to_approve'):
         candidates = request_data['all_candidates_to_approve']
         first_actor_data = next(iter(candidates.values()), None)
         if first_actor_data:
              movie_name_display = first_actor_data.get('movie_name', movie_name_display)

    log.debug(f"Rendering approval template with {len(candidates or {})} candidates.")
    return render_template('approve_generated_images.html',
                           all_candidates_to_approve=candidates,
                           movie_name=movie_name_display,
                           error=error_msg)

@flask_approver_app.route('/submit_approvals', methods=['POST'])
def handle_all_approvals():
    """Handles the submission of approvals for ALL actors."""
    approvals = {}
    processed_actors = request.form.getlist('actors_processed')
    log.info(f"[WebApprover] Received approval submission for actors: {processed_actors}")

    for actor_name in processed_actors:
        selection_key = f"approval_{actor_name}"
        skip_key = f"skip_{actor_name}"
        # If skip is checked, override approvals for this actor
        if request.form.get(skip_key) == "SKIP":
            approvals[actor_name] = "SKIP"
            log.info(f"[WebApprover]   -> Actor {actor_name} was SKIPPED by user.")
        else:
            approved_values = request.form.getlist(selection_key)
            if approved_values:
                approvals[actor_name] = approved_values
                log.info(f"[WebApprover]   -> Approvals for {actor_name}: {approved_values}")
            else:
                approvals[actor_name] = []
                log.warning(f"[WebApprover]   -> No images approved for {actor_name}, not skipped.")

    if approvals:
        log.info("[WebApprover] Putting combined approvals into result queue.")
        approval_result_queue.put(approvals) # Put the entire dict of approvals
        # Clear request queue
        try: approval_request_queue.get_nowait()
        except queue.Empty: pass
        return f"<h1>All Approvals Received!</h1><p>Processed approvals for {len(approvals)} actors.</p><p>You can now return to the script's terminal window.</p>"
    else:
        log.error("[WebApprover] No approval data received in submission.")
        return "<h1>Error</h1><p>No approval data received in submission.</p><a href='/'>Go back</a>", 400

# --- Route for Serving Generated Images ---
@flask_approver_app.route('/generated_images/<path:filepath>')
def serve_generated_image(filepath):
    """Serves generated/swapped images relative to the COMFYUI_OUTPUT_DIR."""
    # Filepath received is like: API_OUTPUTS/Sholay...Run/Char__Actor/swapped_images/image.png
    log.debug(f"Request to serve generated image (relative path): {filepath}")
    try:
        # Construct the FULL absolute path based on the COMFYUI_OUTPUT_DIR from config
        image_abs_path = (config.COMFYUI_OUTPUT_DIR.resolve() / filepath).resolve()

        # Security Check: Ensure the resolved path is still within the configured COMFYUI_OUTPUT_DIR
        if str(image_abs_path).startswith(str(config.COMFYUI_OUTPUT_DIR.resolve())) and image_abs_path.is_file():
            log.debug(f"Serving image file: {image_abs_path}")
            # Use send_file which is generally safer for absolute paths
            return send_file(image_abs_path)
        elif not image_abs_path.is_file():
             log.warning(f"Generated image file not found at path: {image_abs_path}")
             abort(404)
        else: # Path resolved outside the allowed directory
             log.warning(f"Attempt to access file outside configured ComfyUI output dir: {filepath} -> {image_abs_path}")
             abort(403) # Forbidden
    except Exception as e:
        log.error(f"Error serving generated file '{filepath}': {e}", exc_info=True)
        abort(500)


# --- Function to run Flask (called by main orchestrator or approval script) ---
def run_flask_approver_app():
    """Runs the Flask app for image approval."""
    # Use Werkzeug logger directly for less verbose startup message
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.setLevel(logging.WARNING) # Show only warnings and errors
    log.info(f"Starting Flask server for approvals on port {config.FLASK_PORT}...")
    try:
        flask_approver_app.run(host='0.0.0.0', port=config.FLASK_PORT, debug=False, use_reloader=False)
    except Exception as e:
        log.error(f"Flask approval server failed: {e}")

# --- Function called by the main script to start interaction ---
def present_image_approval_page(all_candidates_data):
    """
    Puts candidate data in queue, prints URLs, waits for results from the queue.
    Returns a dictionary of approvals {'actor_name': 'selected_path_or_SKIP'} or None.
    """
    if not all_candidates_data:
        log.error("No candidate data provided to present approval page.")
        return None

    log.info("--- Starting Web UI Image Approval Process ---")

    request_data = { "all_candidates_to_approve": all_candidates_data }

    # Clear queues and put new request data
    while not approval_result_queue.empty(): approval_result_queue.get_nowait()
    while not approval_request_queue.empty(): approval_request_queue.get_nowait()
    log.debug("Putting approval request data into queue.")
    approval_request_queue.put(request_data)

    # Get local IP address to display URL
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        access_url_local = f"http://127.0.0.1:{config.FLASK_PORT}/"
        access_url_network = f"http://{local_ip}:{config.FLASK_PORT}/"
        print("="*50)
        print("ACTION REQUIRED: Approve Generated Images via Web Browser")
        print("Review candidates for all actors.")
        print(f"Open your web browser and go to:")
        print(f"  -> {access_url_local}")
        print(f"  OR (from another device on the same network):")
        print(f"  -> {access_url_network}")
        print(f"For EACH actor: Select ONE image to approve OR choose 'Skip'.")
        print("Click 'Submit All Approvals' at the bottom when done.")
        print("Waiting for your submission in the browser...")
        print("="*50)
    except socket.gaierror:
        log.warning("Could not determine local IP address.")
        print(f"ACTION REQUIRED: Open http://127.0.0.1:{config.FLASK_PORT}/ in your browser.")
        print("Approve images and click 'Submit All Approvals'. Waiting...")

    # Wait for the result dictionary from the Flask app
    all_approvals = None
    try:
        # Blocking get with timeout
        log.info(f"Waiting for approval results from queue (Timeout: {config.WEB_SELECTOR_TIMEOUT}s)...")
        all_approvals = approval_result_queue.get(timeout=config.WEB_SELECTOR_TIMEOUT)
    except queue.Empty:
        log.error("Timeout waiting for batch web approval.")
        # Ensure request queue is cleared if timeout happens
        try: approval_request_queue.get_nowait()
        except queue.Empty: pass
        return None # Indicate failure

    log.info("Received batch approval results via queue.")
    return all_approvals

# --- Optional: Add a main block for standalone testing ---
if __name__ == '__main__':
     print("This script contains the Flask app for image approval.")
     print("It should typically be imported and run by the main orchestrator or a dedicated approval script.")
     print("To test standalone (requires dummy data generation):")
     print("1. Manually create dummy project structure with images in 'generated_swapped_candidates'.")
     print("2. Add code here to populate 'approval_request_queue' with dummy data.")
     print("3. Call run_flask_approver_app() directly.")
     # Example placeholder:
     # run_flask_approver_app()