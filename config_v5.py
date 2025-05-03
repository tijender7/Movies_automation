# config_v5.py
# Configuration for V5 pipeline (Staged Approach)
from pathlib import Path

print(f"[DEBUG] config_v5.py loaded from: {__file__}")

# --- ComfyUI Paths ---
COMFYUI_INPUT_DIR = Path("D:/Comfy_UI_V20/ComfyUI/input").resolve()
COMFYUI_OUTPUT_DIR = Path("H:/dancers_content").resolve() # Base for ALL ComfyUI outputs

# --- Project Paths ---
PROJECTS_BASE_DIR = Path("H:/projects/Movie_trailer/movie_vignette_generator/Movie_Projects").resolve() # Use absolute path

# --- Workflow Templates ---
# Assuming workflows are in a 'comfyui_workflows' subdir relative to THIS config file
BASE_VIDEO_WORKFLOW_TEMPLATE = str((Path(__file__).parent / "comfyui_workflows" / "API_fast_video_gen_v5.json").resolve())
FACE_SWAP_WORKFLOW_TEMPLATE = str((Path(__file__).parent / "comfyui_workflows" / "API_reactor_swap_v5.json").resolve()) # New template for stage 2
# Remove or comment out old template paths if not needed
# IMAGE_WORKFLOW_TEMPLATE = str((Path(__file__).parent / "comfyui_workflows" / "API_flux_and_reactor.json").resolve())
# VIDEO_WORKFLOW_TEMPLATE = str((Path(__file__).parent / "comfyui_workflows" / "API_wanvideo_reactor.json").resolve())

# --- Node Titles for BASE Video Workflow (API_fast_video_gen_v5.json) ---
BASE_VIDEO_POS_PROMPT_NODE_TITLE = "API_Positive_Prompt"       # Node 220
BASE_VIDEO_NEG_PROMPT_NODE_TITLE = "API_Negative_Prompt"       # Node 221
BASE_VIDEO_SEED_NODE_TITLE = "API_Seed_Input"               # Node 123 (KSampler)
BASE_VIDEO_START_IMAGE_NODE_TITLE = "API_Video_Start_Image"    # Node 126
BASE_VIDEO_LENGTH_NODE_TITLE = "API_Image_To_Video_Settings" # Node 50 (WanImageToVideo)
BASE_VIDEO_OUTPUT_PREFIX_NODE_TITLE = "API_Output_Prefix_Base" # Node 405 (New Prefix Node)
BASE_VIDEO_SAVE_NODE_TITLE = "API_Save_Base_Video"          # Node 55 (VHS_VideoCombine Renamed)

# --- Node Titles for FACE SWAP Workflow (API_reactor_swap_v5.json) ---
# --- Define these when creating Stage 2 ---
SWAP_VIDEO_INPUT_NODE_TITLE = "API_Load_Base_Video"         # e.g., LoadVideo node
SWAP_FACE_INPUT_NODE_TITLE = "API_Load_Source_Face"        # e.g., LoadImage node
SWAP_REACTOR_NODE_TITLE = "ReActor 🌌 Fast Face Swap"      # Title of the ReActor node
SWAP_OUTPUT_PREFIX_NODE_TITLE = "API_Output_Prefix_Swapped" # e.g., FileNamePrefix node
SWAP_SAVE_NODE_TITLE = "API_Save_Swapped_Video"       # e.g., VHS_VideoCombine node

# --- File Handling Prefixes ---
BASE_VIDEO_CLIP_PREFIX = "base_clip_"     # Prefix for Stage 1 video files
FINAL_VIDEO_CLIP_PREFIX = "final_clip_"   # Prefix for Stage 2 (swapped) video files

# --- Folder Names ---
# Project Folder Structure
CHARACTERS_FOLDER_NAME = "characters"
SOURCE_ACTORS_FOLDER_NAME = "source_actors"
APPROVED_IMAGES_FOLDER_NAME = "approved_images" # Corrected folder name
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json"         # Input for stage 1 script

# API Output Folder Structure (within COMFYUI_OUTPUT_DIR/API_OUTPUTS/RunID/Char__Actor/)
API_OUTPUTS_SUBDIR = "API_OUTPUTS"
COMFYUI_BASE_VIDEOS_SUBFOLDER = "base_videos"         # Output of Stage 1
FINAL_VIDEOS_FOLDER_NAME = "final_videos"             # Output of Stage 2 (face swapped)
# Add others if needed (e.g., for upscale/interpolation outputs)
# COMFYUI_UPSCALED_VIDEOS_SUBFOLDER = "upscaled_videos"
# COMFYUI_INTERPOLATED_VIDEOS_SUBFOLDER = "interpolated_videos"

# --- Core Configuration ---
LOG_FILE = 'orchestrator_pipeline.log' # Maybe a unified log for the pipeline?
COMFYUI_URL = "http://127.0.0.1:8188"
FLASK_PORT = 5002

# --- API Keys (Names only) ---
TAVILY_API_KEY_NAME = "TAVILY_API_KEY"
# GEMINI_API_KEY_NAME = "GEMINI_API_KEY" # Remove if not used
# TMDB_API_KEY_NAME = "TMDB_API_KEY"   # Remove if not used

# --- Default Settings ---
DEFAULT_VIDEO_LENGTH = 81 # Default number of frames for base video gen
DEFAULT_VIDEO_NEGATIVE_PROMPT = "text, watermark, blurry, noisy, low quality, artifacts, deformed, duplicate limbs, static"

# --- .env Path ---
DOTENV_PATH = Path(__file__).parent / ".env"