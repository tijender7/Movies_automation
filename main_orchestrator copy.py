import logging
import threading
import time
import os
from pathlib import Path
from dotenv import load_dotenv
import argparse
import json
import shutil
import comfyui_interactions

# Import project modules
import config
import llm_interactions
import web_utils
import image_processing
import web_selector

# --- Load Environment Variables ---
load_dotenv(dotenv_path=config.DOTENV_PATH)
TAVILY_API_KEY = os.getenv(config.TAVILY_API_KEY_NAME)
GEMINI_API_KEY = os.getenv(config.GEMINI_API_KEY_NAME)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate futuristic movie vignettes.")
    parser.add_argument("-m", "--movie", required=True, help="Name of the movie")
    parser.add_argument("-t", "--theme", required=True, help="Futuristic theme (e.g., 'Cyberpunk Noir')")
    args = parser.parse_args()
    movie_name = args.movie
    selected_theme = args.theme

    logging.info("===== Orchestrator Script Started =====")
    logging.info(f"Movie: {movie_name}, Theme: {selected_theme}")

    # --- Create Project Directory ---
    project_name = f"{movie_name.replace(' ', '_')}_{selected_theme.replace(' ', '_')}_{int(time.time())}"
    project_path = config.PROJECTS_BASE_DIR / project_name
    characters_base_path = project_path / "characters"
    source_actors_path = project_path / config.SOURCE_ACTORS_FOLDER_NAME
    final_compilations_path = project_path / "final_compilations"
    try:
        project_path.mkdir(parents=True, exist_ok=True)
        characters_base_path.mkdir(exist_ok=True)
        source_actors_path.mkdir(exist_ok=True)
        final_compilations_path.mkdir(exist_ok=True)
        logging.info(f"Project directory structure ensured at: {project_path}")
    except OSError as e:
        logging.error(f"Failed to create project directories: {e}")
        exit()

    # --- Start Flask Server ---
    flask_thread = threading.Thread(target=web_selector.run_flask_app, daemon=True)
    flask_thread.start()
    logging.info(f"Flask server starting in background thread on port {config.FLASK_PORT}...")
    time.sleep(2)

    # ===== PHASE 1: Information & Asset Gathering =====
    logging.info("--- Starting Phase 1 ---")

    # 1. Get Structured Movie Info (Using Google)
    movie_info = llm_interactions.get_movie_info_google_search(movie_name, GEMINI_API_KEY)
    if not movie_info or not movie_info.get('main_characters_actors'):
        logging.error("Exiting: Failed to get valid structured movie info.")
        exit()
    project_metadata_path = project_path / "metadata.json"
    with open(project_metadata_path, 'w', encoding='utf-8') as f: json.dump(movie_info, f, indent=4)
    logging.info(f"Saved structured movie metadata to {project_metadata_path}")

    all_candidates_for_web = {}
    all_characters_data = {}

    logging.info("--- Gathering candidate images for all actors ---")
    for char_info in movie_info.get('main_characters_actors', []):
        character_name = char_info.get('character_name', 'Unknown Character')
        actor_name = char_info.get('actor_name')
        if not actor_name: continue

        logging.info(f"Processing actor: {actor_name} ({character_name})")
        char_name_sanitized = "".join(c for c in character_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        character_dir = characters_base_path / char_name_sanitized
        character_dir.mkdir(parents=True, exist_ok=True)

        # 2. Get Character Details (Ollama)
        char_details = llm_interactions.get_character_details(
            movie_name, character_name, actor_name, config.OLLAMA_MODEL
        )
        if char_details:
             all_characters_data[character_name] = char_details
             char_metadata_path = character_dir / "metadata.json"
             try:
                  with open(char_metadata_path, 'w', encoding='utf-8') as f: json.dump(char_details, f, indent=4)
                  logging.info(f"Saved character metadata to {char_metadata_path}")
             except IOError as e:
                  logging.error(f"Failed to save character metadata for {character_name}: {e}")

        # 3. Search Actor Images (Tavily)
        release_year = movie_info.get("release_year", "")
        actor_temp_image_dir = character_dir / config.TEMP_IMAGE_FOLDER_NAME
        actor_filtered_image_dir = character_dir / config.FILTERED_IMAGE_FOLDER_NAME
        search_query = (
             f'"{actor_name}" ({release_year}) OR "{actor_name}" 1970s '
             f'face portrait close-up OR headshot '
             f'-poster -group -cast -multiple -advertisement -logo -text -movie -scene '
             f'-ai -art -illustration -generated -render -drawing -painting -cgi'
            )

        _, downloaded_paths_in_temp = web_utils.perform_web_search(
            search_query, TAVILY_API_KEY,
            download_dir=actor_temp_image_dir,
            num_results=config.TAVILY_SEARCH_COUNT,
            search_for_images=True
        )

        valid_filtered_image_paths = []
        if downloaded_paths_in_temp:
             valid_filtered_image_paths = image_processing.filter_and_copy_single_face_images(
                 downloaded_paths_in_temp,
                 actor_filtered_image_dir,
                 required_faces=config.FACE_FILTER_REQUIRED_COUNT
             )

        # 5. Prepare data for web UI
        if valid_filtered_image_paths:
            candidates_list_for_actor = []
            base_serving_dir = config.PROJECTS_BASE_DIR.resolve()  # Base is Movie_Projects
            for img_path in valid_filtered_image_paths:
                try:
                    img_path_resolved = img_path.resolve()
                    relative_path_obj = img_path_resolved.relative_to(base_serving_dir)
                    relative_path_url = relative_path_obj.as_posix()  # e.g., sholay.../characters/.../image.jpg
                    candidates_list_for_actor.append({
                        "filename": img_path.name,
                        "full_path": str(img_path_resolved),
                        "relative_path": relative_path_url  # Use this path for the template
                    })
                except ValueError as e:
                    logging.warning(f"MAIN: Cannot make path relative for image serving (Base: {base_serving_dir}, Img: {img_path}): {e}. Skipping.")
            all_candidates_for_web[actor_name] = {
                "candidates": candidates_list_for_actor,
                "character_name": character_name,
                "movie_name": movie_name
            }
        else:
             logging.info(f"No single-face images found for {actor_name} after filtering.")
             all_candidates_for_web[actor_name] = {
                 "candidates": [],
                 "character_name": character_name,
                 "movie_name": movie_name
             }
        # Clean up temp downloads for this actor now
        if actor_temp_image_dir.exists():
             try: shutil.rmtree(actor_temp_image_dir)
             except OSError as e: logging.warning(f"Could not remove temp dir {actor_temp_image_dir}: {e}")

    # --- End of gathering loop ---
    logging.info(f"Finished gathering candidates for {len(all_candidates_for_web)} actors.")

    actor_image_map = {}
    if all_candidates_for_web:
        all_selections = web_selector.present_web_selection_page(all_candidates_for_web)
        if all_selections:
            logging.info("Processing batch selections...")
            # Process the results dictionary
            for actor_name, selected_value in all_selections.items():
                if selected_value != "SKIP":
                    try:
                        selected_path_from_web = Path(selected_value)
                        # Basic check: Does the file exist where it says it is?
                        if selected_path_from_web.is_file():
                             # Copy from filtered folder to final source_actors
                             source_actors_path = project_path / config.SOURCE_ACTORS_FOLDER_NAME
                             sanitized_name = "".join(c for c in actor_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
                             target_filename = f"{sanitized_name}{selected_path_from_web.suffix}"
                             final_target_path = source_actors_path / target_filename
                             shutil.copy(selected_path_from_web, final_target_path)
                             logging.info(f"Copied selected image for {actor_name} to {final_target_path}")
                             actor_image_map[actor_name] = str(final_target_path) # Store final path
                        else:
                            logging.error(f"Selected path for {actor_name} ({selected_path_from_web}) does not exist. Skipping copy.")
                            actor_image_map[actor_name] = None
                    except Exception as e:
                        logging.error(f"Error processing selection or copying file for {actor_name} ({selected_value}): {e}")
                        actor_image_map[actor_name] = None
                else:
                    logging.info(f"User skipped {actor_name}.")
                    actor_image_map[actor_name] = None # Mark as skipped
        else:
            logging.error("Web selection process failed or timed out.")
            # Handle case where selection failed entirely - maybe exit?

    else:
        logging.warning("No candidates gathered, skipping web selection step.")

    logging.info("--- Finished Phase 1 ---")
    logging.info(f"Final Actor Image Map: {actor_image_map}")

    # ===== PHASE 2: Futuristic Visual Generation =====
    logging.info("--- Starting Phase 2: Futuristic Visual Generation ---")

    generated_character_images = {} # Store paths to final swapped images
    all_flux_prompts = {} # Store generated prompts

    for actor_name, source_image_path_str in actor_image_map.items():
        if not source_image_path_str: continue # Skip if no source image

        # Find corresponding character name and details
        character_name = None
        character_data = None
        for char_info_meta in movie_info.get('main_characters_actors', []):
            if char_info_meta.get('actor_name') == actor_name:
                character_name = char_info_meta.get('character_name', 'Unknown Character')
                char_name_sanitized = "".join(c for c in character_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
                character_dir = characters_base_path / char_name_sanitized
                char_metadata_path = character_dir / "metadata.json"
                try:
                     with open(char_metadata_path, 'r', encoding='utf-8') as f:
                          character_data = json.load(f)
                except Exception as e:
                     logging.warning(f"Could not load character metadata for {character_name}: {e}")
                break # Found actor, stop searching

        if not character_name or not character_data:
             logging.warning(f"Missing character name or data for actor {actor_name}. Skipping prompt generation.")
             continue

        logging.info(f"\nGenerating Flux prompt for: {character_name} ({actor_name})")

        # 2a. Generate Flux Prompt (Ollama)
        flux_prompt = llm_interactions.generate_flux_prompt(
            character_name,
            actor_name,
            character_data,
            selected_theme, # The theme chosen by user at start
            config.OLLAMA_MODEL
        )

        if not flux_prompt:
             logging.error(f"Failed to generate Flux prompt for {character_name}. Skipping.")
             continue

        logging.info(f"Generated Flux prompt: {flux_prompt[:150]}...") # Log longer snippet
        all_flux_prompts[actor_name] = flux_prompt # Store the prompt

        # --- TODO: Add ComfyUI Calls Here ---
        # 2b. Run Flux Workflow (ComfyUI) - Needs implementation
        # logging.info("TODO: Implement ComfyUI Flux call")
        # base_image_save_path = run_flux_workflow(flux_prompt, ...) # Placeholder

        # 2c. Run ReActor Image Swap Workflow (ComfyUI) - Needs implementation
        # logging.info("TODO: Implement ComfyUI ReActor call")
        # final_swapped_image_path = run_reactor_workflow(base_image_save_path, source_image_path_str, ...) # Placeholder
        # generated_character_images[actor_name] = str(final_swapped_image_path)


    logging.info("--- Finished Phase 2 (Prompt Generation Stub) ---")
    logging.info(f"Generated Flux Prompts: {json.dumps(all_flux_prompts, indent=2)}")
    # logging.info(f"Generated Swapped Images Map: {generated_character_images}")

    # --- TODO: Call Phase 3 (Video Clips) ---
    # ...

    # --- TODO: Call Phase 4 (Compilation) ---
    # ...

    logging.info("Main script finished.")
    # ... (Print shutdown message) ...