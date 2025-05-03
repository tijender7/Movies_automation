import logging
from pathlib import Path
import sys

# --- Project Setup ---
PROJECT_ROOT_DIR = Path(__file__).resolve().parent
print(f"[DEBUG] config_mythic.py PROJECT_ROOT_DIR: {PROJECT_ROOT_DIR}")

# --- Base Project Structure ---
PROJECTS_BASE_DIR = PROJECT_ROOT_DIR / "mythic_movie_project"

# --- Logging ---
LOG_DIR = PROJECT_ROOT_DIR / "logs"
LOG_FILE_BASENAME_GEN     = 'mythic_image_gen.log'
LOG_FILE_BASENAME_APPROVE = 'mythic_approval.log'

# --- ComfyUI Connection & Paths ---
COMFYUI_URL        = "http://127.0.0.1:8188"
COMFYUI_OUTPUT_DIR = Path("H:/dancers_content").resolve()
API_OUTPUTS_SUBDIR = "API_OUTPUTS_MYTHIC"

# --- Workflow Template ---
WORKFLOW_TEMPLATE_DIR      = PROJECT_ROOT_DIR / "comfyui_workflows"
FLUX_WORKFLOW_TEMPLATE_FILENAME = "flux_image_body.json"
FLUX_WORKFLOW_TEMPLATE_PATH     = str((WORKFLOW_TEMPLATE_DIR / FLUX_WORKFLOW_TEMPLATE_FILENAME).resolve())

# --- Node Titles for the Flux Workflow ---
FLUX_PROMPT_NODE_TITLE        = "Text Multiline"
FLUX_SEED_NODE_TITLE          = "RandomNoise"
FLUX_PREFIX_NODE_TITLE        = "File Name Prefix (Mikey)"

# --- Input/Output File & Folder Names ---
PROMPT_JSON_FILENAME     = "image_prompts_detailed.json"
STORY_FILENAME           = "story_approved.txt"
SCENES_FILENAME          = "scenes_batched.json"

OUTPUT_IMAGES_SUBFOLDER      = "generated_images"
MYTHIC_APPROVED_FOLDER_NAME  = "approved_scene_images"
VIDEO_PROMPTS_FOLDER_NAME    = "video_prompts"

# **NEW**: suffix for your video‐prompts JSON files
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json"

# --- Web UI Settings (Approval Script) ---
FLASK_HOST          = "127.0.0.1"
FLASK_PORT          = 5003
WEB_APPROVER_TIMEOUT = 1800

# --- .env Path (Optional) ---
DOTENV_PATH       = PROJECT_ROOT_DIR / ".env"

# --- ComfyUI Timeout (Generation Script) ---
COMFYUI_TIMEOUT   = 600

def validate_paths():
    valid = True
    checks = {
        "PROJECTS_BASE_DIR": (PROJECTS_BASE_DIR, "dir"),
        "COMFYUI_OUTPUT_DIR": (COMFYUI_OUTPUT_DIR, "dir"),
        "FLUX_WORKFLOW_TEMPLATE_PATH": (Path(FLUX_WORKFLOW_TEMPLATE_PATH), "file"),
    }
    for name, (p, t) in checks.items():
        ok = p.is_dir() if t=="dir" else p.is_file()
        if not ok:
            print(f"ERROR in config: {name} ('{p}') not a valid {t}")
            valid = False
    return valid
# (optional) call validate_paths() here or from your scripts
