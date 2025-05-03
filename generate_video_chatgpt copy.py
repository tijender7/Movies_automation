# generate_video.py v4
# Fixes finding source actor image with _# suffix.

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
    import config_v2 as config
    import comfyui_interactions as comfyui_interactions
    from generate_prompts_chatgpt import extract_theme_from_project_path, find_latest_project_dir
except ImportError as e:
    print(f"ERROR: Failed to import necessary modules: {e}")
    exit(1)

# --- Constants ---
LOG_FILE = getattr(config, 'LOG_FILE', Path("logs") / "generate_video.log")
PROJECTS_BASE_DIR = getattr(config, 'PROJECTS_BASE_DIR', Path(r"H:\projects\Movie_trailer\movie_vignette_generator\Movie_Projects"))
CHARACTERS_FOLDER_NAME = getattr(config, 'CHARACTERS_FOLDER_NAME', "characters")
SOURCE_ACTORS_FOLDER_NAME = getattr(config, 'SOURCE_ACTORS_FOLDER_NAME', "source_actors")
APPROVED_IMAGES_FOLDER_NAME = getattr(config, 'APPROVED_IMAGES_FOLDER_NAME', "Approved_images_for_videos")
VIDEO_PROMPT_OUTPUT_SUFFIX = ".video_prompts.json"
COMFYUI_INPUT_DIR = getattr(config, 'COMFYUI_INPUT_DIR', None)
COMFYUI_OUTPUT_DIR = getattr(config, 'COMFYUI_OUTPUT_DIR', None)
API_OUTPUTS_SUBDIR = getattr(config, 'API_OUTPUTS_SUBDIR', "API_OUTPUTS")
VIDEO_WORKFLOW_TEMPLATE = getattr(config, 'VIDEO_WORKFLOW_TEMPLATE', None)
FINAL_VIDEOS_FOLDER_NAME = getattr(config, 'FINAL_VIDEOS_FOLDER_NAME', "final_videos")
FINAL_VIDEO_CLIP_PREFIX = getattr(config, 'FINAL_VIDEO_CLIP_PREFIX', "final_clip_")
VIDEO_PROMPT_NODE_TITLE = getattr(config, 'VIDEO_PROMPT_NODE_TITLE', None)
VIDEO_SEED_NODE_TITLE = getattr(config, 'VIDEO_SEED_NODE_TITLE', None)
VIDEO_START_IMAGE_NODE_TITLE = getattr(config, 'VIDEO_START_IMAGE_NODE_TITLE', None)
VIDEO_FACE_NODE_TITLE = getattr(config, 'VIDEO_FACE_NODE_TITLE', None)
VIDEO_OUTPUT_PREFIX_NODE_TITLE = getattr(config, 'VIDEO_OUTPUT_PREFIX_NODE_TITLE', None)
VIDEO_OUTPUT_SAVE_NODE_TITLE = getattr(config, 'VIDEO_OUTPUT_SAVE_NODE_TITLE', None)
VIDEO_ORIGINAL_OUTPUT_PREFIX_NODE_TITLE = getattr(config, 'VIDEO_ORIGINAL_OUTPUT_PREFIX_NODE_TITLE', None)
COMFYUI_ORIGINAL_VIDEOS_SUBFOLDER = getattr(config, 'COMFYUI_ORIGINAL_VIDEOS_SUBFOLDER', None)
ORIGINAL_VIDEO_CLIP_PREFIX = getattr(config, 'ORIGINAL_VIDEO_CLIP_PREFIX', None)
VIDEO_ORIGINAL_SAVE_NODE_TITLE = getattr(config, 'VIDEO_ORIGINAL_SAVE_NODE_TITLE', 'mp4')

# --- Logging Setup ---
log_file_path = Path(LOG_FILE)
try:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s [%(filename)s:%(lineno)d] - %(message)s', handlers=[ logging.FileHandler(log_file_path, mode='a', encoding='utf-8'), logging.StreamHandler(sys.stdout) ] )
    logging.getLogger('urllib3').setLevel(logging.WARNING); logging.getLogger('werkzeug').setLevel(logging.WARNING); logging.getLogger('tensorflow').setLevel(logging.ERROR)
except Exception as e: print(f"ERROR logging setup: {e}"); logging.basicConfig( level=logging.INFO, format='%(asctime)s-%(levelname)s-%(message)s', handlers=[logging.StreamHandler()] )
log = logging.getLogger(__name__)

# --- Helper Functions ---
def log_step(m, l="info", i=False, important=None, level=None, exc_info=None):
    if important is not None: i = important
    if level is not None: l = level
    level_upper = l.upper()
    prefix_map = {"INFO": "[*]", "WARNING": "[!]", "ERROR": "[X]", "SUCCESS": "[+]", "DEBUG": "[D]"}
    final_msg = f"{prefix_map.get(level_upper, '[?]')} {m}"
    if i: final_msg = f"--- {final_msg} ---"

    log_level_int = getattr(logging, level_upper, logging.INFO)

    if exc_info:
        log.log(log_level_int, final_msg, exc_info=True)
    else:
        log.log(log_level_int, final_msg)

def sanitize_name(n):
    if not isinstance(n,str): n=str(n);
    s=re.sub(r'[<>:"/\\|?*\']','_', n); s="".join(c for c in s if c.isalnum() or c in (' ','_','-')).strip().replace(' ','_'); s=re.sub(r'[_]+','_',s); s=re.sub(r'[-]+','-',s); s=s.strip('_-'); return s[:60].strip('_-') or "invalid"

def copy_to_comfyui_input(source_file_path: Path):
    if not source_file_path.is_file(): log_step(f"Source copy fail: not found {source_file_path}", level="error"); return False
    if not COMFYUI_INPUT_DIR or not Path(COMFYUI_INPUT_DIR).is_dir(): log_step(f"Comfy Input Dir invalid: {COMFYUI_INPUT_DIR}", level="error"); return False
    try: dest = Path(COMFYUI_INPUT_DIR) / source_file_path.name; shutil.copy2(source_file_path, dest); log_step(f" Copied '{source_file_path.name}' to input", level="debug"); return True
    except Exception as e: log_step(f"Error copying '{source_file_path.name}': {e}", level="error"); return False

# --- FIXED: find_source_actor_image uses glob ---
def find_source_actor_image(actor_name_sanitized, source_actors_dir):
    """Finds the source actor image file using glob pattern matching."""
    log_step(f" Looking for source face matching '{actor_name_sanitized}_*.[jp][pn]g' in {source_actors_dir}", level="debug")
    search_pattern = str(source_actors_dir / f"{actor_name_sanitized}_*.[jp][pn]g")
    found_files = glob.glob(search_pattern) 

    if found_files:
        source_image_path = Path(found_files[0])
        if len(found_files) > 1:
            log_step(f" Warning: Found multiple source images for {actor_name_sanitized}: {[f.name for f in found_files]}. Using first: {source_image_path.name}", level="warning")
        log_step(f"  Found source face: {source_image_path.name}", level="success") 
        return source_image_path
    else:
        log_step(f" Source face image pattern NOT FOUND for {actor_name_sanitized}", level="warning")
        return None
# --- END FIX ---

def process_and_copy_video_output(comfyui_result_history, output_node_title, api_run_final_video_save_dir, project_filename_prefix):
    """Finds video output (looking for 'gifs' key from VHS node), copies to API run folder."""
    log_step(f" Processing ComfyUI output for node '{output_node_title}'...")
    output_details = comfyui_interactions.get_output_details_from_history(comfyui_result_history, output_node_title)
    output_filename = None
    output_subfolder = ""

    if output_details and isinstance(output_details, dict):
        log_step(f" Parsing output details for node '{output_node_title}'...", level="debug")
        if 'gifs' in output_details and isinstance(output_details['gifs'], list) and output_details['gifs']:
            video_info = output_details['gifs'][0] 
            if isinstance(video_info, dict):
                output_filename = video_info.get('filename')
                output_subfolder = video_info.get('subfolder', '')
                log_step(f"  Extracted from 'gifs': filename='{output_filename}', subfolder='{output_subfolder}'", level="debug")
            else:
                log_step(f" 'gifs'[0] is not a dictionary: {video_info}", level="warning")
        elif 'filename' in output_details:
             output_filename = output_details['filename']
             output_subfolder = output_details.get('subfolder', '')
             log_step(f" Extracted from direct 'filename' key", level="debug")


        if not output_filename:
            log_step(f" Cannot find 'filename' in output details for '{output_node_title}'. Details: {json.dumps(output_details)}", level="error")
            return None
        log_step(f" Found ComfyUI output: '{output_filename}' (Subfolder: '{output_subfolder}')", level="info")
    else:
        log_step(f" Cannot find valid output details for node '{output_node_title}'", level="error")
        log.debug(f" History dump: {str(comfyui_result_history)[:500]}")
        return None

    comfy_output_video_path = (Path(COMFYUI_OUTPUT_DIR).resolve() / output_subfolder / output_filename).resolve()
    comfy_stem, comfy_ext = os.path.splitext(output_filename)
    final_filename = f"{project_filename_prefix.strip('_')}_{comfy_stem}{comfy_ext}"
    api_run_final_video_path = api_run_final_video_save_dir / final_filename

    if comfy_output_video_path.is_file():
        try:
            api_run_final_video_save_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(comfy_output_video_path, api_run_final_video_path)
            log_step(f" Copied ComfyUI video -> {api_run_final_video_path.name}", level="success")
            return api_run_final_video_path
        except Exception as e:
            log_step(f" Failed copy output video '{comfy_output_video_path.name}': {e}", level="error")
            return None
    else:
        log_step(f" ComfyUI output video not found at: {comfy_output_video_path}", level="error")
        return None

# --- Main Logic ---
def run_video_generation(project_path: Path):
    log_step(f"--- Step 1: Setup & Validation ---", important=True)
    if not project_path or not project_path.is_dir(): log_step(f"Invalid project path: {project_path}", level="error"); return False
    approved_images_dir = project_path / APPROVED_IMAGES_FOLDER_NAME; source_actors_dir = project_path / SOURCE_ACTORS_FOLDER_NAME; project_characters_base_path = project_path / CHARACTERS_FOLDER_NAME
    if not approved_images_dir.is_dir(): log_step(f"Folder missing: {approved_images_dir}", level="error"); return False
    if not source_actors_dir.is_dir(): log_step(f"Folder missing: {source_actors_dir}", level="error"); return False
    if not project_characters_base_path.is_dir(): log_step(f"Folder missing: {project_characters_base_path}", level="error"); return False
    comfy_input_dir = Path(COMFYUI_INPUT_DIR); comfy_output_dir = Path(COMFYUI_OUTPUT_DIR); comfy_api_base_folder_abs = comfy_output_dir / API_OUTPUTS_SUBDIR
    try: comfy_api_base_folder_abs.mkdir(parents=True, exist_ok=True)
    except OSError as e: log_step(f"Cannot create API base output dir: {e}", level="error"); return False
    workflow_template = comfyui_interactions.load_workflow_template(VIDEO_WORKFLOW_TEMPLATE)
    if not workflow_template: log_step("Failed load video workflow.", level="error"); return False
    approved_image_paths = sorted(list(approved_images_dir.glob('*.[jp][pn]g')))
    if not approved_image_paths: log_step(f"No approved images in {approved_images_dir}", level="warning"); return True
    log_step(f"Found {len(approved_image_paths)} approved images.")
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S"); comfy_api_run_subfolder_rel = Path(f"{project_path.name}_{run_timestamp}"); comfy_api_run_folder_abs = comfy_api_base_folder_abs / comfy_api_run_subfolder_rel

    log_step(f"--- Step 2: Processing Approved Images for Video ---", important=True)
    success_count = 0; fail_count = 0; processed_count = 0; all_actors_processed_with_source = []

    for approved_image_path in approved_image_paths:
        processed_count += 1; log_step(f"--- Processing {processed_count}/{len(approved_image_paths)}: {approved_image_path.name} ---", important=True)
        filename_stem = approved_image_path.stem; parts = filename_stem.split("__")
        if len(parts) < 3: log_step(f" Skip bad name format: {filename_stem}", level="warning"); fail_count += 1; continue
        try: char_name_san, actor_name_san = parts[0], parts[1]; original_comfy_suffix = "__".join(parts[2:])
        except IndexError: log_step(f" Skip bad name split: {filename_stem}", level="warning"); fail_count += 1; continue
        log_step(f" Parsed: Actor='{actor_name_san}', Char='{char_name_san}'", level="debug")
        prompt_file_path = project_characters_base_path / actor_name_san / (filename_stem + VIDEO_PROMPT_OUTPUT_SUFFIX)
        if not prompt_file_path.is_file(): log_step(f" Prompts missing: {prompt_file_path.name}. Skip.", level="warning"); fail_count += 1; continue
        try: prompts = json.load(open(prompt_file_path, 'r', encoding='utf-8')); pos_prompt=prompts['positive_prompt']; neg_prompt=prompts['negative_prompt']
        except Exception as e: log_step(f" Error loading prompts {prompt_file_path.name}: {e}", level="error"); fail_count += 1; continue
        log_step(f" Loaded prompts for {filename_stem}", level="debug")
        source_actor_face_path = find_source_actor_image(actor_name_san, source_actors_dir)
        if not source_actor_face_path: log_step(f" Source face missing for {actor_name_san}. Skip.", level="warning"); fail_count += 1; continue
        if actor_name_san not in all_actors_processed_with_source: all_actors_processed_with_source.append(actor_name_san) 
        log_step(" Copying files to ComfyUI Input...", level="debug"); copy_ok_face = copy_to_comfyui_input(source_actor_face_path); copy_ok_start = copy_to_comfyui_input(approved_image_path)
        if not (copy_ok_face and copy_ok_start): log_step(" File copy fail. Skip.", level="error"); fail_count += 1; continue
        start_image_filename = approved_image_path.name; source_face_filename = source_actor_face_path.name

        try:
            current_workflow = copy.deepcopy(workflow_template); seed = random.randint(0, 2**32 - 1)
            comfy_char_actor_rel_path = Path(f"{char_name_san}__{actor_name_san}")
            prefix_node_swapped_dir_rel = Path(API_OUTPUTS_SUBDIR) / comfy_api_run_subfolder_rel.name / comfy_char_actor_rel_path / FINAL_VIDEOS_FOLDER_NAME
            final_video_filename_prefix = f"{FINAL_VIDEO_CLIP_PREFIX}{filename_stem}_"
            prefix_node_original_dir_rel = Path(API_OUTPUTS_SUBDIR) / comfy_api_run_subfolder_rel.name / comfy_char_actor_rel_path / COMFYUI_ORIGINAL_VIDEOS_SUBFOLDER
            original_video_filename_prefix = f"{ORIGINAL_VIDEO_CLIP_PREFIX}{filename_stem}_"
            inputs_to_set = {
                VIDEO_PROMPT_NODE_TITLE: {"positive_prompt": pos_prompt, "negative_prompt": neg_prompt},
                VIDEO_SEED_NODE_TITLE: {"seed": seed},
                VIDEO_START_IMAGE_NODE_TITLE: {"image": start_image_filename},
                VIDEO_FACE_NODE_TITLE: {"image": source_face_filename},
                VIDEO_OUTPUT_PREFIX_NODE_TITLE: {"custom_directory": prefix_node_swapped_dir_rel.as_posix(), "custom_text": "", "date_directory": "false" },
                VIDEO_ORIGINAL_OUTPUT_PREFIX_NODE_TITLE: {"custom_directory": prefix_node_original_dir_rel.as_posix(), "custom_text": "", "date_directory": "false" }
            }
            log_step(f" Modifying workflow (Seed: {seed})...", level="debug")
            if not comfyui_interactions.modify_workflow_inputs(current_workflow, inputs_to_set): raise ValueError("Failed modify workflow")
            log_step(f" Queueing video workflow for {filename_stem}...")
            comfyui_result_history = comfyui_interactions.run_comfyui_workflow(current_workflow, timeout=1800)
            if not comfyui_result_history: raise TimeoutError("ComfyUI timeout/fail")
            api_final_video_char_dir = comfy_api_run_folder_abs / comfy_char_actor_rel_path / FINAL_VIDEOS_FOLDER_NAME
            final_video_path = process_and_copy_video_output(comfyui_result_history, VIDEO_OUTPUT_SAVE_NODE_TITLE, api_final_video_char_dir, final_video_filename_prefix)
            api_original_video_char_dir = comfy_api_run_folder_abs / comfy_char_actor_rel_path / COMFYUI_ORIGINAL_VIDEOS_SUBFOLDER
            original_video_path = process_and_copy_video_output(comfyui_result_history, VIDEO_ORIGINAL_SAVE_NODE_TITLE, api_original_video_char_dir, original_video_filename_prefix)
            if final_video_path: success_count += 1; log_step(f" Video OK: {final_video_path.name}", level="success")
            else: raise ValueError("Failed process/copy video output")
            if original_video_path: log_step(f" Original video OK: {original_video_path.name}", level="info")
            else: log_step("Original video output not found or failed to copy.", level="warning")
        except Exception as e: log_step(f" Error generation for {approved_image_path.name}: {e}", level="error", exc_info=True); fail_count += 1

    log_step(f"--- Step 4: Summary ---", important=True)
    log_step(f"Total Approved Images Processed: {processed_count}")
    log_step(f"Video Clips Successfully Generated: {success_count}")
    log_step(f"Video Clips Failed: {fail_count}")
    return fail_count == 0

# --- Script Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Videos using ComfyUI."); parser.add_argument("-p", "--project_path", help="Project folder path. Omit for latest.")
    args = parser.parse_args(); log_step("===== Generate Video Script Started =====", important=True)
    try: load_dotenv(dotenv_path=config.DOTENV_PATH)
    except Exception as e: log_step(f"Note: .env load error: {e}", level="debug")
    if not COMFYUI_INPUT_DIR or not COMFYUI_OUTPUT_DIR or not VIDEO_WORKFLOW_TEMPLATE: log_step("Critical Error: ComfyUI paths/template missing.", level="error", important=True); exit(1)
    if not Path(COMFYUI_INPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_INPUT_DIR invalid: {COMFYUI_INPUT_DIR}", level="error"); exit(1)
    if not Path(COMFYUI_OUTPUT_DIR).is_dir() : log_step(f"Error: COMFYUI_OUTPUT_DIR invalid: {COMFYUI_OUTPUT_DIR}", level="error"); exit(1)
    if not Path(VIDEO_WORKFLOW_TEMPLATE).is_file() : log_step(f"Error: VIDEO_WORKFLOW_TEMPLATE not found: {VIDEO_WORKFLOW_TEMPLATE}", level="error"); exit(1)
    if not all([VIDEO_PROMPT_NODE_TITLE, VIDEO_SEED_NODE_TITLE, VIDEO_START_IMAGE_NODE_TITLE, VIDEO_FACE_NODE_TITLE, VIDEO_OUTPUT_PREFIX_NODE_TITLE, VIDEO_OUTPUT_SAVE_NODE_TITLE]): log_step("Critical Error: VIDEO_*_NODE_TITLE missing in config.", level="error", important=True); exit(1)

    project_to_process = None
    if args.project_path: project_to_process = Path(args.project_path);
    if not project_to_process or not project_to_process.is_dir() or not Path(project_to_process).parent.samefile(PROJECTS_BASE_DIR):
        log_step(f"Finding latest project in {PROJECTS_BASE_DIR}..."); project_to_process = find_latest_project_dir(PROJECTS_BASE_DIR)
        if not project_to_process: log_step(f"No project found.", level="error", important=True); sys.exit(1)
    log_step(f"Using project: {project_to_process.name}"); project_theme = extract_theme_from_project_path(project_to_process) 

    if not hasattr(comfyui_interactions, 'load_workflow_template') or not hasattr(comfyui_interactions, 'modify_workflow_inputs') or not hasattr(comfyui_interactions, 'run_comfyui_workflow') or not hasattr(comfyui_interactions, 'get_output_details_from_history'): log_step("Critical Error: Essential functions missing from comfyui_interactions.py!", level="error", important=True); exit(1)

    success = run_video_generation(project_to_process)
    if success: log_step("Script finished successfully.", level="success", important=True); sys.exit(0)
    else: log_step("Script finished with errors.", level="error", important=True); sys.exit(1)