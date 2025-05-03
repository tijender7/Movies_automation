# comfyui_interactions.py
import websocket # pip install websocket-client
import uuid
import json
import urllib.request
import urllib.parse
import logging
import time
from pathlib import Path
import copy
import random
import re

import config  # Import configuration

# --- Helper: Find Node ID ---
def find_node_id_by_title(workflow, title, wf_name="workflow"):
    """Finds the node ID in a workflow dict by its _meta.title field."""
    for node_id, node_data in workflow.items():
        if isinstance(node_data, dict) and node_data.get("_meta", {}).get("title") == title:
            logging.debug(f"Found node by title '{title}' in {wf_name}: ID {node_id} (Class: {node_data.get('class_type', 'N/A')})")
            return node_id
    logging.warning(f"Node not found by title '{title}' in {wf_name}.")
    return None

# --- Helper: Modify Workflow Inputs ---
def modify_workflow_inputs(workflow, title_value_map):
    """Modifies multiple inputs based on a {node_title: {input_name: value}} map."""
    success = True
    for node_title, inputs_to_set in title_value_map.items():
        node_id = find_node_id_by_title(workflow, node_title)
        if not node_id:
            logging.error(f"Cannot modify inputs: Node title '{node_title}' not found.")
            success = False
            continue
        node_data = workflow.get(node_id, {})
        node_inputs = node_data.get("inputs")
        if not isinstance(node_inputs, dict):
            logging.error(f"Node '{node_title}' (ID: {node_id}) has no 'inputs' dictionary.")
            success = False
            continue
        for input_name, new_value in inputs_to_set.items():
            if input_name in node_inputs:
                node_inputs[input_name] = new_value
                logging.info(f"Modified input '{input_name}' for node '{node_title}' (ID: {node_id})")
            else:
                logging.warning(f"Input '{input_name}' not found in node '{node_title}' (ID: {node_id}). Skipping modification.")
    return success

# --- Helper: Load Template ---
def load_workflow_template(template_filename):
    template_path = config.WORKFLOW_TEMPLATE_DIR / template_filename
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading workflow {template_path}: {e}")
        return None

# --- Helper: Queue Prompt ---
def queue_prompt(prompt_workflow, client_id):
    try:
        payload = {"prompt": prompt_workflow, "client_id": client_id}
        # Log the full workflow JSON payload before sending
        logging.info("[DEBUG] Sending workflow to ComfyUI:\n" + json.dumps(payload, indent=2))
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(f"{config.COMFYUI_URL}/prompt", data=data, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req) as response:
                response_data = json.load(response)
            return response_data
        except urllib.error.HTTPError as http_err:
            # Log the response body for HTTP errors (e.g., 400 Bad Request)
            error_body = http_err.read().decode('utf-8', errors='replace')
            logging.error(f"HTTPError from ComfyUI: {http_err.code} - {http_err.reason}\nResponse body: {error_body}")
            return None
    except Exception as e:
        logging.error(f"Error queuing prompt: {e}")
        return None

# --- Helper: Get History ---
def get_history(prompt_id):
    try:
        history_url = f"{config.COMFYUI_URL}/history/{prompt_id}"
        with urllib.request.urlopen(history_url) as response:
            history_data = json.load(response)
        return history_data.get(prompt_id, {})
    except Exception as e:
        logging.error(f"Error getting history: {e}")
        return None

# --- Helper: Get Output File Details from History ---
def get_output_details_from_history(history, target_node_title):
    """Parses history to find output details from the specifically titled node."""
    if not history or not history.get('outputs'):
        logging.warning("Cannot get outputs: History data missing or malformed.")
        return None
    prompt_workflow_executed = history.get('prompt', [None, None, {}])[2]  # Prompt is usually 3rd item in list
    target_node_id = find_node_id_by_title(prompt_workflow_executed, target_node_title)
    if not target_node_id:
        logging.error(f"Cannot get outputs: Node title '{target_node_title}' not found in executed prompt workflow.")
        return None
    node_output = history['outputs'].get(target_node_id)
    if not node_output:
        logging.warning(f"No output found in history for node '{target_node_title}' (ID: {target_node_id}).")
        return None
    # Return the whole output node (not just images[0]) for flexible use
    return node_output

# --- Helper: Sanitize Filename ---
def sanitize_filename(name):
    """Sanitize a string to be safe for filenames: replace non-alphanum/underscore with _"""
    return re.sub(r'[^A-Za-z0-9_]', '_', name.replace(' ', '_'))

# --- MAIN WORKFLOW RUNNER (Generalized - can be split later) ---
def run_comfyui_workflow(workflow_api_json, timeout=300):
    """Queues prompt, waits, returns output details dict or None."""
    client_id = str(uuid.uuid4())
    start_time = time.time()
    prompt_response = queue_prompt(workflow_api_json, client_id)
    if not prompt_response or 'prompt_id' not in prompt_response:
        return None
    prompt_id = prompt_response['prompt_id']
    logging.info(f"Waiting for ComfyUI execution for prompt {prompt_id}...")
    while time.time() - start_time < timeout:
        history = get_history(prompt_id)
        if history:
            logging.info(f"History received for prompt {prompt_id}.")
            return history
        time.sleep(2)
    logging.error(f"Timeout ({timeout}s) waiting for ComfyUI execution for prompt {prompt_id}.")
    return None

# --- Specialized Runners ---
def run_comfyui_image_workflow(prompt, output_prefix, face_image_path=None, seed=None, template_filename=None):
    """Runs the image workflow with specified arguments. Returns output file details or None."""
    if template_filename is None:
        template_filename = config.IMAGE_WORKFLOW_TEMPLATE
    workflow = load_workflow_template(template_filename)
    if workflow is None:
        return None
    # Prepare node modifications
    title_value_map = {
        config.PROMPT_NODE_TITLE: {'prompt': prompt},
        config.OUTPUT_PREFIX_NODE_TITLE: {'prefix': output_prefix},
    }
    if face_image_path:
        title_value_map[config.FACE_NODE_TITLE] = {'image': str(face_image_path)}
    if seed is not None:
        title_value_map[config.SEED_NODE_TITLE] = {'seed': seed}
    modify_workflow_inputs(workflow, title_value_map)
    # Run the workflow
    history = run_comfyui_workflow(workflow)
    if not history:
        return None
    return get_output_details_from_history(history, config.IMAGE_OUTPUT_SAVE_NODE_TITLE)

def run_comfyui_video_workflow(start_image_path, output_prefix, prompt=None, seed=None, template_filename=None):
    """Runs the video workflow with specified arguments. Returns output file details or None."""
    if template_filename is None:
        template_filename = config.VIDEO_WORKFLOW_TEMPLATE
    workflow = load_workflow_template(template_filename)
    if workflow is None:
        return None
    # Prepare node modifications
    title_value_map = {
        config.VIDEO_START_IMAGE_NODE_TITLE: {'image': str(start_image_path)},
        config.OUTPUT_PREFIX_NODE_TITLE: {'prefix': output_prefix},
    }
    if prompt:
        title_value_map[config.PROMPT_NODE_TITLE] = {'prompt': prompt}
    if seed is not None:
        title_value_map[config.SEED_NODE_TITLE] = {'seed': seed}
    modify_workflow_inputs(workflow, title_value_map)
    # Run the workflow
    history = run_comfyui_workflow(workflow)
    if not history:
        return None
    return get_output_details_from_history(history, config.VIDEO_OUTPUT_SAVE_NODE_TITLE)