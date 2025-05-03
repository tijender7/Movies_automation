# generate_video_prompts.py
import logging
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import argparse
import re # For parsing filenames if needed later

# Import project modules
try:
    import config
    import llm_interactions # Needs the new generate_video_prompt_from_image
    from generate_prompts import extract_theme_from_project_path # Reuse helpers
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules: {e}")
    print("Make sure config.py, llm_interactions.py, and generate_prompts.py exist and are correct.")
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


# --- Main Logic ---
def find_latest_api_outputs_dir(api_outputs_base_dir: Path):
    """
    Find the latest folder (by creation time) in the API_OUTPUTS directory.
    Returns the Path to the latest folder, or None if none found.
    """
    if not api_outputs_base_dir.is_dir():
        log.error(f"API_OUTPUTS base directory not found: {api_outputs_base_dir}")
        return None
    subdirs = [d for d in api_outputs_base_dir.iterdir() if d.is_dir()]
    if not subdirs:
        log.error(f"No subdirectories found in API_OUTPUTS base directory: {api_outputs_base_dir}")
        return None
    latest_dir = max(subdirs, key=lambda d: d.stat().st_ctime)
    return latest_dir


def find_latest_project_dir(projects_base_dir: Path):
    """
    Find the latest folder (by creation time) in the Movie_Projects directory.
    Returns the Path to the latest folder, or None if none found.
    """
    if not projects_base_dir.is_dir():
        log.error(f"Movie_Projects base directory not found: {projects_base_dir}")
        return None
    subdirs = [d for d in projects_base_dir.iterdir() if d.is_dir()]
    if not subdirs:
        log.error(f"No subdirectories found in Movie_Projects base directory: {projects_base_dir}")
        return None
    latest_dir = max(subdirs, key=lambda d: d.stat().st_ctime)
    return latest_dir


def run_video_prompt_generation(api_outputs_base_dir: Path, projects_base_dir: Path):
    latest_api_output_dir = find_latest_api_outputs_dir(api_outputs_base_dir)
    log.info(f"Selected API_OUTPUTS folder: {latest_api_output_dir}")
    if not latest_api_output_dir:
        log.error("Could not find a valid API_OUTPUTS folder.")
        return False

    latest_project_dir = find_latest_project_dir(projects_base_dir)
    log.info(f"Selected Movie_Projects folder: {latest_project_dir}")
    if not latest_project_dir:
        log.error("Could not find a valid Movie_Projects folder.")
        return False

    metadata_path = latest_project_dir / "metadata.json"
    approval_log_path = latest_api_output_dir / "approved_selections.json"
    approved_images_dir = latest_api_output_dir / "approved_images"

    if not metadata_path.is_file():
        log.error(f"Metadata not found: {metadata_path}")
        return False
    if not approval_log_path.is_file():
        log.error(f"Approval log not found: {approval_log_path}. Run approve_images.py first.")
        return False
    if not approved_images_dir.is_dir():
        log.error(f"Approved images folder not found: {approved_images_dir}")
        return False

    print(f"API_OUTPUTS folder selected: {latest_api_output_dir}")
    print(f"Movie_Projects folder selected: {latest_project_dir}")
    print(f"metadata.json path: {metadata_path}")
    print(f"approved_selections.json path: {approval_log_path}")
    print(f"approved_images folder: {approved_images_dir}")

    try:
        with open(metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
        with open(approval_log_path, 'r', encoding='utf-8') as f: approved_selections = json.load(f)
        movie_name = movie_info.get("movie_name", latest_api_output_dir.name.split('_')[0])
        theme = extract_theme_from_project_path(latest_api_output_dir)
        log.info(f"Loaded metadata and approvals for '{movie_name}' ('{theme}' theme).")
    except Exception as e:
        log.error(f"Error loading metadata or approval file: {e}"); return False

    if not theme or theme == "Default Theme":
        log.error("Could not determine theme from project path.")
        return False

    # Build Actor->Character map from metadata
    actor_char_map = {
        item['actor_name']: item['character_name']
        for item in movie_info.get('main_characters_actors', []) if item.get('actor_name') and item.get('character_name')
    }

    # Use characters and prompts from the project folder, output to API_OUTPUTS
    characters_base_path = latest_project_dir / "characters"
    prompts_generated_count = 0
    actors_failed = []

    # --- Loop through ACTORS in the approval dictionary ---
    for actor_name, approved_paths_list in approved_selections.items():
        if not isinstance(approved_paths_list, list) or not approved_paths_list:
            log.info(f"No images approved or invalid format for actor '{actor_name}'. Skipping.")
            continue

        character_name = actor_char_map.get(actor_name)
        if not character_name:
            log.warning(f"Could not find character name for actor '{actor_name}' in metadata. Skipping.")
            actors_failed.append(actor_name)
            continue

        log.info(f"\nProcessing approved images for: {character_name} ({actor_name})")
        char_name_sanitized = "".join(c for c in character_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        # Always use project folder for character data
        character_dir = characters_base_path / char_name_sanitized
        char_metadata_path = character_dir / "metadata.json"
        # Output directory for video prompts
        output_character_dir = latest_api_output_dir / "characters" / char_name_sanitized
        output_character_dir.mkdir(parents=True, exist_ok=True)

        # Load character details ONCE per character
        character_data = {}
        if char_metadata_path.is_file():
            try:
                with open(char_metadata_path, 'r', encoding='utf-8') as f: character_data = json.load(f)
            except Exception as e: log.warning(f"Could not load character metadata for {character_name}: {e}")
        else: log.warning(f"Character metadata not found for {character_name}")

        # --- Loop through EACH approved image path for this actor ---
        for approved_img_path_str in approved_paths_list:
            approved_img_path = Path(approved_img_path_str)

            if not approved_img_path.is_file():
                log.warning(f"Approved image file path does not exist: {approved_img_path}. Skipping.")
                continue

            # Determine shot type from filename (heuristic)
            shot_type = "unknown"
            if "_medium_" in approved_img_path.name: shot_type = "medium"
            elif "_full_" in approved_img_path.name: shot_type = "full"
            log.info(f"  Processing Approved Image ({shot_type}): {approved_img_path.name}")

            # Load original Flux prompt from project folder
            original_prompt_path = character_dir / f"flux_prompt_{shot_type}.txt"
            original_flux_prompt = ""
            if original_prompt_path.is_file():
                try:
                    with open(original_prompt_path, 'r', encoding='utf-8') as f: original_flux_prompt = f.read().strip()
                except Exception as e: log.warning(f"Could not read original flux prompt {original_prompt_path}: {e}")
            else: log.warning(f"Original flux prompt not found: {original_prompt_path}")

            # --- Generate WanVideo Prompts (Simpler) ---
            wan_pos_filename = f"wan_video_prompt_positive_{approved_img_path.stem}.txt" # New prefix
            wan_neg_filename = f"wan_video_prompt_negative_{approved_img_path.stem}.txt" # New prefix
            wan_pos_save_path = output_character_dir / wan_pos_filename
            wan_neg_save_path = output_character_dir / wan_neg_filename

            if wan_pos_save_path.exists() and wan_neg_save_path.exists():
                 log.info(f"    WanVideo prompts (pos/neg) already exist. Skipping.")
                 prompts_generated_count += 1
            else:
                 log.info(f"    Generating WanVideo prompts...")
                 wan_pos_prompt, wan_neg_prompt = llm_interactions.generate_wan_video_prompt_from_image(
                     approved_img_path, movie_name, theme, character_name, actor_name,
                     character_data, # No original_flux_prompt for WanVideo
                     config.OLLAMA_MODEL
                 )
                 if wan_pos_prompt and wan_neg_prompt:
                      prompts_saved_ok = True
                      try: # Save Positive
                           with open(wan_pos_save_path, 'w', encoding='utf-8') as f: f.write(wan_pos_prompt)
                           log.info(f"      Saved Wan Positive prompt to: {wan_pos_save_path}")
                      except IOError as e: log.error(f"      Failed save Wan Positive: {e}"); prompts_saved_ok = False
                      try: # Save Negative
                           with open(wan_neg_save_path, 'w', encoding='utf-8') as f: f.write(wan_neg_prompt)
                           log.info(f"      Saved Wan Negative prompt to: {wan_neg_save_path}")
                      except IOError as e: log.error(f"      Failed save Wan Negative: {e}"); prompts_saved_ok = False

                      if prompts_saved_ok: prompts_generated_count += 1
                      else: actors_failed.append(actor_name)
                 else:
                      log.error(f"    Failed generate WanVideo prompt pair for {approved_img_path.name}.")
                      actors_failed.append(actor_name)

            # --- Generate LTX Video Prompt (Keep as before) ---
            ltx_prompt_filename = f"ltx_video_prompt_{approved_img_path.stem}.txt"
            ltx_prompt_save_path = output_character_dir / ltx_prompt_filename
            if ltx_prompt_save_path.exists():
                log.info(f"    LTX video prompt already exists: {ltx_prompt_save_path.name}. Skipping.")
            else:
                log.info(f"    Generating LTX video prompt...")
                ltx_video_prompt = llm_interactions.generate_ltx_prompt_from_image(
                    approved_img_path, movie_name, theme, character_name, actor_name,
                    character_data, original_flux_prompt, config.OLLAMA_MODEL
                )
                if ltx_video_prompt:
                    try:
                        with open(ltx_prompt_save_path, 'w', encoding='utf-8') as f: f.write(ltx_video_prompt)
                        log.info(f"      Saved LTX video prompt to: {ltx_prompt_save_path}")
                    except IOError as e: log.error(f"      Failed to save LTX video prompt: {e}"); actors_failed.append(actor_name)
                else:
                    log.error(f"    Failed to generate LTX video prompt for {approved_img_path.name}.")
                    actors_failed.append(actor_name)
        # --- End loop through approved images for one actor ---
    # --- End loop through all actors ---

    log.info("--- Finished Video Prompt Generation ---")
    log.info(f"Generated/Found video prompts for {prompts_generated_count} approved images.")
    unique_failed = list(set(actors_failed))
    if unique_failed:
        log.warning(f"Failed video prompt generation for actors: {', '.join(unique_failed)}")
        return False
    else:
        log.info("Video prompt generation completed successfully for all processed actors/images.")
        return True


# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate video prompts for approved character images.")
    parser.add_argument("-a", "--api_outputs_base_dir", default="H:/dancers_content/API_OUTPUTS", help="Path to the API_OUTPUTS base directory.")
    parser.add_argument("-p", "--projects_base_dir", default="H:/projects/Movie_trailer/movie_vignette_generator/Movie_Projects", help="Path to the Movie_Projects base directory.")
    args = parser.parse_args()

    load_dotenv(dotenv_path=config.DOTENV_PATH)

    api_outputs_base_dir = Path(args.api_outputs_base_dir)
    projects_base_dir = Path(args.projects_base_dir)
    success = run_video_prompt_generation(api_outputs_base_dir, projects_base_dir)

    if success:
        log.info("Script finished successfully.")
        exit(0)
    else:
        log.error("Script finished with errors.")
        exit(1)