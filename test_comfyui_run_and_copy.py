# test_comfyui_run_and_copy.py
import logging
import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import argparse
import random
import copy

# Import project modules
import config
import comfyui_interactions # Make sure this is implemented

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [Test Comfy Run] - %(message)s',
    handlers=[logging.StreamHandler()]
)

# --- Helper: Copy Source Face to ComfyUI Input --- (Same as in generate_images)
def copy_to_comfyui_input(source_file_path: Path):
    # ... (Same function - ensure config.COMFYUI_INPUT_DIR is correct) ...
    if not config.COMFYUI_INPUT_DIR or not config.COMFYUI_INPUT_DIR.is_dir(): return False
    try:
        destination_path = config.COMFYUI_INPUT_DIR / source_file_path.name
        shutil.copy2(source_file_path, destination_path)
        logging.info(f"Copied '{source_file_path.name}' to ComfyUI input: {destination_path}")
        return True
    except Exception as e:
        logging.error(f"Error copying file {source_file_path.name} to ComfyUI input: {e}")
        return False

# --- Test Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test running a ComfyUI workflow and copying the output.")
    parser.add_argument("--prompt", required=True, help="Text prompt for image generation.")
    parser.add_argument("--face_image", required=True, help="Path to the source face image.")
    parser.add_argument("--output_dir", required=True, help="Directory within Movie_Projects/... to save the final image.")
    parser.add_argument("--output_prefix", required=True, help="Filename prefix for ComfyUI output and final naming.")
    args = parser.parse_args()

    # --- Load Env Vars (Optional - needed if comfyui_interactions uses API keys) ---
    load_dotenv(dotenv_path=config.DOTENV_PATH)

    # --- Validate Inputs ---
    source_face_path = Path(args.face_image)
    project_char_output_dir = Path(args.output_dir)
    if not source_face_path.is_file():
        logging.error(f"Source face image not found: {source_face_path}")
        exit(1)
    if not project_char_output_dir.is_dir():
        logging.error(f"Output directory does not exist: {project_char_output_dir}. Please create it or run previous steps.")
        # Optionally create it: project_char_output_dir.mkdir(parents=True, exist_ok=True)
        exit(1)

    # --- Load Workflow ---
    workflow_template = comfyui_interactions.load_workflow_template(config.IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template:
        logging.error("Failed to load workflow template.")
        exit(1)

    # --- Prepare for ComfyUI ---
    logging.info("Preparing ComfyUI interaction...")
    if not copy_to_comfyui_input(source_face_path):
        logging.error("Failed to copy source face to ComfyUI input.")
        exit(1)

    current_workflow = copy.deepcopy(workflow_template)
    seed = random.randint(0, 2**32 - 1)

    # --- **MODIFY WORKFLOW INPUTS (Including Output Node)** ---
    inputs_to_set = {
        config.PROMPT_NODE_TITLE: {"text": args.prompt},
        config.SEED_NODE_TITLE: {"noise_seed": seed},
        config.FACE_NODE_TITLE: {"image": source_face_path.name}, # Just filename
        config.OUTPUT_PREFIX_NODE_TITLE: {
            "custom_text": args.output_prefix,
            "custom_directory": "",  # <--- OVERRIDE: Set to empty
            "date_directory": "false" # <--- OVERRIDE: Set to false
        }
    }
    if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
        logging.error("Failed to modify workflow inputs.")
        exit(1)
    # --- End Modification ---

    # --- Run Workflow ---
    logging.info("Running ComfyUI workflow...")
    comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow)

    # --- Process Result ---
    if comfyui_result_history:
        # --- Process BASE Image Output ---
        base_output_details = comfyui_interactions.get_output_details_from_history(
            comfyui_result_history, config.IMAGE_BASE_OUTPUT_SAVE_NODE_TITLE
        )
        if base_output_details and 'filename' in base_output_details:
            comfy_base_filename = base_output_details['filename']
            comfy_base_subfolder = base_output_details.get('subfolder', '')
            comfy_base_output_path = config.COMFYUI_OUTPUT_DIR / comfy_base_subfolder / comfy_base_filename
            project_base_filename = f"{config.BASE_IMAGE_PREFIX}{comfy_base_filename}"
            project_base_path = project_char_output_dir / project_base_filename
            if comfy_base_output_path.is_file():
                try:
                    shutil.copy2(comfy_base_output_path, project_base_path)
                    logging.info(f"Copied BASE image to project: {project_base_path}")
                    base_image_saved = True
                except Exception as e:
                    logging.error(f"Failed copy BASE image: {e}")
            else:
                logging.error(f"Base output file not found: {comfy_base_output_path}")
        else:
            logging.warning(f"Could not find output details for BASE image node.")

        # --- Process SWAPPED Image Output ---
        swapped_output_details = comfyui_interactions.get_output_details_from_history(
            comfyui_result_history, config.IMAGE_SWAPPED_OUTPUT_SAVE_NODE_TITLE
        )
        if swapped_output_details and 'filename' in swapped_output_details:
            comfy_swapped_filename = swapped_output_details['filename']
            comfy_swapped_subfolder = swapped_output_details.get('subfolder', '')
            comfy_swapped_output_path = config.COMFYUI_OUTPUT_DIR / comfy_swapped_subfolder / comfy_swapped_filename
            project_candidates_dir = project_char_output_dir / config.GENERATED_SWAPPED_CANDIDATES_FOLDER_NAME
            project_candidates_dir.mkdir(parents=True, exist_ok=True)
            project_swapped_filename = f"{config.SWAPPED_IMAGE_PREFIX}{comfy_swapped_filename}"
            project_swapped_path = project_candidates_dir / project_swapped_filename
            if comfy_swapped_output_path.is_file():
                try:
                    shutil.copy2(comfy_swapped_output_path, project_swapped_path)
                    logging.info(f"Copied SWAPPED image to project candidates: {project_swapped_path}")
                    swapped_image_saved = True
                except Exception as e:
                    logging.error(f"Failed copy SWAPPED image: {e}")
            else:
                logging.error(f"Swapped output file not found: {comfy_swapped_output_path}")
        else:
            logging.warning(f"Could not find output details for SWAPPED image node.")
    else:
        logging.error("ComfyUI workflow failed or timed out.")

    exit(1) # Indicate failure