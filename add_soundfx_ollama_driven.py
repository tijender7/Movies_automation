# add_soundfx_ollama_driven.py
import os
import sys
import subprocess
import logging
import time
import concurrent.futures
import cv2         # For frame extraction
import base64      # For encoding frames for Ollama
import requests    # For Ollama API calls
import json        # For Ollama API calls
import re          # For cleaning Ollama prompts <-- Added
import shutil      # For cross-drive file moves <-- Added
from pathlib import Path
from datetime import datetime
from gradio_client import Client, handle_file # For Gradio audio generation API

# ─── 1. CONFIG ────────────────────────────────────────────────────────────────
# --- Paths ---
API_OUTPUTS_BASE_DIR = Path(r"H:/dancers_content/API_OUTPUTS") # Base folder containing dated VIDEO folders
UPSCALED_SUBFOLDER      = "upscaled_videos"                  # Subfolder with input videos
SFX_OUTPUT_SUBFOLDER    = "upscaled_with_ollama_sfx"         # Subfolder for final videos with new SFX

# --- Ollama Analysis Configuration ---
OLLAMA_API_URL = "http://localhost:11434/api/generate"       # Your local Ollama API endpoint
OLLAMA_MODEL = "gemma3:12b"                                  # Ollama model for video analysis
NUM_KEYFRAMES_FOR_ANALYSIS = 4                               # How many frames to send to Ollama per video
# Fallback prompt if Ollama analysis fails or produces unusable output
DEFAULT_SFX_PROMPT = "Generate dynamic ambient sound effects suitable for this video scene."

# --- Gradio Audio Generation Configuration ---
GRADIO_URL = "http://127.0.0.1:7860"                         # Your Gradio audio generation API endpoint
# Prompt telling the audio model what *not* to generate (helps improve quality)
GRADIO_NEGATIVE_PROMPT = "low quality, noisy, muffled, quiet, static, distorted, excessive echo, excessive reverb, water sounds, underwater, speech, dialogue, talking, singing"
# Audio generation parameters (tune these based on your Gradio model's performance)
GRADIO_STEPS = 150        # Number of diffusion steps (more steps generally = better quality but slower)
GRADIO_CFG_SCALE = 7      # How strongly the model should adhere to the prompt (typical range 5-10)
GRADIO_SAMPLER = "dpmpp-3m-sde" # Sampler type (check available options in your Gradio API)
GRADIO_SEED = "-1"        # Use "-1" for a random seed for varied outputs, or set a number for reproducible results

# --- Processing Configuration ---
# Start with 1 worker. Increase CAUTIOUSLY based on system resources (CPU/GPU/RAM).
MAX_WORKERS = 1

# ─── 2. LOGGING ───────────────────────────────────────────────────────────────
script_dir = Path(__file__).parent
logs = script_dir / "logs"
logs.mkdir(exist_ok=True)
logfile = logs / f"add_sfx_ollama_{datetime.now():%Y%m%d_%H%M%S}.log"

logging.basicConfig(
    level=logging.INFO, # Set to logging.DEBUG for more detailed logs (frame extraction, API calls, etc.)
    format="%(asctime)s - %(levelname)s - %(process)d - %(threadName)s - %(message)s", # Added threadName
    handlers=[
        logging.FileHandler(logfile, encoding="utf-8"),
        logging.StreamHandler(sys.stdout) # Also print logs to console
    ]
)
logger = logging.getLogger()

# ─── 3. HELPER FUNCTIONS ──────────────────────────────────────────────────────

# --- Filesystem/Video Helpers ---
def get_latest_video_folder(base_dir: Path) -> Path | None:
    """Find the most recently modified folder containing '_VIDEO_'."""
    try:
        candidates = [d for d in base_dir.iterdir() if d.is_dir() and "_VIDEO_" in d.name]
        if not candidates:
            logger.critical(f"No '_VIDEO_' subfolders found in {base_dir}. Please check path and folder structure.")
            return None
        latest = max(candidates, key=lambda d: d.stat().st_mtime)
        logger.info(f"Identified latest video batch folder: {latest.name}")
        return latest
    except FileNotFoundError:
        logger.critical(f"Base directory not found: {base_dir}")
        return None
    except Exception as e:
        logger.critical(f"Error finding latest video folder in {base_dir}: {e}", exc_info=True)
        return None

def get_video_duration(path: Path) -> float:
    """Return video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    try:
        # Added timeout to prevent hanging on problematic files
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
        duration = float(res.stdout.strip() or 0)
        if duration <= 0:
            logger.warning(f"ffprobe reported zero or negative duration for {path.name}. Check video file.")
            return 0.0
        return duration
    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed for duration check of {path.name}: {e.stderr}")
        return 0.0
    except subprocess.TimeoutExpired:
        logger.error(f"ffprobe timed out getting duration for {path.name}")
        return 0.0
    except ValueError:
        logger.error(f"ffprobe returned non-numeric duration for {path.name}: '{res.stdout.strip()}'")
        return 0.0
    except Exception as e:
        logger.error(f"Unexpected error getting video duration for {path.name}: {e}", exc_info=False)
        return 0.0

def merge_audio(video_in: Path, audio_in: Path, video_out: Path) -> bool:
    """Replace the video's audio track with audio_in via ffmpeg."""
    if not audio_in.exists():
        logger.error(f"Cannot merge audio for {video_in.name}, generated audio file not found: {audio_in}")
        return False

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", # Quieter ffmpeg output
        "-i", str(video_in),      # Input video
        "-i", str(audio_in),      # Input audio (generated SFX)
        "-map", "0:v:0",          # Map video stream from first input (0)
        "-map", "1:a:0",          # Map audio stream from second input (1)
        "-c:v", "copy",           # Copy video stream without re-encoding
        "-c:a", "aac",            # Re-encode audio to AAC (common format)
        "-b:a", "192k",           # Set audio bitrate
        "-shortest",              # Finish encoding when the shortest input ends (video or audio)
        str(video_out)            # Output video file path
    ]
    logger.debug(f"Executing ffmpeg merge command for {video_out.name}")
    try:
        res = subprocess.run(cmd, capture_output=True, check=True, timeout=180) # 3 min timeout for merge
        logger.debug(f"ffmpeg merge successful for {video_out.name}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg merge failed for {video_in.name}. Error: {e.stderr.decode(errors='ignore')}")
        return False
    except subprocess.TimeoutExpired:
         logger.error(f"ffmpeg merge timed out for {video_in.name}")
         return False
    except Exception as e:
        logger.error(f"Unexpected error during ffmpeg merge for {video_in.name}: {e}", exc_info=False)
        return False

# --- Ollama Analysis Helpers ---
def extract_keyframes(video_path: Path, num_frames: int) -> list | None:
    """Extracts a specified number of keyframes evenly spaced from a video."""
    frames_data = []
    cap = None  # Initialize cap outside try block
    try:
        logger.debug(f"Attempting to open video for frame extraction: {video_path.name}")
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.error(f"cv2.VideoCapture failed to open video file: {video_path.name}")
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        logger.debug(f"Video properties for {video_path.name}: Total Frames={total_frames}, FPS={fps:.2f}")

        if total_frames <= 0:
             logger.error(f"Video file reports zero or negative frames: {video_path.name}")
             cap.release()
             return None

        # Ensure we don't request more frames than available
        actual_num_frames = min(num_frames, total_frames)
        if actual_num_frames < num_frames:
             logger.warning(f"Video {video_path.name} has only {total_frames} frames. Extracting {actual_num_frames} instead of {num_frames}.")

        if actual_num_frames == 0:
             logger.error(f"Cannot extract 0 frames from {video_path.name}")
             cap.release()
             return None

        logger.debug(f"Extracting {actual_num_frames} keyframes from {video_path.name}...")
        # Calculate indices for evenly spaced frames, avoiding the very first/last frame usually
        indices = [int(total_frames * (i + 1) / (actual_num_frames + 1)) for i in range(actual_num_frames)]
        # Ensure indices are within bounds
        indices = [max(0, min(idx, total_frames - 1)) for idx in indices]
        logger.debug(f"Calculated frame indices: {indices}")

        count = 0
        for frame_index in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
            if ret and frame is not None:
                frames_data.append(frame)
                count += 1
                logger.debug(f"  Successfully extracted frame at index {frame_index} ({count}/{actual_num_frames}) for {video_path.name}")
            else:
                logger.warning(f"  Could not read frame at index {frame_index} for {video_path.name}. Attempting next frame.")
                # Simple retry: try reading the next immediate frame
                ret_next, frame_next = cap.read()
                if ret_next and frame_next is not None:
                    frames_data.append(frame_next)
                    count += 1
                    logger.warning(f"  Successfully read frame at index {frame_index + 1} instead.")
                else:
                     logger.error(f"  Failed to read frame at or near index {frame_index} for {video_path.name}.")

        cap.release()
        logger.debug(f"Released video capture for {video_path.name}")

        if not frames_data:
            logger.error(f"Failed to extract any valid frames from {video_path.name}.")
            return None

        logger.debug(f"Successfully extracted {len(frames_data)} frames in total from {video_path.name}.")
        return frames_data

    except Exception as e:
        logger.error(f"Error during frame extraction for {video_path.name}: {e}", exc_info=True)
        if cap is not None and cap.isOpened():
            cap.release()
        return None

def encode_image_to_base64(frame) -> str | None:
    """Encodes an OpenCV frame (numpy array) to a Base64 string."""
    try:
        # Encode the frame to PNG format in memory (PNG is lossless)
        success, buffer = cv2.imencode('.png', frame)
        if not success:
            logger.error("cv2.imencode failed to encode frame to PNG buffer.")
            return None
        # Encode the PNG buffer to Base64
        base64_encoded = base64.b64encode(buffer).decode('utf-8')
        return base64_encoded
    except cv2.error as e:
         logger.error(f"OpenCV error during frame encoding: {e}")
         return None
    except Exception as e:
        logger.error(f"Error encoding frame to Base64: {e}", exc_info=False)
        return None

def get_sfx_prompt_from_ollama(base64_images: list, video_name: str) -> str | None:
    """Sends frames to Ollama and asks for an SFX generation prompt."""
    if not base64_images:
        logger.error(f"No base64 images provided to Ollama for {video_name}.")
        return None

    # --- Refined prompt asking ONLY for sound keywords/phrases ---
    prompt_to_ollama = (
        f"Analyze these {len(base64_images)} sequential keyframes from a short video clip. "
        "Describe the essential sound elements ONLY. List key sounds as comma-separated keywords or very short phrases "
        "(e.g., 'futuristic hover vehicle hum, bustling city crowd, energetic synth music, whoosh sound'). "
        "Do NOT add explanations, narrative descriptions, or conversational text like 'Okay, here is...'. "
        "Just provide the sound keywords/phrases directly."
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt_to_ollama,
        "images": base64_images,
        "stream": False,
        "options": { # Optional: Add generation parameters if needed for Ollama
            "temperature": 0.5 # Lower temperature for more focused output
        }
    }
    headers = {'Content-Type': 'application/json'}

    logger.debug(f"Sending request to Ollama for {video_name} (model: {OLLAMA_MODEL})...")
    try:
        response = requests.post(OLLAMA_API_URL, headers=headers, data=json.dumps(payload), timeout=300) # 5 min timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        response_data = response.json()
        generated_prompt = response_data.get("response")

        if generated_prompt:
             logger.debug(f"Received raw response from Ollama for {video_name}.")
             return generated_prompt.strip() # Return the raw response (cleaning happens later)
        else:
             logger.error(f"Ollama response for {video_name} missing 'response' key. Response: {response_data}")
             return None

    except requests.exceptions.Timeout:
        logger.error(f"Request to Ollama timed out for {video_name}.")
        return None
    except requests.exceptions.ConnectionError:
        logger.error(f"Could not connect to Ollama API at {OLLAMA_API_URL}. Is it running?")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Ollama API request failed for {video_name}: {e}")
        # Log response body if available and contains error details
        if e.response is not None:
             logger.error(f"Ollama Response Body: {e.response.text}")
        return None
    except json.JSONDecodeError:
        logger.error(f"Could not decode JSON response from Ollama for {video_name}: {response.text}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error querying Ollama for {video_name}: {e}", exc_info=True)
        return None

def clean_ollama_prompt(raw_text: str, video_name: str) -> str:
    """Attempts to extract the core prompt keywords from Ollama's potentially verbose output."""
    if not raw_text:
        logger.warning(f"Received empty raw text from Ollama for {video_name} for cleaning.")
        return ""

    logger.debug(f"Raw Ollama text for {video_name} before cleaning: '{raw_text[:200]}...'")

    cleaned = raw_text # Start with the original text

    # 1. Attempt to find explicit markers like "Prompt:**" (case-insensitive)
    #    This regex looks for variations like Prompt:, Prompt**, Prompt: ** etc.
    prompt_marker = re.search(r'\bprompt\b\s*[:*]+\s*', cleaned, re.IGNORECASE)
    if prompt_marker:
        cleaned = cleaned[prompt_marker.end():]
        logger.debug(f"Cleaned after prompt marker removal: '{cleaned[:200]}...'")
    else:
        # 2. If no marker, try removing common conversational prefixes
        common_prefixes = [
            "Okay, here's a text prompt for an AI audio generation model:",
            "Okay, here's a text prompt:",
            "Here's a concise text prompt suitable for an AI audio generation model:",
            "Here's a concise text prompt:",
            "Here's a prompt:",
            "Based on the keyframes, here are some sound elements:",
            "Sound elements:",
            "Keywords:",
        ]
        # Check prefixes case-insensitively
        original_cleaned = cleaned # Keep a copy for comparison
        for prefix in common_prefixes:
             prefix_lower = prefix.lower()
             if cleaned.strip().lower().startswith(prefix_lower):
                 # Find the actual end index of the prefix in the original case string
                 try:
                      prefix_end_index = cleaned.lower().index(prefix_lower) + len(prefix_lower)
                      cleaned = cleaned[prefix_end_index:].strip()
                      logger.debug(f"Cleaned after removing prefix '{prefix}': '{cleaned[:200]}...'")
                      break # Stop after removing the first matching prefix
                 except ValueError: # Should not happen if startswith is true, but be safe
                      pass
        if cleaned == original_cleaned:
            logger.debug("No common prefixes found or removed.")


    # 3. Remove potential leading/trailing quotes and extra whitespace/newlines
    cleaned = cleaned.strip(' \t\n\r"\'*')

    # 4. Optional: Replace newline characters with commas or spaces if the model prefers single-line prompts
    cleaned = re.sub(r'\s*\n\s*', ', ', cleaned).strip() # Replace newlines with comma+space
    # Remove potentially repeated commas
    cleaned = re.sub(r',(\s*,)+', ',', cleaned)
    cleaned = cleaned.strip(',')


    logger.debug(f"Final cleaned prompt for {video_name}: '{cleaned[:200]}...'")

    if not cleaned:
        logger.warning(f"Prompt cleaning resulted in an empty string for {video_name}. Original: '{raw_text[:200]}...'")

    return cleaned


# --- Gradio SFX Generation Helper ---
def generate_sfx_via_gradio(video_path: Path, sfx_prompt: str, temp_audio: Path) -> bool:
    """Call Gradio '/generate' with a specific prompt to make SFX audio."""
    video_name = video_path.name
    logger.debug(f"Attempting Gradio SFX generation for {video_name}...")
    logger.debug(f"  Using Prompt: '{sfx_prompt}'")
    logger.debug(f"  Using Negative Prompt: '{GRADIO_NEGATIVE_PROMPT}'")

    try:
        # Initialize Gradio client (consider initializing once outside the loop if performance is critical)
        client = Client(GRADIO_URL, verbose=False) # Set verbose=True for detailed client logs if needed

        duration = get_video_duration(video_path)
        if duration <= 0.1: # Check for reasonably positive duration
             logger.error(f"Cannot generate SFX for {video_name}, invalid duration detected: {duration:.2f}s")
             return False

        logger.info(f"Calling Gradio API for {video_name} (Duration: {duration:.2f}s)")

        # Predict using the parameters defined in the API docs
        # Ensure parameter names match exactly what the /generate endpoint expects
        result = client.predict(
            prompt=sfx_prompt,                      # Use the cleaned prompt from Ollama (or default)
            negative_prompt=GRADIO_NEGATIVE_PROMPT, # Use the configured negative prompt
            video_file=handle_file(str(video_path)),# Send the video file object
            video_path=None,                        # API might ignore this if video_file is provided
            audio_prompt_file=None,                 # Not using audio prompt
            audio_prompt_path=None,
            seconds_start=0,                        # Generate for the whole duration
            seconds_total=duration,                 # Use actual video duration
            cfg_scale=GRADIO_CFG_SCALE,
            steps=GRADIO_STEPS,
            preview_every=0,                        # Disable preview generation
            seed=GRADIO_SEED,
            sampler_type=GRADIO_SAMPLER,
            sigma_min=0.03,                         # Keep API defaults unless tuning needed
            sigma_max=500,
            cfg_rescale=0,
            use_init=False,                         # Not using init audio
            init_audio=None,
            init_noise_level=0.1,
            api_name="/generate"                    # Crucial: specify the correct Gradio API endpoint name
        )

        # --- Process the result ---
        # Expected result structure based on API docs: tuple (video_dict, audio_filepath)
        if isinstance(result, (list, tuple)) and len(result) >= 2:
            _, audio_fp_str = result # Get the second element which should be the audio filepath string
            if audio_fp_str and isinstance(audio_fp_str, str):
                 audio_fp = Path(audio_fp_str)
                 if audio_fp.exists() and audio_fp.is_file():
                    logger.debug(f"Gradio returned audio file path: {audio_fp}")
                    # Move generated audio from Gradio's temp location to our script's temp path
                    try:
                        logger.debug(f"Attempting to move {audio_fp} to {temp_audio}")
                        shutil.move(str(audio_fp), str(temp_audio)) # <-- Use shutil.move for cross-drive compatibility
                        logger.info(f"Successfully generated and saved SFX: {temp_audio.name}")
                        return True
                    except Exception as move_err:
                         logger.error(f"Failed to move generated audio file for {video_name}. Error: {move_err}", exc_info=True)
                         # Attempt to copy as fallback if move fails? (Optional)
                         # try:
                         #     shutil.copy2(str(audio_fp), str(temp_audio))
                         #     logger.info(f"Successfully COPIED generated SFX after move failed: {temp_audio.name}")
                         #     # Try to clean up original Gradio temp file if copy succeeded
                         #     try: audio_fp.unlink() except OSError: pass
                         #     return True
                         # except Exception as copy_err:
                         #     logger.error(f"Fallback copy also failed for {video_name}. Error: {copy_err}")
                         #     return False
                         return False # Fail if move fails
                 else:
                    logger.error(f"Gradio API returned an invalid or non-existent audio file path: '{audio_fp_str}' for {video_name}")
                    return False
            else:
                 logger.error(f"Gradio API returned unexpected audio path type: {type(audio_fp_str)} Value: {audio_fp_str} for {video_name}")
                 return False
        else:
             logger.error(f"Unexpected result structure from Gradio API for {video_name}. Expected tuple/list of length >= 2, Got: {type(result)} Value: {result}")
             return False

    except ImportError:
         logger.critical("gradio_client library not found. Please install it using: pip install gradio_client")
         # Exit here as the script cannot function without it
         sys.exit(1)
    except Exception as e:
        # Catch potential gradio_client specific errors or general exceptions
        logger.error(f"Failed Gradio API call for {video_name}. Error: {e}", exc_info=True)
        return False

# --- Main Video Processing Function ---
def process_video(task_info):
    """Full pipeline for one video: Ollama analysis -> Gradio SFX gen -> merge -> cleanup."""
    idx, total_videos, video_path, out_folder = task_info # Unpack task info
    video_name = video_path.name
    processing_start_time = time.time() # Time the entire processing for this video
    logger.info(f"[{idx}/{total_videos}] >>>> Starting processing for: {video_name}")

    # Define temporary and final file paths
    tmp_audio_filename = f"{video_path.stem}_sfx_temp.wav" # Assume Gradio outputs WAV, adjust if needed
    tmp_audio_path = out_folder / tmp_audio_filename
    final_video_filename = f"{video_path.stem}_with_ollama_sfx.mp4"
    final_video_path = out_folder / final_video_filename

    # --- Step 1: Analyze with Ollama to get SFX prompt ---
    sfx_raw_prompt = None
    analysis_start_time = time.time()
    logger.info(f"[{idx}/{total_videos}] Step 1: Analyzing video with Ollama...")

    keyframes = extract_keyframes(video_path, NUM_KEYFRAMES_FOR_ANALYSIS)
    if keyframes:
        base64_frames = []
        logger.debug(f"[{idx}] Encoding {len(keyframes)} frames for Ollama...")
        for i, frame in enumerate(keyframes):
            encoded = encode_image_to_base64(frame)
            if encoded:
                base64_frames.append(encoded)
            else:
                logger.warning(f"[{idx}] Failed to encode frame #{i+1} for {video_name}")
        logger.debug(f"[{idx}] Successfully encoded {len(base64_frames)} frames.")

        if base64_frames: # Proceed if at least one frame was encoded
             sfx_raw_prompt = get_sfx_prompt_from_ollama(base64_frames, video_name)
        else:
            logger.error(f"[{idx}] Could not encode ANY frames for Ollama analysis for {video_name}.")
    else:
        logger.error(f"[{idx}] Failed to extract keyframes for Ollama analysis for {video_name}.")

    analysis_duration = time.time() - analysis_start_time
    logger.info(f"[{idx}/{total_videos}] Ollama analysis took {analysis_duration:.2f}s.")

    # --- Step 1b: Clean the Ollama Prompt ---
    if not sfx_raw_prompt:
        logger.warning(f"[{idx}] Ollama analysis failed or returned no prompt for {video_name}. Using default prompt.")
        sfx_final_prompt = DEFAULT_SFX_PROMPT # Use default if Ollama failed
    else:
        logger.info(f"[{idx}] Cleaning Ollama suggested prompt for {video_name}...")
        sfx_final_prompt = clean_ollama_prompt(sfx_raw_prompt, video_name) # Clean the prompt
        if not sfx_final_prompt: # If cleaning resulted in empty string
             logger.warning(f"[{idx}] Prompt cleaning failed for {video_name}. Using default prompt.")
             sfx_final_prompt = DEFAULT_SFX_PROMPT
        else:
             logger.info(f"[{idx}] Using CLEANED prompt for Gradio: '{sfx_final_prompt[:150]}...'")

    # --- Step 2: Generate SFX audio via Gradio ---
    logger.info(f"[{idx}/{total_videos}] Step 2: Generating SFX via Gradio...")
    generation_start_time = time.time()
    generation_success = generate_sfx_via_gradio(video_path, sfx_final_prompt, tmp_audio_path)
    generation_duration = time.time() - generation_start_time

    if not generation_success:
        logger.error(f"[{idx}/{total_videos}] SFX generation via Gradio failed for {video_name}. Skipping merge.")
        # Optionally keep the failed temp audio path for debugging
        # if tmp_audio_path.exists(): logger.info(f"[{idx}] Keeping failed temp audio: {tmp_audio_path}")
        return False # Stop processing this video if generation fails
    logger.info(f"[{idx}/{total_videos}] Gradio SFX generation took {generation_duration:.2f}s.")

    # --- Step 3: Merge audio into final video ---
    logger.info(f"[{idx}/{total_videos}] Step 3: Merging audio into {final_video_path.name}...")
    merge_start_time = time.time()
    merge_success = merge_audio(video_path, tmp_audio_path, final_video_path)
    merge_duration = time.time() - merge_start_time

    if not merge_success:
        logger.error(f"[{idx}/{total_videos}] Audio merge failed for {video_name}.")
        # Keep temp audio for debugging if merge fails
        if tmp_audio_path.exists(): logger.warning(f"[{idx}] Keeping temp audio due to merge failure: {tmp_audio_path}")
        return False
    logger.info(f"[{idx}/{total_videos}] Audio merge took {merge_duration:.2f}s.")

    # --- Step 4: Cleanup temp audio ---
    try:
        if tmp_audio_path.exists():
             tmp_audio_path.unlink()
             logger.debug(f"[{idx}] Cleaned up temporary audio file: {tmp_audio_path.name}")
    except OSError as e:
        logger.warning(f"[{idx}] Could not delete temporary audio file {tmp_audio_path.name}: {e}")

    total_time_for_video = time.time() - processing_start_time
    logger.info(f"✅ [{idx}/{total_videos}] <<<< Successfully processed {video_name} -> {final_video_path.name} in {total_time_for_video:.2f}s")
    return True

# ─── 4. MAIN EXECUTION ────────────────────────────────────────────────────────
if __name__=="__main__":
    main_start_time = time.time()
    logger.info("="*70)
    logger.info(" Starting OLLAMA-DRIVEN Sound-FX Replacement Pipeline ")
    logger.info(f"Timestamp: {datetime.now()}")
    logger.info("="*70)

    try:
        # --- Setup Paths ---
        latest_folder = get_latest_video_folder(API_OUTPUTS_BASE_DIR)
        if not latest_folder:
            sys.exit(1) # Exit if no suitable folder found

        upscaled_dir  = latest_folder / UPSCALED_SUBFOLDER
        sfx_dir       = latest_folder / SFX_OUTPUT_SUBFOLDER
        sfx_dir.mkdir(exist_ok=True) # Create output directory if it doesn't exist

        if not upscaled_dir.exists():
            logger.critical(f"Input video folder does not exist: {upscaled_dir}")
            sys.exit(1)

        # --- Find Videos ---
        # Use glob to find potential video files, then filter for files only
        video_files = sorted([p for p in upscaled_dir.glob("*.mp4") if p.is_file()]) # Add other extensions if needed: glob("*.mp4") | glob("*.mov") etc.
        if not video_files:
            logger.warning(f"No compatible video files (.mp4) found in {upscaled_dir}")
            sys.exit(0)

        total_videos = len(video_files)
        logger.info(f"Found {total_videos} videos to process in: {upscaled_dir.name}")
        logger.info(f"Output destination: {sfx_dir.resolve()}")
        logger.info(f"Using MAX_WORKERS = {MAX_WORKERS} (adjust based on system resources)")
        logger.info(f"Ollama Model: {OLLAMA_MODEL}, Gradio Endpoint: {GRADIO_URL}")
        logger.info("-"*70)


        # --- Prepare Tasks ---
        # Pass total_videos count to each task for better logging
        tasks = [(i + 1, total_videos, vid_path, sfx_dir) for i, vid_path in enumerate(video_files)]

        success_count = 0
        failure_count = 0

        # --- Execute Tasks Concurrently ---
        # Use ThreadPoolExecutor as tasks are primarily I/O bound (network calls, file operations)
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="SFX_Worker") as executor:
            # Use executor.map to get results in the order tasks were submitted (optional)
            # Or use submit + as_completed for results as they finish
            future_to_task = {executor.submit(process_video, task): task for task in tasks}

            for future in concurrent.futures.as_completed(future_to_task):
                task_info = future_to_task[future]
                task_idx, _, task_video_path, _ = task_info
                video_name = task_video_path.name
                try:
                    result_success = future.result() # result() will re-raise exceptions from the worker thread
                    if result_success:
                        success_count += 1
                    else:
                        # The process_video function logs errors internally if it returns False
                        logger.error(f"Processing task for {video_name} (Index {task_idx}) reported failure.")
                        failure_count += 1
                except KeyboardInterrupt:
                     logger.warning("Keyboard interrupt received. Shutting down workers...")
                     # Attempt graceful shutdown (may not immediately stop running tasks)
                     executor.shutdown(wait=False, cancel_futures=True)
                     # Re-raise the KeyboardInterrupt to stop the main script
                     raise
                except Exception as exc:
                    logger.error(f"Worker thread for {video_name} (Index {task_idx}) raised an unexpected exception: {exc}", exc_info=True)
                    failure_count += 1

        # --- Final Summary ---
        logger.info("="*70)
        logger.info(" Processing Finished ")
        logger.info(f"  Successfully processed: {success_count} video(s)")
        logger.info(f"  Failed to process:     {failure_count} video(s)")
        total_execution_time = time.time() - main_start_time
        logger.info(f"  Total execution time: {total_execution_time:.2f} seconds")
        logger.info(f"Logs saved to: {logfile.resolve()}")
        logger.info("="*70)

    except KeyboardInterrupt:
         logger.warning("Script terminated by user (KeyboardInterrupt).")
         sys.exit(1)
    except Exception as e:
        logger.critical(f"A critical error occurred in the main execution block: {e}", exc_info=True)
        sys.exit(1)