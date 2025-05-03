# generate_base_videos_v5.py
# Stage 1: Generates BASE videos using ComfyUI based on approved images and video prompts.
# Fixes loading of APPROVED_IMAGES_FOLDER_NAME constant.

import logging
import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
import argparse
import random
import copy
from datetime import datetime
import sys
import re
import glob
import time

# Import project modules
try:
    import config_v5 as config # Use V5 config
    import comfyui_interactions as comfyui_interactions
    # Ensure helper source script name is correct if you renamed it
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules (config_v5, comfyui_interactions, helpers): {e}")
    exit(1)

# --- Constants ---
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "generate_base_video.log") # Specific log
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR')
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME')
# --- FIXED CONSTANT LOADING ---
APPROVED_IMAGES_FOLDER_NAME = getattr(config, 'APPROVED_IMAGES_FOLDER_NAME') # Use correct attribute name
# --- END FIX ---
VIDEO_PROMPT_OUTPUT_SUFFIX = getattr(config, 'VIDEO_PROMPT_OUTPUT_SUFFIX')
COMFYUI_INPUT_DIR = getattr(config, 'COMFYUI_INPUT_DIR')
COMFYUI_OUTPUT_DIR = getattr(config, 'COMFYUI_OUTPUT_DIR')
API_OUTPUTS_SUBDIR = getattr(config, 'API_OUTPUTS_SUBDIR')
# --- Use BASE workflow and constants ---
BASE_VIDEO_WORKFLOW_TEMPLATE = getattr(config, 'BASE_VIDEO_WORKFLOW_TEMPLATE')
COMFYUI_BASE_VIDEOS_SUBFOLDER = getattr(config, 'COMFYUI_BASE_VIDEOS_SUBFOLDER')
BASE_VIDEO_CLIP_PREFIX = getattr(config, 'BASE_VIDEO_CLIP_PREFIX')
BASE_VIDEO_POS_PROMPT_NODE_TITLE = getattr(config, 'BASE_VIDEO_POS_PROMPT_NODE_TITLE')
BASE_VIDEO_NEG_PROMPT_NODE_TITLE = getattr(config, 'BASE_VIDEO_NEG_PROMPT_NODE_TITLE')
BASE_VIDEO_SEED_NODE_TITLE = getattr(config, 'BASE_VIDEO_SEED_NODE_TITLE')
BASE_VIDEO_START_IMAGE_NODE_TITLE = getattr(config, 'BASE_VIDEO_START_IMAGE_NODE_TITLE')
BASE_VIDEO_LENGTH_NODE_TITLE = getattr(config, 'BASE_VIDEO_LENGTH_NODE_TITLE') # Node to set length
BASE_VIDEO_OUTPUT_PREFIX_NODE_TITLE = getattr(config, 'BASE_VIDEO_OUTPUT_PREFIX_NODE_TITLE')
BASE_VIDEO_SAVE_NODE_TITLE = getattr(config, 'BASE_VIDEO_SAVE_NODE_TITLE')
DEFAULT_VIDEO_LENGTH = getattr(config, 'DEFAULT_VIDEO_LENGTH', 81)
# ---

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s [%(filename)s:%(lineno)d] - %(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING); logging.getLogger('werkzeug').setLevel(logging.WARNING); logging.getLogger('tensorflow').setLevel(logging.ERROR)
except Exception as e: print(f"ERROR logging setup: {e}"); logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-%(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Functions (Assumed Correct) ---
def log_step(m, l="info", i=False, important=None, level=None, exc_info=None):
    if important is not None: i = important
    if level is not None: l = level
    level_upper = l.upper(); prefix_map = {"INFO": "[*]", "WARNING": "[!]", "ERROR": "[X]", "SUCCESS": "[+]", "DEBUG": "[D]"}
    final_msg = f"{prefix_map.get(level_upper, '[?]')} {m}";
    if i: final_msg = f"--- {final_msg} ---"
    log_level_int = getattr(logging, level_upper, logging.INFO)
    if exc_info: log.log(log_level_int, final_msg, exc_info=True)
    else: log.log(log_level_int, final_msg)

def sanitize_name(n):
    if not isinstance(n,str):n=str(n);
    s=re.sub(r'[<>:"/\\|?*\']','_', n); s="".join(c for c in s if c.isalnum() or c in (' ','_','-')).strip().replace(' ','_'); s=re.sub(r'[_]+','_',s); s=re.sub(r'[-]+','-',s); s=s.strip('_-'); return s[:60].strip('_-') or "invalid"

def copy_to_comfyui_input(p: Path):
    if not p.is_file():log_step(f"Copy fail: {p}",l="error");return False;
    if not COMFYUI_INPUT_DIR or not Path(COMFYUI_INPUT_DIR).is_dir():log_step(f"Input Dir invalid",l="error");return False;
    try:d=Path(COMFYUI_INPUT_DIR)/p.name;shutil.copy2(p,d);log_step(f" Copied '{p.name}'",l="debug");return True;
    except Exception as e:log_step(f"Copy error '{p.name}': {e}",l="error");return False;

def process_and_copy_video_output(comfyui_result_history, output_node_title, api_run_video_save_dir, output_filename_prefix):
    log_step(f" Processing output for node '{output_node_title}'...")
    output_details = comfyui_interactions.get_output_details_from_history(comfyui_result_history, output_node_title)
    output_filename = None; output_subfolder = ""
    if output_details and isinstance(output_details, dict):
        video_info = None
        log_step(f" Parsing output details...", level="debug")
        if 'gifs' in output_details and isinstance(output_details['gifs'], list) and output_details['gifs']: video_info = output_details['gifs'][0]
        elif 'videos' in output_details and isinstance(output_details['videos'], list) and output_details['videos']: video_info = output_details['videos'][0]
        elif 'filename' in output_details: video_info = output_details
        else: log_step(" No 'gifs', 'videos', or 'filename' key found.", level="warning")
        if isinstance(video_info, dict): output_filename=video_info.get('filename'); output_subfolder=video_info.get('subfolder', '')
        if not output_filename: log_step(f" Cannot find 'filename' for '{output_node_title}'. Details: {json.dumps(output_details)}", level="error"); return None
        log_step(f" Found ComfyUI output: '{output_filename}' (Subfolder: '{output_subfolder}')", level="info")
    else: log_step(f" Cannot find valid output details for '{output_node_title}'", level="error"); return None
    comfy_output_video_path = (Path(COMFYUI_OUTPUT_DIR).resolve() / output_subfolder / output_filename).resolve()
    comfy_stem, comfy_ext = os.path.splitext(output_filename)
    final_filename = f"{output_filename_prefix.strip('_')}_{comfy_stem}{comfy_ext}"
    api_run_final_video_path = api_run_video_save_dir / final_filename
    if comfy_output_video_path.is_file():
        try: api_run_video_save_dir.mkdir(parents=True, exist_ok=True); shutil.copy2(comfy_output_video_path, api_run_final_video_path); log_step(f" Copied ComfyUI video -> {api_run_final_video_path.name}", level="success"); return api_run_final_video_path
        except Exception as e: log_step(f" Failed copy output video '{comfy_output_video_path.name}': {e}", level="error"); return None
    else: log_step(f" ComfyUI video not found at: {comfy_output_video_path}", level="error"); return None

# --- Main Logic ---
def run_base_video_generation(project_path: Path, requested_video_length: int):
    log_step(f"--- Step 1: Setup & Validation (Base Video) ---", important=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", level="error"); return False
    # Define paths (use corrected constant name here)
    approved_images_dir = project_path / APPROVED_IMAGES_FOLDER_NAME
    project_characters_base_path = project_path / CHARACTERS_FOLDER_NAME
    if not approved_images_dir.is_dir(): log_step(f"Folder missing: {approved_images_dir}", level="error"); return False
    if not project_characters_base_path.is_dir(): log_step(f"Folder missing: {project_characters_base_path}", level="error"); return False
    comfy_input_dir = Path(COMFYUI_INPUT_DIR); comfy_output_dir = Path(COMFYUI_OUTPUT_DIR); comfy_api_base_folder_abs = comfy_output_dir / API_OUTPUTS_SUBDIR
    try: comfy_api_base_folder_abs.mkdir(parents=True, exist_ok=True)
    except OSError as e: log_step(f"Cannot create API base out dir: {e}", level="error"); return False
    workflow_template = comfyui_interactions.load_workflow_template(BASE_VIDEO_WORKFLOW_TEMPLATE)
    if not workflow_template: log_step(f"Failed load BASE video workflow: {BASE_VIDEO_WORKFLOW_TEMPLATE}", level="error"); return False
    approved_image_paths = sorted(list(approved_images_dir.glob('*.[jp][pn]g')))
    if not approved_image_paths: log_step(f"No approved images in {approved_images_dir}", level="warning"); return True
    log_step(f"Found {len(approved_image_paths)} approved images for base video generation.")
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S"); comfy_api_run_subfolder_rel = Path(f"{project_path.name}_{run_timestamp}"); comfy_api_run_folder_abs = comfy_api_base_folder_abs / comfy_api_run_subfolder_rel

    log_step(f"--- Step 2: Processing Images for BASE Video Generation ---", important=True)
    success_count = 0; fail_count = 0; processed_count = 0

    for approved_image_path in approved_image_paths:
        processed_count += 1; log_step(f"--- Processing {processed_count}/{len(approved_image_paths)}: {approved_image_path.name} ---", important=True)
        filename_stem = approved_image_path.stem; parts = filename_stem.split("__")
        if len(parts)<2: log_step(f" Skip bad name format: {filename_stem}",l="warning"); fail_count+=1; continue # Check needs >= 2 parts for Char__Actor
        try: char_san, actor_san = parts[0], parts[1];
        except IndexError: log_step(f" Skip bad name split: {filename_stem}",l="warning"); fail_count+=1; continue
        log_step(f" Parsed: Actor='{actor_san}', Char='{char_san}'", l="debug")
        prompt_file_path = project_characters_base_path / actor_san / (filename_stem + VIDEO_PROMPT_OUTPUT_SUFFIX)
        if not prompt_file_path.is_file(): log_step(f" Prompts missing: {prompt_file_path.name}. Skip.",l="warning"); fail_count+=1; continue
        try: prompts = json.load(open(prompt_file_path,'r',encoding='utf-8')); pos=prompts['positive_prompt']; neg=prompts['negative_prompt']
        except Exception as e: log_step(f" Error load prompts {prompt_file_path.name}: {e}",l="error"); fail_count+=1; continue
        log_step(f" Loaded prompts for {filename_stem}", l="debug")
        log_step(" Copying start image...", l="debug")
        if not copy_to_comfyui_input(approved_image_path): log_step(" File copy fail. Skip.",l="error"); fail_count+=1; continue
        start_image_filename = approved_image_path.name

        try:
            current_wf = copy.deepcopy(workflow_template); seed = random.randint(0, 2**32-1)
            char_actor_rel = Path(f"{char_san}__{actor_san}")
            prefix_node_base_dir_rel = Path(API_OUTPUTS_SUBDIR)/comfy_api_run_subfolder_rel.name/char_actor_rel/COMFYUI_BASE_VIDEOS_SUBFOLDER
            base_video_filename_prefix = f"{BASE_VIDEO_CLIP_PREFIX}{filename_stem}_"
            inputs_to_set = {
                BASE_VIDEO_POS_PROMPT_NODE_TITLE: {"text": pos},
                BASE_VIDEO_NEG_PROMPT_NODE_TITLE: {"text": neg},
                BASE_VIDEO_SEED_NODE_TITLE: {"seed": seed},
                BASE_VIDEO_START_IMAGE_NODE_TITLE: {"image": start_image_filename},
                BASE_VIDEO_LENGTH_NODE_TITLE: {"length": requested_video_length},
                BASE_VIDEO_OUTPUT_PREFIX_NODE_TITLE: {"custom_directory": prefix_node_base_dir_rel.as_posix(), "custom_text": "", "date_directory": "false" }
            }
            log_step(f" Modifying workflow (Seed: {seed}, Length: {requested_video_length})...", l="debug")
            if not comfyui_interactions.modify_workflow_inputs(current_wf, inputs_to_set): raise ValueError("Fail modify workflow")
            log_step(f" Queueing BASE video: {filename_stem}...")
            history = comfyui_interactions.run_comfyui_workflow(current_wf, timeout=1800)
            if not history: raise TimeoutError("ComfyUI timeout/fail")
            api_base_video_char_dir = comfy_api_run_folder_abs / char_actor_rel / COMFYUI_BASE_VIDEOS_SUBFOLDER
            final_base_video_path = process_and_copy_video_output(history, BASE_VIDEO_SAVE_NODE_TITLE, api_base_video_char_dir, base_video_filename_prefix)
            if final_base_video_path: success_count += 1; log_step(f" Base Video OK: {final_base_video_path.name}", l="success")
            else: raise ValueError("Fail process/copy BASE video")
        except Exception as e: log_step(f" Error generation for {approved_image_path.name}: {e}", level="error", exc_info=True); fail_count += 1

    log_step(f"--- Step 3: Summary ---", important=True)
    log_step(f"Total Approved Images Processed: {processed_count}")
    log_step(f"Base Video Clips Successfully Generated: {success_count}")
    log_step(f"Base Video Clips Failed: {fail_count}")
    log_step(f"Base videos saved within API Run Folder: {comfy_api_run_folder_abs}")
    return fail_count == 0

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate BASE Videos using ComfyUI.");
    parser.add_argument("-p", "--project_path", help="Project folder path. Omit for latest.")
    parser.add_argument("-l", "--length", type=int, default=DEFAULT_VIDEO_LENGTH, help=f"Number of frames for generated video (default: {DEFAULT_VIDEO_LENGTH})")
    args = parser.parse_args(); log_step("===== Generate Base Video Script Started =====", important=True)
    try: load_dotenv(dotenv_path=config.DOTENV_PATH)
    except Exception as e: log_step(f"Note: .env load error: {e}", level="debug")
    if not COMFYUI_INPUT_DIR or not COMFYUI_OUTPUT_DIR or not BASE_VIDEO_WORKFLOW_TEMPLATE: log_step("Critical Error: ComfyUI paths/template missing.", level="error", important=True); exit(1)
    if not Path(COMFYUI_INPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_INPUT_DIR invalid: {COMFYUI_INPUT_DIR}", level="error"); exit(1)
    if not Path(COMFYUI_OUTPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_OUTPUT_DIR invalid: {COMFYUI_OUTPUT_DIR}", level="error"); exit(1)
    if not Path(BASE_VIDEO_WORKFLOW_TEMPLATE).is_file() : log_step(f"Error: BASE_VIDEO_WORKFLOW_TEMPLATE not found: {BASE_VIDEO_WORKFLOW_TEMPLATE}", level="error"); exit(1)
    required_nodes = [BASE_VIDEO_POS_PROMPT_NODE_TITLE, BASE_VIDEO_NEG_PROMPT_NODE_TITLE, BASE_VIDEO_SEED_NODE_TITLE, BASE_VIDEO_START_IMAGE_NODE_TITLE, BASE_VIDEO_LENGTH_NODE_TITLE, BASE_VIDEO_OUTPUT_PREFIX_NODE_TITLE, BASE_VIDEO_SAVE_NODE_TITLE]
    if not all(required_nodes): log_step("Critical Error: Required BASE_VIDEO_*_NODE_TITLE missing.", level="error", important=True); exit(1)

    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not Path(project_to_process).parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", level="error", important=True); sys.exit(1)
    log_step(f"Using project: {project_to_process.name}")
    success = run_base_video_generation(project_to_process, args.length)
    if success: log_step("Script finished successfully.", level="success", important=True); sys.exit(0)
    else: log_step("Script finished with errors.", level="error", important=True); sys.exit(1)