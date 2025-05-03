import logging
import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import argparse
import random # For seed generation
import config
import copy
import comfyui_interactions
from datetime import datetime

import generate_prompts # <-- Add this import
import llm_interactions # We might need this if prompts weren't pre-generated (but they are)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [Generate Images] - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE, mode='a'), # Append to main log file
        logging.StreamHandler()
    ]
)

# --- Helper Function to Find Latest Project --- (Copied from generate_prompts.py)
def find_latest_project_dir(base_dir):
    # ... (Same code as in generate_prompts.py) ...
    try:
        base_path = Path(base_dir)
        if not base_path.is_dir(): return None
        subdirs = [d for d in base_path.iterdir() if d.is_dir()]
        if not subdirs: return None
        latest_dir = max(subdirs, key=lambda d: d.stat().st_mtime)
        return latest_dir
    except Exception as e:
        logging.error(f"Error finding latest project directory: {e}")
        return None

# --- Helper: Copy Source Face to ComfyUI Input ---
def copy_to_comfyui_input(source_file_path: Path):
    """Copies a file to the configured ComfyUI input directory."""
    if not config.COMFYUI_INPUT_DIR or not config.COMFYUI_INPUT_DIR.is_dir():
        logging.error(f"ComfyUI Input Directory not configured correctly or does not exist: {config.COMFYUI_INPUT_DIR}")
        return False
    try:
        destination_path = config.COMFYUI_INPUT_DIR / source_file_path.name
        shutil.copy2(source_file_path, destination_path) # Use copy2 to preserve metadata
        logging.info(f"Copied '{source_file_path.name}' to ComfyUI input: {destination_path}")
        return True
    except Exception as e:
        logging.error(f"Error copying file {source_file_path.name} to ComfyUI input: {e}")
        return False

# --- Main Logic ---
def run_image_generation(project_path: Path, theme: str):
    """Generates futuristic character images using ComfyUI for the specified project."""
    logging.info(f"--- Starting Image Generation for Project: {project_path.name} ---")
    logging.info(f"Using Theme: {theme}")

    # Load main movie metadata to get actor list in order
    metadata_path = project_path / "metadata.json"
    if not metadata_path.is_file():
        logging.error(f"Metadata file not found: {metadata_path}. Cannot proceed.")
        return False
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
    except Exception as e:
        logging.error(f"Error loading main metadata.json: {e}")
        return False

    characters_base_path = project_path / "characters"
    source_actors_path = project_path / config.SOURCE_ACTORS_FOLDER_NAME
    generated_images_count = 0
    failed_actors = []
    failed_actors_medium = []
    failed_actors_full = []
    medium_images_generated = 0
    full_images_generated = 0

    # --- DYNAMIC OUTPUT FOLDER SETUP (per run) ---
    run_id = datetime.now().strftime("%d%m%y_%H%M")  # e.g., '210421_1242'
    base_dir = f"dancers_from_workflow/{run_id}/original"
    swapped_dir = f"dancers_from_workflow/{run_id}/faceswapped"

    # Load the ComfyUI workflow template ONCE
    workflow_template = comfyui_interactions.load_workflow_template(config.IMAGE_WORKFLOW_TEMPLATE)
    if not workflow_template:
        logging.error("Failed to load ComfyUI workflow template. Halting.")
        return False

    # Loop through characters defined in the main metadata
    for char_info in movie_info.get('main_characters_actors', []):
        character_name = char_info.get('character_name')
        actor_name = char_info.get('actor_name')
        if not character_name or not actor_name: continue

        logging.info(f"\nProcessing Character for Image Generation: {character_name} ({actor_name})")
        char_name_sanitized = "".join(c for c in character_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        actor_name_sanitized = "".join(c for c in actor_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        character_dir = characters_base_path / char_name_sanitized
        character_dir.mkdir(parents=True, exist_ok=True)

        # --- Prepare workflow for this character ---
        current_workflow = copy.deepcopy(workflow_template)
        # Set dynamic output folders for this run
        for node_id, node_data in current_workflow.items():
            if node_data.get("_meta", {}).get("title") == "API_Base_Image_Output_SaveNode":
                node_data["inputs"]["custom_directory"] = base_dir
            elif node_data.get("_meta", {}).get("title") == "API_Image_Output_SaveNode":
                node_data["inputs"]["custom_directory"] = swapped_dir

        # 1. Find the prompt for this character
        flux_prompt = generate_prompts.find_prompt_for_character(project_path, character_name, theme)
        if not flux_prompt:
            logging.error(f"Prompt not found for character: {character_name}. Skipping.")
            failed_actors.append(actor_name)
            continue

        # 2. Find the source image for this actor or character
        source_image_found = None
        for ext in [".jpg", ".jpeg", ".png"]:
            # Try character name first
            candidate = source_actors_path / f"{char_name_sanitized}{ext}"
            if candidate.is_file():
                source_image_found = candidate
                break
            # Fallback to actor name
            candidate = source_actors_path / f"{actor_name_sanitized}{ext}"
            if candidate.is_file():
                source_image_found = candidate
                break
        if not source_image_found:
            logging.error(f"Source image not found for character: {character_name} or actor: {actor_name}. Skipping.")
            failed_actors.append(actor_name)
            continue

        # --- Generate MEDIUM/UPPER BODY Image ---
        prompt_medium_path = character_dir / "flux_prompt_medium.txt"
        output_medium_pattern = f"{config.SWAPPED_IMAGE_PREFIX}medium_{character_name.replace(' ','_')}__{actor_name.replace(' ','_')}__{theme.replace(' ','_')}*.png"
        existing_medium_outputs = list(character_dir.glob(output_medium_pattern))

        if existing_medium_outputs:
            logging.info(f"Medium swapped image already exists for {character_name}. Skipping.")
            medium_images_generated += 1
        elif prompt_medium_path.is_file():
            logging.info("--- Generating Medium/Upper Body Image ---")
            try:
                with open(prompt_medium_path, 'r', encoding='utf-8') as f: flux_prompt = f.read().strip()
                # --- Run ComfyUI Workflow for Medium ---
                if copy_to_comfyui_input(source_image_found):
                    output_prefix = f"medium_{character_name.replace(' ','_')}__{actor_name.replace(' ','_')}__{theme.replace(' ','_')}"
                    seed = random.randint(0, 2**32 - 1)
                    inputs_to_set = {
                        config.PROMPT_NODE_TITLE: {"text": flux_prompt},
                        config.SEED_NODE_TITLE: {"noise_seed": seed},
                        config.FACE_NODE_TITLE: {"image": source_image_found.name},
                        config.OUTPUT_PREFIX_NODE_TITLE: {"custom_text": output_prefix}
                        # Add other static modifications if needed
                    }
                    if comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                         comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow)
                         if comfyui_result_history:
                              output_image_details = comfyui_interactions.get_output_details_from_history(
                                  comfyui_result_history, config.IMAGE_OUTPUT_SAVE_NODE_TITLE
                              )
                              if output_image_details:
                                   project_output_filename = f"{config.SWAPPED_IMAGE_PREFIX}{output_prefix}_{output_image_details['filename']}"
                                   project_final_path = character_dir / project_output_filename
                                   if shutil.copy2(config.COMFYUI_OUTPUT_DIR / output_image_details['filename'], project_final_path):
                                       logging.info(f"Copied ComfyUI output '{output_image_details['filename']}' to project: {project_final_path}")
                                       medium_images_generated += 1
                                   else:
                                       logging.error(f"Failed to copy output file from ComfyUI for {character_name} (Medium).")
                                       failed_actors_medium.append(actor_name)
                              else:
                                   logging.error("Failed to get output details (Medium).")
                                   failed_actors_medium.append(actor_name)
                         else:
                              logging.error("ComfyUI workflow failed (Medium).")
                              failed_actors_medium.append(actor_name)
                    else:
                         logging.error("Failed to modify workflow (Medium).")
                         failed_actors_medium.append(actor_name)
                else:
                     logging.error("Failed to copy source image (Medium).")
                     failed_actors_medium.append(actor_name)
            except Exception as e:
                logging.error(f"Error during Medium image generation for {character_name}: {e}")
                failed_actors_medium.append(actor_name)
        else:
            logging.warning(f"Medium prompt not found for {character_name}. Skipping medium image generation.")
            failed_actors_medium.append(actor_name) # Count as failed if prompt missing

        # --- Generate FULL BODY Image ---
        prompt_full_path = character_dir / "flux_prompt_full.txt"
        output_full_pattern = f"{config.SWAPPED_IMAGE_PREFIX}full_{character_name.replace(' ','_')}__{actor_name.replace(' ','_')}__{theme.replace(' ','_')}*.png"
        existing_full_outputs = list(character_dir.glob(output_full_pattern))

        if existing_full_outputs:
             logging.info(f"Full Body swapped image already exists for {character_name}. Skipping.")
             full_images_generated += 1
        elif prompt_full_path.is_file():
            logging.info("--- Generating Full Body Image ---")
            try:
                with open(prompt_full_path, 'r', encoding='utf-8') as f: flux_prompt = f.read().strip()
                # --- Run ComfyUI Workflow for Full Body ---
                if copy_to_comfyui_input(source_image_found):
                    output_prefix = f"full_{character_name.replace(' ','_')}__{actor_name.replace(' ','_')}__{theme.replace(' ','_')}"
                    seed = random.randint(0, 2**32 - 1)
                    inputs_to_set = {
                        config.PROMPT_NODE_TITLE: {"text": flux_prompt},
                        config.SEED_NODE_TITLE: {"noise_seed": seed},
                        config.FACE_NODE_TITLE: {"image": source_image_found.name},
                        config.OUTPUT_PREFIX_NODE_TITLE: {"custom_text": output_prefix}
                        # Add other static modifications if needed
                    }
                    if comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set):
                         comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow)
                         if comfyui_result_history:
                              output_image_details = comfyui_interactions.get_output_details_from_history(
                                  comfyui_result_history, config.IMAGE_OUTPUT_SAVE_NODE_TITLE
                              )
                              if output_image_details:
                                   project_output_filename = f"{config.SWAPPED_IMAGE_PREFIX}{output_prefix}_{output_image_details['filename']}"
                                   project_final_path = character_dir / project_output_filename
                                   if shutil.copy2(config.COMFYUI_OUTPUT_DIR / output_image_details['filename'], project_final_path):
                                       logging.info(f"Copied ComfyUI output '{output_image_details['filename']}' to project: {project_final_path}")
                                       full_images_generated += 1
                                   else:
                                       logging.error(f"Failed to copy output file from ComfyUI for {character_name} (Full).")
                                       failed_actors_full.append(actor_name)
                              else:
                                   logging.error("Failed to get output details (Full).")
                                   failed_actors_full.append(actor_name)
                         else:
                              logging.error("ComfyUI workflow failed (Full).")
                              failed_actors_full.append(actor_name)
                    else:
                         logging.error("Failed to modify workflow (Full).")
                         failed_actors_full.append(actor_name)
                else:
                     logging.error("Failed to copy source image (Full).")
                     failed_actors_full.append(actor_name)
            except Exception as e:
                 logging.error(f"Error during Full Body image generation for {character_name}: {e}")
                 failed_actors_full.append(actor_name)
        else:
             logging.warning(f"Full Body prompt not found for {character_name}. Skipping full body image generation.")
             failed_actors_full.append(actor_name) # Count as failed if prompt missing

    # --- End Character Loop ---

    logging.info("--- Finished Image Generation ---")
    logging.info(f"Successfully generated/found medium images for {medium_images_generated} actors.")
    logging.info(f"Successfully generated/found full body images for {full_images_generated} actors.")
    if failed_actors_medium:
         logging.warning(f"Failed medium image generation for actors: {', '.join(failed_actors_medium)}")
    if failed_actors_full:
         logging.warning(f"Failed full body image generation for actors: {', '.join(failed_actors_full)}")
    return not failed_actors_medium and not failed_actors_full # Return True if all succeeded


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate futuristic character images using ComfyUI.")
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder. If not provided, uses the latest.")
    # Theme is now derived from folder name, not needed as argument
    args = parser.parse_args()

    # --- Load Environment Variables ---
    load_dotenv(dotenv_path=config.DOTENV_PATH)
    # API keys loaded here if comfyui_interactions needs them (unlikely for local URL)

    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        if not project_to_process.is_dir():
             logging.error(f"Provided project path does not exist: {project_to_process}")
             exit(1)
    else:
        logging.info("No project path provided, finding the latest project directory...")
        project_to_process = find_latest_project_dir(config.PROJECTS_BASE_DIR)
        if not project_to_process:
             logging.error(f"Could not find any project directory in {config.PROJECTS_BASE_DIR}")
             exit(1)
        logging.info(f"Using latest project directory: {project_to_process}")

    # Extract theme from folder name
    project_theme = generate_prompts.extract_theme_from_project_path(project_to_process) # Reuse function
    if not project_theme or project_theme == "Default Theme":
         logging.error("Could not reliably determine theme from project folder name. Halting.")
         exit(1)

    # --- Run the main logic ---
    success = run_image_generation(project_to_process, project_theme)

    if success:
         logging.info("Image generation completed successfully for all processed actors.")
         exit(0)
    else:
         logging.error("Image generation encountered errors for one or more actors.")
         exit(1)