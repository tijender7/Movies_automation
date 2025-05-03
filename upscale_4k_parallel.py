# upscale_4k_parallel.py v3.5 (Adjusted Paths + Prefixed Output Names)
# Upscales videos found in the latest API_OUTPUTS/[latest_video_folder]/[character]/final_video_swapped
# Saves results to API_OUTPUTS/[latest_video_folder]/upscaled_videos/

import os
import subprocess
import time
import logging
import sys
import concurrent.futures
from datetime import datetime
from pathlib import Path
import random # For random delay (if added later)

try:
    from tqdm import tqdm
except ImportError:
    print("ERROR: tqdm library not found. Please install it: pip install tqdm")
    sys.exit(1)

# ==============================================================================
#  CONFIGURATION - 4K VERSION
# ==============================================================================

# --- Paths ---
# Base directory containing the latest video folder (e.g., sholay_..._VIDEO_...)
API_OUTPUTS_BASE_DIR = Path(r"H:/dancers_content/API_OUTPUTS")
# Subfolder within character folders where source videos are located
SOURCE_VIDEO_SUBFOLDER = "final_video_swapped"
# Subfolder within the LATEST video folder where upscaled videos will be saved
UPSCALE_OUTPUT_FOLDER_NAME = "upscaled_videos"

# --- Topaz Specific Configuration ---
TOPAZ_INSTALL_DIR = Path(r"C:\Program Files\Topaz Labs LLC\Topaz Video AI")
TOPAZ_MODEL_DIR = Path(r"C:\ProgramData\Topaz Labs LLC\Topaz Video AI\models")
TOPAZ_FFMPEG_EXE = TOPAZ_INSTALL_DIR / "ffmpeg.exe"
TOPAZ_TIMEOUT = 7200 # Timeout per video in seconds (2 hours)

# --- 4K Target Filters & Settings ---
TARGET_BITRATE_KBPS = "15000k"
MAX_BITRATE_KBPS = "25000k"
AUDIO_BITRATE_KBPS = "192k"

TOPAZ_FILTER_COMPLEX = (
    "tvai_fi=model=chr-2:slowmo=1:rdt=0.01:fps=30:device=0:vram=1:instances=1," # Frame interpolation
    "tvai_up=model=prob-4:scale=2:preblur=-0.334523:noise=0.05:details=0.2:halo=0.0573913:blur=0.14:compression=0.535133:blend=0.2:device=0:vram=1:instances=1," # 1st upscale Proteus
    "tvai_up=model=amq-13:scale=0:w=3840:h=2160:blend=0.2:device=0:vram=1:instances=1," # 2nd upscale Artemis MQ to 4K
    "scale=w=3840:h=2160:flags=lanczos:threads=0:force_original_aspect_ratio=decrease," # Ensure final scale
    "pad=3840:2160:-1:-1:color=black" # Pad if needed
)

TOPAZ_ENCODER_SETTINGS = (
    f"-c:v h264_nvenc -profile:v high -pix_fmt yuv420p -g 30 "
    f"-preset p6 -tune hq " # p6=very slow/better quality, p7=slowest/best
    f"-rc vbr -cq 22 " # VBR with CQ target (lower=better quality/larger file)
    f"-b:v {TARGET_BITRATE_KBPS} -maxrate {MAX_BITRATE_KBPS} -bufsize {int(float(MAX_BITRATE_KBPS[:-1])*1.5)}k "
    f"-rc-lookahead 20 -spatial_aq 1 -aq-strength 15 "
    f"-c:a aac -b:a {AUDIO_BITRATE_KBPS} -ac 2 " # AAC Stereo audio
    f"-map_metadata 0 -map_metadata:s:v 0:s:v "
    f"-movflags frag_keyframe+empty_moov+delay_moov+use_metadata_tags+write_colr -bf 2"
)

# --- Parallel Processing Settings ---
MAX_CONCURRENT_UPSCALES = 1 # Keep at 1 unless you have a very high-end GPU and sufficient VRAM

# ==============================================================================
#  Logging Setup
# ==============================================================================
script_dir = Path(__file__).resolve().parent
log_directory = script_dir / "logs"
log_directory.mkdir(exist_ok=True)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
log_file = log_directory / f"upscale_4k_parallel_run_{datetime.now():%Y%m%d_%H%M%S}.log"
file_handler = logging.FileHandler(log_file, encoding='utf-8'); file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler(sys.stdout); console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger = logging.getLogger(); logger.setLevel(logging.INFO)
if logger.hasHandlers(): logger.handlers.clear()
logger.addHandler(file_handler); logger.addHandler(console_handler)

# ==============================================================================
#  Topaz Upscaling Function (Identical to previous versions)
# ==============================================================================
def upscale_video_topaz(task_id, input_video_path: Path, output_video_path: Path):
    """Runs the Topaz FFmpeg command. Returns True on success, False on failure."""
    label = f"4K Upscale Task {task_id}"
    logger.info(f"  ⬆️ Starting {label}: '{input_video_path.name}' -> '{output_video_path.name}'")

    start_time = time.time()
    env_vars = os.environ.copy()
    env_vars["TVAI_MODEL_DIR"] = str(TOPAZ_MODEL_DIR.resolve())
    env_vars["CUDA_VISIBLE_DEVICES"] = "0" # Or specific GPU ID if multiple

    command = ( f'"{str(TOPAZ_FFMPEG_EXE)}" -y -hide_banner -hwaccel auto -i "{str(input_video_path)}" '
                f'-sws_flags spline+accurate_rnd+full_chroma_int -filter_complex "{TOPAZ_FILTER_COMPLEX}" '
                f'{TOPAZ_ENCODER_SETTINGS} "{str(output_video_path)}"' )

    result = None
    stderr_snippet = "[No STDERR captured]"

    try:
        logger.debug(f"    Executing command for {label}: {command}")
        result = subprocess.run( command, shell=True, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=str(TOPAZ_INSTALL_DIR), env=env_vars, timeout=TOPAZ_TIMEOUT )
        end_time = time.time()
        success = (result.returncode == 0 and output_video_path.exists() and output_video_path.stat().st_size > 10240) # Check size > 10KB

        if result and result.stderr:
             stderr_snippet = '\n'.join(result.stderr.strip().splitlines()[-20:]) # Get last 20 lines

        if success:
            logger.info(f"    ✅ {label} finished successfully in {round(end_time - start_time, 2)}s. Output size: {output_video_path.stat().st_size / (1024*1024):.2f} MB")
            return True
        else:
            logger.error(f"    ❌ {label} failed.")
            logger.error(f"       Input: {input_video_path.name}")
            if result:
                 logger.error(f"       FFmpeg Exit Code: {result.returncode}")
                 logger.error(f"       STDERR Snippet:\n{stderr_snippet}")
            else: logger.error("       FFmpeg process likely did not start or finish properly.")
            if not output_video_path.exists(): logger.error(f"       Output file not found: {output_video_path}")
            elif output_video_path.stat().st_size <= 10240: logger.error(f"       Output file invalid/too small: {output_video_path} ({output_video_path.stat().st_size} bytes)")
            # Clean up failed/small output file
            if output_video_path.exists() and output_video_path.stat().st_size <= 10240:
                try: output_video_path.unlink(); logger.warning(f"       Removed small/invalid output file: {output_video_path.name}")
                except Exception as rm_err: logger.error(f"       Failed to remove small/invalid output file: {rm_err}")
            return False

    except subprocess.TimeoutExpired:
         logger.error(f"    ❌ {label} timed out after {TOPAZ_TIMEOUT} seconds for {input_video_path.name}.")
         if output_video_path.exists(): # Clean up potentially incomplete file
             try: output_video_path.unlink(); logger.warning(f"       Removed potentially incomplete output file (timeout): {output_video_path.name}")
             except Exception as rm_err: logger.error(f"       Failed to remove timed-out output file: {rm_err}")
         return False
    except Exception as e:
        logger.error(f"    ❌ Python error during {label} for {input_video_path.name}: {e}", exc_info=True)
        if result and result.stderr: # Log stderr even on Python error if available
             stderr_snippet = '\n'.join(result.stderr.strip().splitlines()[-20:])
             logger.error(f"       STDERR Snippet:\n{stderr_snippet}")
        return False

# ==============================================================================
#  Main Script Logic
# ==============================================================================
if __name__ == "__main__":
    logger.info("=" * 50); logger.info(f"Starting Parallel 4K Upscaling Script (v3.5): {datetime.now()}"); logger.info("=" * 50)

    # --- Pre-checks ---
    if not API_OUTPUTS_BASE_DIR.is_dir(): logger.critical(f"CRITICAL: API Outputs base directory not found: {API_OUTPUTS_BASE_DIR}. Exiting."); sys.exit(1)
    if not TOPAZ_FFMPEG_EXE.is_file(): logger.critical(f"CRITICAL: Topaz FFmpeg not found: {TOPAZ_FFMPEG_EXE}. Exiting."); sys.exit(1)
    if not TOPAZ_MODEL_DIR.is_dir(): logger.warning(f"WARNING: Topaz Model Directory not found: {TOPAZ_MODEL_DIR}.")

    # --- Find the latest VIDEO folder based on naming convention ---
    latest_video_folder = None
    try:
        # Find folders matching the pattern '*_VIDEO_*' within the API Outputs base directory
        potential_video_folders = [d for d in API_OUTPUTS_BASE_DIR.iterdir() if d.is_dir() and "_VIDEO_" in d.name]

        if not potential_video_folders:
            logger.critical(f"CRITICAL: No '*_VIDEO_*' folders found in '{API_OUTPUTS_BASE_DIR}'. Exiting."); sys.exit(1)

        # Sort by modification time to find the latest
        latest_video_folder = sorted(potential_video_folders, key=lambda x: x.stat().st_mtime, reverse=True)[0]
        logger.info(f"Found latest video processing folder: {latest_video_folder.name}")
        logger.info(f"Full path: {latest_video_folder.resolve()}")

    except Exception as e:
        logger.critical(f"CRITICAL: Error finding latest video folder in '{API_OUTPUTS_BASE_DIR}': {e}", exc_info=True); sys.exit(1)

    # --- Define and Create the Specific Output Folder ---
    upscaled_output_dir = None
    try:
        upscaled_output_dir = latest_video_folder / UPSCALE_OUTPUT_FOLDER_NAME # Path relative to latest folder
        upscaled_output_dir.mkdir(parents=True, exist_ok=True) # Create it if it doesn't exist
        logger.info(f"Upscaled videos will be saved to: {upscaled_output_dir.resolve()}")
    except Exception as e:
        logger.critical(f"CRITICAL: Cannot create output directory {upscaled_output_dir}: {e}"); sys.exit(1)


    # --- Find videos to process within the latest folder ---
    videos_to_process = []
    try:
        # Get all subdirectories within the latest_video_folder (potential character folders)
        # Exclude the output folder itself from the scan
        character_folders = [d for d in latest_video_folder.iterdir() if d.is_dir() and d.name != UPSCALE_OUTPUT_FOLDER_NAME]

        if not character_folders:
            logger.warning(f"No character subfolders found within '{latest_video_folder}'. Nothing to process."); sys.exit(0)

        logger.info(f"Scanning {len(character_folders)} character folders inside '{latest_video_folder.name}'...")
        for char_folder in character_folders:
            source_video_dir = char_folder / SOURCE_VIDEO_SUBFOLDER # Look inside 'final_video_swapped'
            if source_video_dir.is_dir():
                found_videos = list(source_video_dir.glob("*.mp4")) # Find only .mp4 files
                if found_videos:
                    logger.info(f"  Found {len(found_videos)} videos in '{char_folder.name}/{SOURCE_VIDEO_SUBFOLDER}'")
                    # Store tuple: (character_folder_name, video_path)
                    for vid_path in found_videos:
                        videos_to_process.append((char_folder.name, vid_path))
                # else: # Optional logging for empty folders
                #    logger.debug(f"  No videos found in '{char_folder.name}/{SOURCE_VIDEO_SUBFOLDER}'")
            # else: # Optional logging for missing subfolders
            #    logger.warning(f"  Subfolder '{SOURCE_VIDEO_SUBFOLDER}' not found in '{char_folder.name}'")

    except Exception as e:
        logger.critical(f"CRITICAL: Error scanning for videos within '{latest_video_folder}': {e}", exc_info=True); sys.exit(1)


    if not videos_to_process:
        logger.warning(f"No video files (*.mp4) found in any '{SOURCE_VIDEO_SUBFOLDER}' subfolders within '{latest_video_folder}'. Exiting."); sys.exit(0)

    total_videos_found = len(videos_to_process)
    logger.info(f"Found a total of {total_videos_found} MP4 videos to potentially process.")


    # --- Prepare tasks for parallel execution ---
    tasks = []
    skipped_existing = 0

    for i, (char_folder_name, video_path) in enumerate(videos_to_process):
        original_video_name = video_path.name
        # Construct output filename including character name prefix
        # Example: Basanti__Hema_Malini_video1_upscaled_4k.mp4
        upscaled_filename = f"{char_folder_name}_{original_video_name.replace(video_path.suffix, '')}_upscaled_4k{video_path.suffix}"

        upscaled_output_path = upscaled_output_dir / upscaled_filename # Target the specific output dir

        task_id = f"{i+1:03d}" # Task ID based on total found videos

        # Check if output already exists and is valid
        if upscaled_output_path.exists() and upscaled_output_path.stat().st_size > 10240:
            logger.info(f"  ⏭️ Skipping Task {task_id}: Output file already exists: '{upscaled_output_path.name}'")
            skipped_existing += 1
            continue # Skip adding this task

        tasks.append((task_id, video_path, upscaled_output_path)) # Add task tuple: (id, input_path, output_path)

    tasks_to_run_count = len(tasks)
    if tasks_to_run_count == 0:
        logger.info(f"All {total_videos_found} found videos seem to be already processed ({skipped_existing} skipped). Exiting.")
        sys.exit(0)

    logger.info(f"\n--- Starting Topaz 4K Video Upscaling ({tasks_to_run_count} tasks to run, {MAX_CONCURRENT_UPSCALES} parallel) ---")
    successful_upscales = 0
    failed_upscales = 0

    # --- Execute tasks in parallel ---
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_UPSCALES) as executor:
        # Store future alongside its task details for better error reporting/tracking
        future_to_task = {executor.submit(upscale_video_topaz, task_id, vid_path, out_path): (task_id, vid_path.name) for task_id, vid_path, out_path in tasks}

        # Process completed futures using tqdm progress bar
        for future in tqdm(concurrent.futures.as_completed(future_to_task), total=tasks_to_run_count, desc="Upscaling 4K Videos"):
            task_id, video_name = future_to_task[future]
            try:
                success = future.result() # Get result (True/False) from upscale_video_topaz
                if success:
                    successful_upscales += 1
                else:
                    failed_upscales += 1
                    # Error already logged within the function
                    # logger.warning(f"Upscaling task {task_id} reported failure for: {video_name}") # Optional summary warning
            except Exception as exc:
                failed_upscales += 1
                logger.error(f"Task {task_id} for '{video_name}' generated an unexpected exception: {exc}", exc_info=True)

    # --- Final Summary ---
    logger.info(f"\n--- Finished 4K Upscaling ---")
    logger.info(f"  Total Videos Found: {total_videos_found}")
    logger.info(f"  Skipped (Already Existed): {skipped_existing}")
    logger.info(f"  Tasks Executed: {tasks_to_run_count}")
    logger.info(f"  Successful Upscales: {successful_upscales}")
    logger.info(f"  Failed Upscales: {failed_upscales}")
    logger.info(f"Upscaled 4K videos saved in: {upscaled_output_dir.resolve()}")
    logger.info("=" * 50)