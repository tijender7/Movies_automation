# main_orchestrator_v2.py
import logging
import threading
import time
import os
from pathlib import Path
from dotenv import load_dotenv
import json
import shutil
import sys # Import sys for exiting
import re # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< IMPORT RE HERE
import database_utils  # <-- IMPORT NEW MODULE

# Import project v2 modules
try:
    import config_v2 as config
    import llm_interactions_v2 as llm_interactions
    import web_utils_v2 as web_utils
    import image_processing_v2 as image_processing
    import web_selector_v2 as web_selector
    # Note: ComfyUI interactions aren't directly used in main_orchestrator
except ImportError as e:
    print(f"ERROR: Failed to import necessary v2 modules: {e}")
    sys.exit(1) # Exit if imports fail


# --- Load Environment Variables ---
load_dotenv(dotenv_path=config.DOTENV_PATH)
TAVILY_API_KEY = os.getenv(config.TAVILY_API_KEY_NAME)
TMDB_API_KEY = os.getenv(config.TMDB_API_KEY_NAME) # <-- Load TMDB Key
GEMINI_API_KEY = os.getenv(config.GEMINI_API_KEY_NAME)

# --- Logging Setup ---
log_file_path = Path(config.LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', # Added line number
        handlers=[
            logging.FileHandler(config.LOG_FILE, mode='a'), # Use mode 'a' to append
            logging.StreamHandler()
        ]
    )
except Exception as e:
    print(f"ERROR setting up logging: {e}")
    # Fallback to basic stream logging if file fails
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# --- Helper Function moved to the top --- >>>>>>>>>>>>>>>>>>>>>>>>>>>>>
def sanitize_name(name):
    """Sanitizes a name for file/folder usage."""
    if not isinstance(name, str):
        name = str(name)
    # Keep basic alphanumeric, underscore, space. Replace space with underscore.
    sanitized = "".join(c for c in name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    return sanitized if sanitized else "invalid_name"
# --- End Helper Function --- >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

# --- Function to get interactive input ---
def get_interactive_input():
    """Gets movie name and theme interactively from the user."""
    movie_name = input("Enter the movie name: ").strip()
    while not movie_name:
        print("Movie name cannot be empty.")
        movie_name = input("Enter the movie name: ").strip()

    themes = [
        "Cyberpunk Noir",
        "Solarpunk Utopia",
        "Biopunk Dystopia",
        "Post-Apocalyptic Dieselpunk",
        "Retro-Futurism (1950s Atom Age)",
        "Space Opera Galaxy",
        "Steampunk Mechanized World",
        "Nanopunk Microverse",
        "Clockpunk Renaissance",
        "Deep Sea Hydro-Futurism"
    ]

    print("\nSelect a futuristic theme:")
    for i, theme in enumerate(themes):
        print(f"{i + 1}. {theme}")
    print(f"{len(themes) + 1}. Enter a custom theme")

    selected_theme = None
    while selected_theme is None:
        try:
            choice = input(f"Enter number (1-{len(themes)+1}) or type your custom theme: ").strip()
            if not choice:
                print("Input cannot be empty.")
                continue

            # Try converting to number first
            try:
                choice_num = int(choice)
                if 1 <= choice_num <= len(themes):
                    selected_theme = themes[choice_num - 1]
                elif choice_num == len(themes) + 1:
                    custom_theme = input("Enter your custom theme: ").strip()
                    if custom_theme:
                        selected_theme = custom_theme
                    else:
                        print("Custom theme cannot be empty.")
                else:
                    print(f"Invalid number. Please enter between 1 and {len(themes)+1}.")
            except ValueError:
                # If not a number, treat as custom theme directly
                selected_theme = choice
                print(f"Using custom theme: {selected_theme}")

        except ValueError:
            print("Invalid input. Please enter a number or your custom theme name.")

    return movie_name, selected_theme

# --- Main Execution ---
if __name__ == "__main__":
    # --- ADD Interactive Input ---
    movie_name, selected_theme = get_interactive_input()
    # ---

    logging.info("===== Orchestrator Script Started =====")
    logging.info(f"Movie: {movie_name}, Theme: {selected_theme}")

    # --- Validate API Keys early ---
    if not TMDB_API_KEY: log.critical("TMDB_API_KEY not found in .env!"); exit(1)
    if not TAVILY_API_KEY: log.critical("TAVILY_API_KEY not found in .env!"); exit(1)
    if not GEMINI_API_KEY:
        logging.error(f"{config.GEMINI_API_KEY_NAME} not found in environment variables. Check .env file.")
        sys.exit(1)
    logging.debug("API keys found.")
    # ---

    # --- Create Project Directory ---
    project_timestamp = int(time.time())
    # Now sanitize_name is defined before this line <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    project_name_sanitized = f"{sanitize_name(movie_name)}_{sanitize_name(selected_theme)}_{project_timestamp}"
    project_path = config.PROJECTS_BASE_DIR / project_name_sanitized
    characters_base_path = project_path / config.CHARACTERS_FOLDER_NAME
    source_actors_path = project_path / config.SOURCE_ACTORS_FOLDER_NAME
    # final_compilations_path = project_path / "final_compilations" # This seems unused currently
    try:
        project_path.mkdir(parents=True, exist_ok=True)
        characters_base_path.mkdir(exist_ok=True)
        source_actors_path.mkdir(exist_ok=True)
        # final_compilations_path.mkdir(exist_ok=True)
        logging.info(f"Project directory structure ensured at: {project_path}")
    except OSError as e:
        logging.error(f"Failed to create project directories: {e}")
        sys.exit(1) # Exit if cannot create directories

    # --- Step 1: Get Core Movie Info from TMDb ---
    log.info(f"--- Fetching Movie Info from TMDb: {movie_name} ---")
    movie_info = database_utils.get_movie_details_tmdb(movie_name, TMDB_API_KEY)

    if not movie_info or not movie_info.get('main_characters_actors'):
        log.error(f"Exiting: Failed to get required movie info from TMDb for {movie_name}.")
        exit(1)
    log.info("Successfully retrieved core movie info from TMDb.")
    # --- End Step 1 ---

    # --- Save final metadata ---
    project_metadata_path = project_path / "metadata.json"
    try:
        with open(project_metadata_path, 'w', encoding='utf-8') as f:
            json.dump(movie_info, f, indent=2, ensure_ascii=False)
        log.info(f"Saved final metadata (from TMDb) to {project_metadata_path}")
    except IOError as e:
        log.error(f"Failed to save metadata: {e}")

    # --- Check Cast List ---
    if not movie_info.get('main_characters_actors'): log.error("No cast info found after TMDb. Exiting."); exit(1)

    # --- Proceed with Gathering Actor Images (Phase 1d) ---
    log.info("--- Gathering candidate images for all actors listed in metadata ---")
    character_actor_list = movie_info.get('main_characters_actors', [])
    total_actors_in_meta = len(character_actor_list)
    log.info(f"Found {total_actors_in_meta} character/actor pairs in metadata.")

    all_candidates_for_web = {}
    all_characters_data = {}
    processed_actors_count = 0

    logging.info("--- Gathering candidate images for all actors listed in metadata ---")
    character_actor_list = movie_info.get('main_characters_actors', [])
    total_actors_in_meta = len(character_actor_list)
    logging.info(f"Found {total_actors_in_meta} character/actor pairs in metadata.")

    for char_info in character_actor_list:
        # Add safety checks for dict structure
        if not isinstance(char_info, dict):
            log.warning(f"Skipping invalid entry in 'main_characters_actors': {char_info}")
            continue
        character_name = char_info.get('character_name', 'Unknown Character')
        actor_name = char_info.get('actor_name')

        if not actor_name:
            log.warning(f"Skipping entry with missing actor name for character '{character_name}'.")
            continue

        processed_actors_count += 1
        log.info(f"\nProcessing Actor {processed_actors_count}/{total_actors_in_meta}: {actor_name} (Character: {character_name})")
        char_name_sanitized = sanitize_name(character_name)
        actor_name_sanitized = sanitize_name(actor_name) # Use this for filenames related to actor
        character_dir = characters_base_path / char_name_sanitized
        character_dir.mkdir(parents=True, exist_ok=True) # Ensure character subdir exists

        # 2. Get Character Details (Ollama)
        # Note: Ensure your Ollama model supports this kind of detailed extraction task
        log.info(f"  Getting character details for '{character_name}'...")
        char_details = llm_interactions.get_character_details(
            movie_name, character_name, actor_name, config.OLLAMA_MODEL
        )
        if char_details:
             all_characters_data[character_name] = char_details
             char_metadata_path = character_dir / "metadata.json"
             try:
                  with open(char_metadata_path, 'w', encoding='utf-8') as f: json.dump(char_details, f, indent=4, ensure_ascii=False)
                  logging.info(f"  Saved character metadata to {char_metadata_path}")
             except IOError as e:
                  logging.error(f"  Failed to save character metadata for {character_name}: {e}")
                  # Decide if this is fatal - perhaps continue without details?
        else:
             log.warning(f"  Failed to retrieve character details for {character_name}. Proceeding without them.")
             all_characters_data[character_name] = {} # Add empty dict to avoid errors later if code expects the key

        # 3. Search Actor Images (Tavily)
        # --- Get Release Year for Context ---
        release_year = movie_info.get("release_year", "")
        # --- Determine Decade ---
        decade_str = ""
        try:
            release_year_int = int(release_year)
            decade_start = (release_year_int // 10) * 10
            decade_str = f"{decade_start}s" # e.g., "1970s"
        except (ValueError, TypeError):
            decade_str = ""
        # ---

        # --- Classic Search Query for Era Authenticity (from main_orchestrator.py) ---
        search_query = (
            f'"{actor_name}" ({release_year}) OR "{actor_name}" {decade_str} '
            f'face portrait close-up OR headshot '
            f'-poster -group -cast -multiple -advertisement -logo -text -movie -scene '
            f'-ai -art -illustration -generated -render -drawing -painting -cgi'
        )
        log.info(f"  Using Era-Specific Image Search Query: {search_query}")

        actor_temp_image_dir = character_dir / config.TEMP_IMAGE_FOLDER_NAME
        actor_filtered_image_dir = character_dir / config.FILTERED_IMAGE_FOLDER_NAME

        web_results, downloaded_paths_in_temp = web_utils.perform_web_search(
            search_query, TAVILY_API_KEY,
            download_dir=actor_temp_image_dir,
            num_results=config.TAVILY_SEARCH_COUNT,
            search_for_images=True
        )

        valid_filtered_image_paths_in_target = []
        if downloaded_paths_in_temp:
             log.info(f"  Filtering {len(downloaded_paths_in_temp)} downloaded images for single face...")
             valid_filtered_image_paths_in_target = image_processing.filter_and_copy_single_face_images(
                 downloaded_paths_in_temp,
                 actor_filtered_image_dir, # Target is the filtered dir
                 required_faces=config.FACE_FILTER_REQUIRED_COUNT
             )
             log.info(f"  Found {len(valid_filtered_image_paths_in_target)} valid single-face images.")
        else:
             log.info(f"  No images downloaded for {actor_name}.")

        # 5. Prepare data for web UI (using paths in filtered folder)
        if valid_filtered_image_paths_in_target:
            candidates_list_for_actor = []
            # The base path for serving images via Flask should be the project's base dir
            base_serving_dir = config.PROJECTS_BASE_DIR.resolve()
            log.debug(f"  Base serving directory for web selector: {base_serving_dir}")

            for img_path_str in valid_filtered_image_paths_in_target:
                try:
                    img_path_obj = Path(img_path_str) # Path in filtered folder
                    img_path_resolved = img_path_obj.resolve()

                    # Make path relative TO THE SERVING BASE (PROJECTS_BASE_DIR)
                    relative_path_obj = img_path_resolved.relative_to(base_serving_dir)
                    relative_path_url = relative_path_obj.as_posix() # Use forward slashes for URL
                    # e.g., Sholay_Cyberpunk.../characters/Gabbar_Singh/filtered.../image.jpg

                    candidates_list_for_actor.append({
                        "filename": img_path_obj.name,
                        # Full path needed by selection handler to find file to copy
                        "full_path": str(img_path_resolved),
                        # Relative path used in HTML template src attribute
                        "relative_path": relative_path_url
                    })
                    log.debug(f"    Added candidate: {img_path_obj.name} (rel: {relative_path_url})")

                except ValueError as e:
                    # This error means the image path is not within the base serving path
                    logging.warning(f"  Cannot make path relative for web serving. Img: '{img_path_resolved}', Base: '{base_serving_dir}'. Error: {e}. Skipping image.")
                except Exception as e:
                    logging.warning(f"  Error processing candidate image path '{img_path_str}': {e}")

            all_candidates_for_web[actor_name] = {
                "candidates": candidates_list_for_actor,
                "character_name": character_name,
                "movie_name": movie_name # Pass movie name for display
            }
            log.info(f"  Prepared {len(candidates_list_for_actor)} candidates for web selection for {actor_name}.")
        else:
             log.info(f"  No valid single-face images found/prepared for {actor_name} after filtering.")
             # Still add entry so user can explicitly skip them if desired
             all_candidates_for_web[actor_name] = {
                 "candidates": [],
                 "character_name": character_name,
                 "movie_name": movie_name
             }

        # Clean up temp download directory for this actor immediately
        if actor_temp_image_dir.exists():
             log.debug(f"  Removing temporary download directory: {actor_temp_image_dir}")
             try:
                 shutil.rmtree(actor_temp_image_dir)
             except OSError as e:
                 logging.warning(f"  Could not remove temp dir {actor_temp_image_dir}: {e}")

    # --- End of gathering loop ---
    total_actors_processed = len(all_candidates_for_web)
    logging.info(f"\nFinished gathering candidates for {total_actors_processed} actors.")
    if total_actors_processed == 0:
        logging.error("No actors were processed successfully. Check metadata or API errors.")
        sys.exit(1)

    # --- Phase 1b: Web Selection ---
    actor_image_map = {} # Stores {actor_name: path_to_final_source_image}
    if all_candidates_for_web:
        # Call the web selector presentation function
        all_selections = web_selector.present_web_selection_page(all_candidates_for_web)

        if all_selections:
            logging.info("Processing web selections...")
            # Process the results dictionary: {actor_name: full_path_or_SKIP}
            for actor_name, selected_value in all_selections.items():
                if selected_value != "SKIP":
                    try:
                        # selected_value is the 'full_path' from the candidate dict
                        selected_path_from_filtered = Path(selected_value)

                        # Double-check the file exists at the path provided by the web UI
                        if selected_path_from_filtered.is_file():
                            # Define the FINAL destination in source_actors
                            # Use the sanitized actor name for the filename
                            actor_name_sanitized = sanitize_name(actor_name)
                            target_filename = f"{actor_name_sanitized}{selected_path_from_filtered.suffix}"
                            final_target_path = source_actors_path / target_filename

                            # --- Copy from FILTERED folder to FINAL source_actors folder ---
                            shutil.copy2(selected_path_from_filtered, final_target_path)
                            logging.info(f"Copied selected image for {actor_name} TO final location: {final_target_path}")
                            actor_image_map[actor_name] = str(final_target_path) # Store final path

                            # --- Optional: Clean up the filtered folder for this actor ---
                            # parent_filtered_dir = selected_path_from_filtered.parent
                            # log.debug(f"Removing filtered image directory: {parent_filtered_dir}")
                            # try:
                            #     shutil.rmtree(parent_filtered_dir)
                            # except OSError as e:
                            #     logging.warning(f"Could not remove filtered dir {parent_filtered_dir}: {e}")
                            # --- End Optional Cleanup ---

                        else:
                            logging.error(f"Selected path '{selected_path_from_filtered}' for {actor_name} provided by web UI does not exist. Skipping copy.")
                            actor_image_map[actor_name] = None # Mark as failed/missing
                    except Exception as e:
                        logging.error(f"Error processing selection or copying file for {actor_name} (Value: '{selected_value}'): {e}", exc_info=True)
                        actor_image_map[actor_name] = None # Mark as failed
                else:
                    logging.info(f"User skipped actor: {actor_name}. No source image saved.")
                    actor_image_map[actor_name] = None # Mark as skipped

            # --- Optional: Cleanup empty filtered folders for skipped actors ---
            # Iterate through all_candidates_for_web again
            # If actor was skipped and had candidates, find their filtered dir and remove it
            # --- End Optional Cleanup ---

        else:
            logging.error("Web selection process failed or timed out. Cannot proceed without source images.")
            sys.exit(1) # Exit if selection fails critically

    else:
        # This case should technically not be reached if total_actors_processed > 0
        logging.warning("No candidates were prepared for web selection, skipping step.")


    logging.info("--- Finished Phase 1 ---")
    logging.info(f"Final Actor Source Image Map (Paths in {source_actors_path}):")
    log.info(json.dumps(actor_image_map, indent=2))

    actors_with_source_images = sum(1 for path in actor_image_map.values() if path is not None)
    if actors_with_source_images == 0:
        logging.error("No source images were selected or successfully copied. Cannot proceed to image generation.")
        sys.exit(1)

    print(f"\nPhase 1 Complete. {actors_with_source_images} actor source images saved.")
    print(f"Project Path: {project_path}")
    print("You should now run: python generate_prompts_v2.py") # Use v2 script name
    print("Optionally stop the Flask server (Ctrl+C in this window) if not needed immediately.")

    # ===== PHASE 2, 3, 4 etc. are now separate scripts =====
    # main_orchestrator is now only responsible for Phase 1 setup and source selection.

    # Keep the Flask server running in the background if needed by subsequent steps immediately,
    # otherwise prompt the user they can stop it. Since the next step is `generate_prompts`,
    # the web server isn't needed yet.
    # Consider adding a prompt here to ask if the user wants to keep the server running or exit.
    # For simplicity now, let it run; user can Ctrl+C.

    # Keep the main thread alive while the Flask thread runs (if needed for longer interactions)
    # Or just exit here, since the main task is done. Exiting is simpler.
    logging.info("Main orchestrator script finished Phase 1.")
    exit(0) # Exit successfully after Phase 1

# --- Note: The sanitize_name function is now defined near the top ---