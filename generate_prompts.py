# generate_prompts.py
import logging
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import argparse
import re # Import regular expressions for parsing

# Import necessary modules
import config
import llm_interactions

# --- Logging Setup --- (Keep as before)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [Generate Prompts] - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE, mode='a'),
        logging.StreamHandler()
    ]
)

# --- Helper Function to Find Latest Project --- 
def find_latest_project_dir(base_dir):
    """
    Finds the latest (most recently modified) project directory in the given base directory.
    Returns a Path object or None if not found.
    """
    try:
        base = Path(base_dir)
        if not base.is_dir():
            logging.error(f"Base directory does not exist: {base_dir}")
            return None
        # List all subdirectories
        subdirs = [d for d in base.iterdir() if d.is_dir()]
        if not subdirs:
            logging.error(f"No subdirectories found in: {base_dir}")
            return None
        # Sort by modification time, descending
        subdirs_sorted = sorted(subdirs, key=lambda d: d.stat().st_mtime, reverse=True)
        latest_dir = subdirs_sorted[0]
        return latest_dir
    except Exception as e:
        logging.error(f"Error finding latest project directory: {e}")
        return None

# --- Helper Function to Extract Theme from Path ---
def extract_theme_from_project_path(project_path: Path):
    """Extracts the theme name from the project folder name."""
    folder_name = project_path.name
    # Regex to capture the part between the first and last underscore
    # Assumes format Movie_Theme_Timestamp or Movie_Theme_With_Spaces_Timestamp
    match = re.match(r'^.+?_(.+?)_\d+$', folder_name)
    if match:
        theme_underscores = match.group(1)
        theme_name = theme_underscores.replace('_', ' ') # Replace underscores with spaces
        logging.info(f"Extracted theme: '{theme_name}' from folder name: {folder_name}")
        return theme_name
    else:
        logging.warning(f"Could not extract theme from folder name format: {folder_name}. Using default.")
        return "Default Theme" # Or handle error differently

# --- Helper Function to Find Prompt for Character ---
def find_prompt_for_character(project_path: Path, character_name: str, theme: str = None) -> str:
    """
    Finds and returns the flux prompt for a given character in a project.
    Looks for the flux_prompt.txt in the character's directory.
    """
    characters_base_path = project_path / "characters"
    char_name_sanitized = "".join(c for c in character_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
    character_dir = characters_base_path / char_name_sanitized
    prompt_file_path = character_dir / "flux_prompt.txt"
    if prompt_file_path.is_file():
        try:
            with open(prompt_file_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception as e:
            logging.error(f"Error reading flux prompt for {character_name}: {e}")
            return None
    else:
        logging.error(f"Flux prompt file not found for {character_name} at {prompt_file_path}")
        return None

# --- Main Logic ---
def run_prompt_generation(project_path: Path): # Removed theme argument
    """Generates Flux prompts for all characters in the specified project."""
    logging.info(f"--- Starting Flux Prompt Generation for Project: {project_path.name} ---")

    # --- Extract Theme ---
    selected_theme = extract_theme_from_project_path(project_path)
    if not selected_theme:
        logging.error("Could not determine theme. Halting.")
        return False
    logging.info(f"Using Theme: {selected_theme}")
    # --- End Extract Theme ---

    # ... (Load main metadata as before) ...
    metadata_path = project_path / "metadata.json"
    # ... (Handle file not found) ...
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f: movie_info = json.load(f)
    except Exception as e: return False

    characters_base_path = project_path / "characters"
    actors_generated_count = 0
    actors_failed_count = 0
    medium_prompts_generated = 0
    full_prompts_generated = 0
    actors_failed_medium = []
    actors_failed_full = []

    # Loop through characters defined in the main metadata
    for char_info in movie_info.get('main_characters_actors', []):
        character_name = char_info.get('character_name')
        actor_name = char_info.get('actor_name')
        if not character_name or not actor_name: continue

        logging.info(f"\nProcessing Character: {character_name} ({actor_name})")
        # ... (Get character_dir, char_metadata_path) ...
        char_name_sanitized = "".join(c for c in character_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
        character_dir = characters_base_path / char_name_sanitized
        char_metadata_path = character_dir / "metadata.json"

        # ... (Load character_data) ...
        if not char_metadata_path.is_file(): continue # Skip if no data
        try:
            with open(char_metadata_path, 'r', encoding='utf-8') as f: character_data = json.load(f)
        except Exception as e: continue # Skip if error loading

        # --- Generate Medium/Upper Body Prompt ---
        prompt_medium_path = character_dir / "flux_prompt_medium.txt" # New filename
        logging.info("Generating Medium/Upper Body prompt...")
        flux_prompt_medium = llm_interactions.generate_flux_prompt(
            character_name, actor_name, character_data, selected_theme, config.OLLAMA_MODEL
        )
        if flux_prompt_medium:
            try:
                with open(prompt_medium_path, 'w', encoding='utf-8') as f: f.write(flux_prompt_medium)
                logging.info(f"Saved Medium prompt to: {prompt_medium_path}")
                medium_prompts_generated += 1
            except IOError as e: logging.error(f"Failed to save Medium prompt: {e}"); actors_failed_medium.append(character_name)
        else:
            logging.error("Failed to generate Medium prompt.")
            actors_failed_medium.append(character_name)

        # --- Generate Full Body Prompt ---
        prompt_full_path = character_dir / "flux_prompt_full.txt" # New filename
        logging.info("Generating Full Body prompt...")
        flux_prompt_full = llm_interactions.generate_flux_prompt_full_body(
            character_name, actor_name, character_data, selected_theme, config.OLLAMA_MODEL
        )
        if flux_prompt_full:
            try:
                with open(prompt_full_path, 'w', encoding='utf-8') as f: f.write(flux_prompt_full)
                logging.info(f"Saved Full Body prompt to: {prompt_full_path}")
                full_prompts_generated += 1
            except IOError as e: logging.error(f"Failed to save Full Body prompt: {e}"); actors_failed_full.append(character_name)
        else:
            logging.error("Failed to generate Full Body prompt.")
            actors_failed_full.append(character_name)

    logging.info("--- Finished Flux Prompt Generation ---")
    # ... (Final logging and return status) ...
    if actors_failed_count == 0 and len(actors_failed_medium) == 0 and len(actors_failed_full) == 0:
        return True
    else:
        return False

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Flux prompts for characters in a movie project.")
    # Removed --theme argument
    parser.add_argument("-p", "--project_path", help="Path to the specific movie project folder. If not provided, uses the latest.")
    args = parser.parse_args()

    # --- Load Environment Variables ---
    load_dotenv(dotenv_path=config.DOTENV_PATH)

    project_to_process = None
    if args.project_path:
        project_to_process = Path(args.project_path)
        # ... (Check if path exists) ...
        if not project_to_process.is_dir(): exit(1)
    else:
        logging.info("No project path provided, finding the latest project directory...")
        project_to_process = find_latest_project_dir(config.PROJECTS_BASE_DIR)
        # ... (Check if found) ...
        if not project_to_process: exit(1)
        logging.info(f"Using latest project directory: {project_to_process}")

    # Run the main logic (no longer needs theme passed)
    success = run_prompt_generation(project_to_process)

    if success:
         logging.info("Prompt generation completed successfully.")
         exit(0)
    else:
         logging.error("Prompt generation encountered errors.")
         exit(1)