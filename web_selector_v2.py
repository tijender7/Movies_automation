import logging
import threading
import queue
import socket
import json
import shutil
from pathlib import Path
import os
from flask import Flask, render_template, request, send_from_directory, abort, send_file

import config

# --- Shared Data Queues ---
selection_request_queue = queue.Queue(maxsize=1)
selection_result_queue = queue.Queue(maxsize=1)

template_dir = Path(__file__).parent / 'templates'
flask_app = Flask(__name__, template_folder=str(template_dir))
flask_app.secret_key = os.urandom(24)

@flask_app.route('/')
def index_all():
    request_data = None
    error_msg = None
    if not selection_request_queue.empty():
        try:
            request_data = selection_request_queue.queue[0]
        except IndexError:
            error_msg = "Selection data temporarily unavailable. Please wait or refresh."
    else:
        error_msg = "Waiting for main script to gather all candidate images..."
    movie_name_display = "Unknown Movie"
    if request_data and request_data.get('all_candidates'):
        first_actor_data = next(iter(request_data['all_candidates'].values()), None)
        if first_actor_data:
            movie_name_display = first_actor_data.get('movie_name', movie_name_display)
    return render_template('select_all_images.html',
                           all_candidates=request_data.get('all_candidates') if request_data else None,
                           movie_name=movie_name_display,
                           error=error_msg)

@flask_app.route('/submit_all', methods=['POST'])
def handle_all_selections():
    selections = {}
    processed_actors = request.form.getlist('actors_processed')
    logging.info(f"[Flask] Received submission for actors: {processed_actors}")
    for actor_name in processed_actors:
        selection_key = f"selection_{actor_name}"
        selected_value = request.form.get(selection_key)
        if selected_value:
            selections[actor_name] = selected_value
            logging.info(f"[Flask]   -> Selection for {actor_name}: {selected_value}")
        else:
            selections[actor_name] = "SKIP"
            logging.warning(f"[Flask]   -> No selection received for {actor_name}, defaulting to SKIP.")
    if selections:
        logging.info("[Flask] Putting combined selections into result queue.")
        selection_result_queue.put(selections)
        try: selection_request_queue.get_nowait()
        except queue.Empty: pass
        return f"<h1>All Selections Received!</h1><p>Processed selections for {len(selections)} actors.</p><p>You can now return to the script's terminal window.</p>"
    else:
        return "<h1>Error</h1><p>No selection data received in submission.</p><a href='/'>Go back</a>", 400

@flask_app.route('/images/<path:filepath>')
def serve_image(filepath):
    try:
        base_serve_path = config.PROJECTS_BASE_DIR.resolve()
        image_abs_path = (base_serve_path / filepath).resolve()
        # Security check: ensure the image path is truly inside the base serving path and is a file
        if str(image_abs_path).startswith(str(base_serve_path)) and image_abs_path.is_file():
            logging.debug(f"Serving image: {image_abs_path}")
            return send_file(image_abs_path)
        else:
            logging.warning(f"Access Denied or File Not Found: {filepath} -> {image_abs_path}")
            abort(404)
    except Exception as e:
        logging.error(f"Error serving file {filepath}: {e}")
        abort(500)

def run_flask_app():
    flask_app.run(host='0.0.0.0', port=config.FLASK_PORT, debug=False, use_reloader=False)

def present_web_selection_page(all_candidates_data):
    selection_request_queue.queue.clear()
    selection_result_queue.queue.clear()
    selection_request_queue.put({"all_candidates": all_candidates_data})
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        access_url_local = f"http://127.0.0.1:{config.FLASK_PORT}/"
        access_url_network = f"http://{local_ip}:{config.FLASK_PORT}/"
        print("="*50)
        print("ACTION REQUIRED: Image Selection via Web Browser")
        print("Gathered candidates for all actors.")
        print(f"Open your web browser and go to:")
        print(f"  -> {access_url_local}")
        print(f"  OR (from another device on the same network):")
        print(f"  -> {access_url_network}")
        print(f"For EACH actor: Select the best image OR choose 'Skip'.")
        print("Click 'Submit All Selections' at the bottom when done.")
        print("Waiting for your submission in the browser...")
        print("="*50)
    except socket.gaierror:
        print(f"ACTION REQUIRED: Open http://127.0.0.1:{config.FLASK_PORT}/ in your browser.")
        print("Select images and click 'Submit All Selections'. Waiting...")
    try:
        all_selections = selection_result_queue.get(timeout=config.WEB_SELECTOR_TIMEOUT)
    except queue.Empty:
        logging.error("Timeout waiting for batch web selection.")
        try: selection_request_queue.get_nowait()
        except queue.Empty: pass
        return None
    logging.info("Received batch selection results via queue.")
    return all_selections
