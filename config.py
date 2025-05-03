from pathlib import Path

# --- ComfyUI Paths ---
# !!! IMPORTANT: CHANGE THESE TO YOUR ACTUAL PATHS !!!
COMFYUI_INPUT_DIR = Path("D:/Comfy_UI_V20/ComfyUI/input").resolve()
COMFYUI_OUTPUT_DIR = Path("H:/dancers_content").resolve()

# --- Workflow Templates ---
# Assumes a comfyui_workflows subfolder exists where main script is
WORKFLOW_TEMPLATE_DIR = Path(__file__).resolve().parent / "comfyui_workflows"
IMAGE_WORKFLOW_TEMPLATE = "API_flux_and_reactor.json" # Updated to point to the correct workflow file
VIDEO_WORKFLOW_TEMPLATE = "API_wanvideo_reactor.json" # Your video workflow filename (update later)

# --- Node Titles (Match your workflow JSON exactly) ---
PROMPT_NODE_TITLE = "API_Prompt_Input"
FACE_NODE_TITLE = "API_Face_Input" # For ReActor source face
SEED_NODE_TITLE = "API_Seed_Input" # For main sampler seed

# --- Titles for BASE Image Output ---
IMAGE_BASE_OUTPUT_PREFIX_NODE_TITLE = "API_Base_Output_Prefix" # Title of the prefix node for BASE image
IMAGE_BASE_OUTPUT_SAVE_NODE_TITLE = "API_Base_Image_Output_SaveNode" # Title of the SaveImage node for BASE image

# --- Titles for SWAPPED Image Output ---
IMAGE_SWAPPED_OUTPUT_PREFIX_NODE_TITLE = "API_Swapped_Output_Prefix" # Corrected title
IMAGE_SWAPPED_OUTPUT_SAVE_NODE_TITLE = "API_Image_Output_SaveNode" # Title of the SaveImage node for SWAPPED image

# --- Title for VIDEO START IMAGE (Used in Video WF) ---
VIDEO_START_IMAGE_NODE_TITLE = "API_Video_Start_Image"

# --- Title for VIDEO OUTPUT (Used in Video WF) ---
VIDEO_OUTPUT_SAVE_NODE_TITLE = "Video Combine " # Final video VHS_VideoCombine node (or retitle)

# --- File Handling ---
# ... (TEMP_IMAGE_FOLDER_NAME, FILTERED_IMAGE_FOLDER_NAME, SOURCE_ACTORS_FOLDER_NAME already exist) ...
BASE_IMAGE_PREFIX = "base_"
SWAPPED_IMAGE_PREFIX = "swapped_" # Prefix for CANDIDATE files
APPROVED_IMAGE_PREFIX = "approved_" # Prefix for files after web approval
FINAL_VIDEO_CLIP_PREFIX = "final_clip_"

# --- Folder Names (Used by Python script) ---
TEMP_IMAGE_FOLDER_NAME = "temp_actor_images"
FILTERED_IMAGE_FOLDER_NAME = "filtered_single_face_images" # For source selection
SOURCE_ACTORS_FOLDER_NAME = "source_actors"
GENERATED_SWAPPED_CANDIDATES_FOLDER_NAME = "generated_swapped_candidates" # For approval step
APPROVED_IMAGES_FOLDER_NAME = "approved_images" # Final images for video gen
BASE_IMAGES_FOLDER_NAME = "generated_base_images" # Where base gens are saved in project

# --- Core Configuration ---
OLLAMA_MODEL = 'gemma3:12b'
LOG_FILE = 'orchestrator_log.log'
PROJECTS_BASE_DIR = Path("Movie_Projects") # Base directory for all movie projects
COMFYUI_URL = "http://127.0.0.1:8188" # Default ComfyUI API URL
FLASK_PORT = 5002 # Port for the selection web UI

# --- API Keys (Names only - actual keys stay in .env) ---
TAVILY_API_KEY_NAME = "TAVILY_API_KEY"
GEMINI_API_KEY_NAME = "GEMINI_API_KEY"

# --- Web Search & Image Gathering ---
TAVILY_SEARCH_COUNT = 10 # How many image results to request from Tavily
FACE_FILTER_REQUIRED_COUNT = 1 # Number of faces required by filter

# --- LLM Interaction ---
# MAX_RETRIES = 3 # If using critique loop

# --- Web Selector ---
WEB_SELECTOR_TIMEOUT = 1800 # Timeout in seconds (30 minutes)

# --- Future Phases ---
# Add relevant constants for ComfyUI, Video processing later

# --- .env Path ---
DOTENV_PATH = Path(__file__).parent / ".env"



# Video Workflow Titles (MUST MATCH YOUR VIDEO JSON)
VIDEO_PROMPT_NODE_TITLE = "API_Prompt_Input"          # Node 161 (WanVideoTextEncode)
VIDEO_SEED_NODE_TITLE = "API_Seed_Input"              # Node 27 (WanVideoSampler) - RENAME IN COMFYUI!
VIDEO_START_IMAGE_NODE_TITLE = "API_Video_Start_Image" # Node 341 (LoadImage)
VIDEO_FACE_NODE_TITLE = "API_Face_Input"              # Node 392 (LoadImage) - RENAME IN COMFYUI!
VIDEO_OUTPUT_PREFIX_NODE_TITLE = "API_Output_Prefix"      # Node 403 (FileNamePrefix) - RENAME IN COMFYUI!
VIDEO_OUTPUT_SAVE_NODE_TITLE = "Video Combine 🎥🅥🅗🅢" # Node 395 (VHS_VideoCombine) - Use this or retitle

# ... (File Handling Prefixes) ...
FINAL_VIDEO_CLIP_PREFIX = "final_clip_" # Used by Python script for final naming in project

# ... (Folder Names) ...
VIDEO_PROMPTS_FOLDER_NAME = "" # Video prompts saved directly in character folder for now
FINAL_VIDEOS_FOLDER_NAME = "final_videos" # Subfolder in character dir for generated videos