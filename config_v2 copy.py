print(f"[DEBUG] config_v2.py loaded from: {__file__}")

# config_v2.py
from pathlib import Path

# --- ComfyUI Paths ---
# !!! IMPORTANT: CHANGE THESE TO YOUR ACTUAL PATHS !!!
COMFYUI_INPUT_DIR = Path("D:/Comfy_UI_V20/ComfyUI/input").resolve()
COMFYUI_OUTPUT_DIR = Path("H:/dancers_content").resolve() # Base for ComfyUI outputs

# --- Project Paths ---
PROJECTS_BASE_DIR = Path("Movie_Projects").resolve() # Base directory for all movie projects

# --- Workflow Templates ---
# Assumes a comfyui_workflows subfolder exists where main script is
WORKFLOW_TEMPLATE_DIR = Path(__file__).resolve().parent / "comfyui_workflows"
IMAGE_WORKFLOW_TEMPLATE = str((Path(__file__).parent / "comfyui_workflows" / "API_flux_and_reactor.json").resolve())
VIDEO_WORKFLOW_TEMPLATE = str((Path(__file__).parent / "comfyui_workflows" / "API_wanvideo_reactor.json").resolve())

# --- Node Titles (Match your workflow JSON exactly) ---

# --- Titles for Image Workflow (API_flux_and_reactor.json) ---
PROMPT_NODE_TITLE = "API_Prompt_Input" # Node 217 (Positive Prompt)
FACE_NODE_TITLE = "API_Face_Input" # Node 219 (Load Image for ReActor Face)
SEED_NODE_TITLE = "API_Seed_Input" # Node 216 (Noise Seed for Sampler)
# --- Titles for BASE Image Output ---
IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE = "API_Base_Output_Prefix" # Node 222 (Filename Prefix for Base Image)
IMAGE_BASE_OUTPUT_SAVE_NODE_TITLE = "API_Base_Image_Output_SaveNode" # Node 221 (Save Image for Base Image)
# --- Titles for SWAPPED Image Output ---
IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE = "API_Swapped_Output_Prefix" # Node 220 (Filename Prefix for Swapped Image)
IMAGE_SWAPPED_OUTPUT_SAVE_NODE_TITLE = "API_Image_Output_SaveNode" # Node 3 (Save Image for Swapped Image)

# --- Titles for Video Workflow (API_wanvideo_reactor.json) ---
VIDEO_PROMPT_NODE_TITLE = "API_Prompt_Input"          # Node 161 (WanVideoTextEncode - takes positive/negative)
VIDEO_SEED_NODE_TITLE = "API_Seed_Input"              # Node 27 (WanVideoSampler - RENAME IN COMFYUI!)
VIDEO_START_IMAGE_NODE_TITLE = "API_Video_Start_Image" # Node 341 (LoadImage - for the starting frame)
VIDEO_FACE_NODE_TITLE = "API_Face_Input"              # Node 392 (LoadImage - for ReActor face swap) - RENAME IN COMFYUI!
VIDEO_OUTPUT_PREFIX_NODE_TITLE = "API_Output_Prefix_Swapped"      # Node 403 (FileNamePrefix) - RENAME IN COMFYUI!
VIDEO_OUTPUT_SAVE_NODE_TITLE = "Video Combine 🎥🅥🅗🅢" # Node 395 (VHS_VideoCombine) - Final video output node
VIDEO_ORIGINAL_OUTPUT_PREFIX_NODE_TITLE = "API_Output_Prefix_Original" # New: Prefix node for original video output
VIDEO_ORIGINAL_SAVE_NODE_TITLE = "mp4" # New: Save node title for original video output

# --- File Handling Prefixes ---
BASE_IMAGE_PREFIX = "base_"           # Prefix for base images saved by ComfyUI (within API_OUTPUTS)
SWAPPED_IMAGE_PREFIX = "swapped_"       # Prefix for swapped candidate images saved by ComfyUI (within API_OUTPUTS)
APPROVED_IMAGE_PREFIX = "approved_"     # Prefix added to approved image filenames (within API_OUTPUTS)
FINAL_VIDEO_CLIP_PREFIX = "final_clip_" # Prefix for final video files (within API_OUTPUTS)
ORIGINAL_VIDEO_CLIP_PREFIX = "original_clip_" # New: Prefix for original video files

# --- Folder Names (Used by Python script logic) ---

# ** Project Folder Structure (`PROJECTS_BASE_DIR/...`) **
CHARACTERS_FOLDER_NAME = "characters"                 # Subfolder for character data within project
SOURCE_ACTORS_FOLDER_NAME = "source_actors"           # Subfolder for user-selected source face images within project
TEMP_IMAGE_FOLDER_NAME = "temp_actor_images"          # Temporary download folder (deleted after filtering)
FILTERED_IMAGE_FOLDER_NAME = "filtered_single_face_images" # Temp folder for filtered images before user selection

# ** API Output Folder Structure (`COMFYUI_OUTPUT_DIR/API_OUTPUTS/...`) **
API_OUTPUTS_SUBDIR = "API_OUTPUTS"                    # Main subfolder within COMFYUI_OUTPUT_DIR
COMFYUI_BASE_IMAGES_SUBFOLDER = "base_images"         # Subfolder within ComfyUI's Character__Actor folder for base gens
COMFYUI_SWAPPED_IMAGES_SUBFOLDER = "swapped_images"     # Subfolder within ComfyUI's Character__Actor folder for swapped gens
COMFYUI_ORIGINAL_VIDEOS_SUBFOLDER = "original_videos"  # New: Subfolder for original videos
APPROVED_IMAGES_FOLDER_NAME = "approved_images"       # Subfolder within API Run Folder for copied approved images
API_CHARACTERS_FOLDER_NAME = "characters"             # Subfolder within API Run Folder for generated *video* prompts
FINAL_VIDEOS_FOLDER_NAME = "final_videos"             # Subfolder within API Run Folder for final generated videos

# --- Core Configuration ---
OLLAMA_MODEL = 'gemma3:12b' # Or your preferred model
LOG_FILE = 'orchestrator_log.log'
COMFYUI_URL = "http://127.0.0.1:8188" # Default ComfyUI API URL
FLASK_PORT = 5002 # Port for the selection web UI

# --- API Keys (Names only - actual keys stay in .env) ---
TAVILY_API_KEY_NAME = "TAVILY_API_KEY"
GEMINI_API_KEY_NAME = "GEMINI_API_KEY"
TMDB_API_KEY_NAME = "TMDB_API_KEY"  # Added for TMDb support

# --- Web Search & Image Gathering ---
TAVILY_SEARCH_COUNT = 10 # How many image results to request from Tavily
FACE_FILTER_REQUIRED_COUNT = 1 # Number of faces required by filter

# --- Web Selector / Approver ---
WEB_SELECTOR_TIMEOUT = 1800 # Timeout in seconds (30 minutes) for source face selection
WEB_APPROVER_TIMEOUT = 1800 # Timeout for generated image approval

# --- .env Path ---
DOTENV_PATH = Path(__file__).parent / ".env"

# --- Video Generation ---
DEFAULT_VIDEO_NEGATIVE_PROMPT = "blurry, noisy, text, watermark, deformation, bad quality, duplicate limbs, choppy motion, low frame rate"