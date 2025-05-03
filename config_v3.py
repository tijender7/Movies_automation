# config_v3.py - Targets the ORIGINAL WanVideo workflow structure

import logging
from pathlib import Path
import sys

PROJECT_ROOT_DIR = Path(__file__).resolve().parent
print(f"[DEBUG] config_v3.py PROJECT_ROOT_DIR: {PROJECT_ROOT_DIR}")

# --- Logging ---
LOG_FILE_BASENAME = 'orchestrator_pipeline.log'
LOG_DIR = PROJECT_ROOT_DIR / "logs"

# --- Core Paths ---
PROJECTS_BASE_DIR = PROJECT_ROOT_DIR / "Movie_Projects"

# --- ComfyUI Paths ---
COMFYUI_INPUT_DIR = Path("D:/Comfy_UI_V20/ComfyUI/input").resolve()  # YOUR PATH
COMFYUI_OUTPUT_DIR = Path("H:/dancers_content").resolve()          # YOUR PATH
API_OUTPUTS_SUBDIR = "API_OUTPUTS"

# --- Workflow Templates ---
WORKFLOW_TEMPLATE_DIR = PROJECT_ROOT_DIR / "comfyui_workflows"

# --- Point to your ORIGINAL second workflow file ---
VIDEO_WORKFLOW_TEMPLATE = str((WORKFLOW_TEMPLATE_DIR / "API_wanvideo_original.json").resolve())

# --- Other workflow paths (assuming they are correct) ---
IMAGE_WORKFLOW_TEMPLATE = str((Path(__file__).parent / "comfyui_workflows" / "API_flux_and_reactor.json").resolve())
REACTOR_WORKFLOW_TEMPLATE = str((WORKFLOW_TEMPLATE_DIR / "API_reactor_face_swap.json").resolve())


# --- Node Titles (Match API_wanvideo_original.json EXACTLY) ---
# --- Titles for VIDEO Workflow (API_wanvideo_original.json) ---

VIDEO_PROMPT_NODE_TITLE = "API_Prompt_Input"          # Title of WanVideoTextEncode (Node #161)
# VIDEO_NEGATIVE_PROMPT_NODE_TITLE variable is not needed

VIDEO_SEED_NODE_TITLE = "API_Seed_Input"                 # Title of WanVideoSampler (Node #27)
VIDEO_START_IMAGE_NODE_TITLE = "API_Video_Start_Image"    # Title of LoadImage (Node #341)
VIDEO_OUTPUT_SAVE_NODE_TITLE = "mp4"                      # Title of VHS_VideoCombine (Node #30)
# VIDEO_OUTPUT_PREFIX_NODE_TITLE variable is not needed


# --- Titles for Image Workflow (API_flux_and_reactor.json) ---
PROMPT_NODE_TITLE = "API_Prompt_Input"
FACE_NODE_TITLE = "API_Face_Input"
SEED_NODE_TITLE = "API_Seed_Input"
SAVE_BASE_IMAGE_NODE_TITLE = "API_Base_Image_Output_SaveNode"
SAVE_SWAPPED_IMAGE_NODE_TITLE = "API_Image_Output_SaveNode"

# --- Titles for FACE SWAP Workflow (API_reactor_face_swap.json) ---
REACTOR_SOURCE_FACE_NODE_TITLE = "API_Reactor_Source_Face"
REACTOR_TARGET_VIDEO_NODE_TITLE = "API_Reactor_Target_Video"
REACTOR_OUTPUT_PREFIX_NODE_TITLE = "API_Reactor_Output_Prefix"


# --- File Handling Prefixes ---
VIDEO_PREFIX = "vid_"

# --- Folder Names ---
CHARACTERS_FOLDER_NAME = "characters"
SOURCE_ACTORS_FOLDER_NAME = "source_actors"
APPROVED_IMAGES_FOLDER_NAME = "approved_images"
FINAL_VIDEO_SWAPPED_FOLDER_NAME = "final_video_swapped"

# --- Core Configuration ---
COMFYUI_URL = "http://127.0.0.1:8188"
FLASK_PORT = 5002

# --- API Keys (Names only - actual keys stay in .env) ---
TAVILY_API_KEY_NAME = "TAVILY_API_KEY"
GEMINI_API_KEY_NAME = "GEMINI_API_KEY"
TMDB_API_KEY_NAME = "TMDB_API_KEY"

# --- Web Search & Image Gathering ---
TAVILY_SEARCH_COUNT = 10
FACE_FILTER_REQUIRED_COUNT = 1

# --- Web Selector / Approver ---
WEB_SELECTOR_TIMEOUT = 1800
WEB_APPROVER_TIMEOUT = 1800

# --- .env Path ---
DOTENV_PATH = PROJECT_ROOT_DIR / ".env"

# --- Video Generation Defaults (Optional) ---
# DEFAULT_VIDEO_STEPS = 20
# DEFAULT_VIDEO_CFG = 6.0
# DEFAULT_VIDEO_SCHEDULER = "unipc"