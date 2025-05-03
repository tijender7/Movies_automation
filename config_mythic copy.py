# config_mythic.py
import logging
from pathlib import Path
import sys

# --- Project Setup ---
# Assuming this config file is in the same directory as your main script
PROJECT_ROOT_DIR = Path(__file__).resolve().parent
print(f"[DEBUG] config_mythic.py PROJECT_ROOT_DIR: {PROJECT_ROOT_DIR}")

# --- Base Project Structure ---
# This should point to the folder *containing* all your 'mythic_movie_...' project folders
PROJECTS_BASE_DIR = PROJECT_ROOT_DIR / "mythic_movie_project" # e.g., mythic_movie_project/

# --- Logging ---
LOG_DIR = PROJECT_ROOT_DIR / "logs"
# Define base names, scripts can append timestamps
LOG_FILE_BASENAME_GEN = 'mythic_image_gen.log'
LOG_FILE_BASENAME_APPROVE = 'mythic_approval.log'

# --- ComfyUI Connection & Paths ---
COMFYUI_URL = "http://127.0.0.1:8188" # Your ComfyUI server URL
# Base directory where ComfyUI saves outputs (must match ComfyUI's setup if using --output-directory)
COMFYUI_OUTPUT_DIR = Path("H:/dancers_content").resolve() # <<< Make sure this is correct for your system
# Subdirectory within COMFYUI_OUTPUT_DIR where *API-triggered* outputs for THIS project should be organized.
API_OUTPUTS_SUBDIR = "API_OUTPUTS_MYTHIC" # <<< Corrected name

# --- Workflow Template ---
# Path to the specific Flux workflow JSON used by the generation script
FLUX_WORKFLOW_TEMPLATE_FILENAME = "flux_image_body.json"
WORKFLOW_TEMPLATE_DIR = PROJECT_ROOT_DIR / "comfyui_workflows" # Assuming it's in this subfolder
FLUX_WORKFLOW_TEMPLATE_PATH = str((WORKFLOW_TEMPLATE_DIR / FLUX_WORKFLOW_TEMPLATE_FILENAME).resolve())

# --- Node Titles for the Flux Workflow (flux_image_body.json) ---
# Used by the GENERATION script (generate_mythic_images.py)
FLUX_PROMPT_NODE_TITLE = "Text Multiline"         # Node 617 (Takes the main prompt)
FLUX_SEED_NODE_TITLE = "RandomNoise"              # Node 295 (Takes the noise seed)
FLUX_PREFIX_NODE_TITLE = "File Name Prefix (Mikey)" # Node 611 (Controls filename/path for Save nodes 607/627)

# --- Input/Output File & Folder Names ---
# Name of the JSON file containing prompts within each project folder
PROMPT_JSON_FILENAME = "image_prompts_detailed.json"
STORY_FILENAME = "story_approved.txt" # <<< New
SCENES_FILENAME = "scenes_batched.json" # <<< New

# Subfolder name within the specific project's API output directory (inside API_OUTPUTS_SUBDIR/project_name/) where images are saved by the generation script
OUTPUT_IMAGES_SUBFOLDER = "generated_images"
# Name of the folder within the main project directory (e.g., mythic_movie_project/project_name/) where approved images will be copied by the approval script
MYTHIC_APPROVED_FOLDER_NAME = "approved_scene_images"
VIDEO_PROMPT_SUFFIX = getattr(config, 'VIDEO_PROMPT_OUTPUT_SUFFIX', '.video_prompts.json')

# --- Web UI Settings (for Approval Script) ---
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5003 # Using a distinct port for the Mythic approval UI
WEB_APPROVER_TIMEOUT = 1800 # Timeout in seconds (e.g., 30 minutes)

# --- .env Path (Optional) ---
DOTENV_PATH = PROJECT_ROOT_DIR / ".env"

# --- ComfyUI Timeout (for Generation Script) ---
COMFYUI_TIMEOUT = 600 # Seconds to wait for a generation workflow to complete (e.g., 10 minutes)


# --- Startup Validation (Basic Checks) ---
def validate_paths():
    valid = True
    paths_to_check = {
        "PROJECTS_BASE_DIR": (PROJECTS_BASE_DIR, "dir"),
        "COMFYUI_OUTPUT_DIR": (COMFYUI_OUTPUT_DIR, "dir"),
        "FLUX_WORKFLOW_TEMPLATE_PATH": (Path(FLUX_WORKFLOW_TEMPLATE_PATH), "file")
    }
    for name, (path_obj, path_type) in paths_to_check.items():
        exists = path_obj.is_dir() if path_type == "dir" else path_obj.is_file()
        if not exists:
            print(f"ERROR in config: {name} ('{path_obj}') not found or not a valid {path_type}.")
            valid = False
    return valid

# You can optionally call validate_paths() here if you want an immediate check when config is imported,
# but scripts using it also perform checks.
# if not validate_paths():
#     sys.exit("Exiting due to invalid configuration paths.")