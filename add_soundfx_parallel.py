# add_soundfx_parallel.py
import os, sys, subprocess, logging, time, concurrent.futures
from pathlib import Path
from datetime import datetime

# ─── 1. CONFIG ────────────────────────────────────────────────────────────────
API_OUTPUTS_BASE_DIR = Path(r"H:/dancers_content/API_OUTPUTS")
UPSCALED_SUBFOLDER      = "upscaled_videos"
SFX_OUTPUT_SUBFOLDER    = "upscaled_with_sound_effects"

# Gradio client endpoint
GRADIO_URL = "http://127.0.0.1:7860"  
# adjust if your server is on another host/port

# Concurrency: number of videos to process at once
MAX_WORKERS = 2

# ─── 2. LOGGING ───────────────────────────────────────────────────────────────
script_dir = Path(__file__).parent
logs = script_dir / "logs"
logs.mkdir(exist_ok=True)
logfile = logs / f"add_sfx_{datetime.now():%Y%m%d_%H%M%S}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(logfile, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger()

# ─── 3. HELPERS ───────────────────────────────────────────────────────────────
def get_latest_video_folder(base_dir: Path) -> Path:
    """Find the most recently modified folder containing '_VIDEO_'."""
    candidates = [d for d in base_dir.iterdir() if d.is_dir() and "_VIDEO_" in d.name]
    if not candidates:
        logger.critical(f"No '_VIDEO_' folders in {base_dir}")
        sys.exit(1)
    latest = max(candidates, key=lambda d: d.stat().st_mtime)
    logger.info(f"Latest video batch: {latest.name}")
    return latest

def get_video_duration(path: Path) -> float:
    """Return video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe","-v","error",
        "-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return float(res.stdout.strip() or 0)

def generate_sfx_audio(video_path: Path, temp_audio: Path) -> bool:
    """Call Gradio '/generate' to make an SFX audio file."""
    from gradio_client import Client, handle_file

    client = Client(GRADIO_URL)
    duration = get_video_duration(video_path)

    try:
        # result → (video_dict, audio_filepath)
        _, audio_fp = client.predict(
            prompt="Generate dynamic sound effects for this video",
            video_file=handle_file(str(video_path)),
            video_path=None,
            audio_prompt_file=None,
            audio_prompt_path=None,
            seconds_start=0,
            seconds_total=duration,
            cfg_scale=7,
            steps=250,
            preview_every=0,
            seed="-1",
            sampler_type="dpmpp-3m-sde",
            sigma_min=0.03,
            sigma_max=500,
            cfg_rescale=0,
            use_init=False,
            init_audio=None,
            init_noise_level=0.1,
            api_name="/generate"
        )
        # move or copy returned audio to our temp_audio
        os.replace(audio_fp, str(temp_audio))
        return True

    except Exception as e:
        logger.error(f"Failed AI SFX gen for {video_path.name}: {e}")
        return False

def merge_audio(video_in: Path, audio_in: Path, video_out: Path) -> bool:
    """Replace the video's audio track with audio_in via ffmpeg."""
    cmd = [
        "ffmpeg","-y",
        "-i", str(video_in),
        "-i", str(audio_in),
        "-map","0:v:0","-map","1:a:0",
        "-c:v","copy","-c:a","aac","-b:a","192k",
        str(video_out)
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        logger.error(f"ffmpeg merge failed for {video_in.name}: {res.stderr.decode()}")
        return False
    return True

def process_video(task):
    """Full pipeline for one video: AI gen → merge → cleanup."""
    idx, video_path, out_folder = task
    name = video_path.stem
    logger.info(f"[{idx}] Starting SFX for: {video_path.name}")

    tmp_audio = out_folder / f"{name}_sfx_temp.wav"
    final_video = out_folder / f"{name}_with_sfx.mp4"

    # 1) Generate SFX audio
    ok = generate_sfx_audio(video_path, tmp_audio)
    if not ok:
        return False

    # 2) Merge into final video
    ok = merge_audio(video_path, tmp_audio, final_video)
    if not ok:
        return False

    # 3) Cleanup temp audio
    try:
        tmp_audio.unlink()
    except:
        pass

    logger.info(f"[{idx}] Done → {final_video.name}")
    return True

# ─── 4. MAIN ─────────────────────────────────────────────────────────────────
if __name__=="__main__":
    logger.info("Starting parallel sound-FX replacement…")

    latest_folder = get_latest_video_folder(API_OUTPUTS_BASE_DIR)
    upscaled_dir  = latest_folder / UPSCALED_SUBFOLDER
    sfx_dir       = latest_folder / SFX_OUTPUT_SUBFOLDER
    sfx_dir.mkdir(exist_ok=True)

    videos = sorted(upscaled_dir.glob("*.mp4"))
    if not videos:
        logger.warning(f"No .mp4 in {upscaled_dir}")
        sys.exit(0)

    tasks = [(i+1, vid, sfx_dir) for i, vid in enumerate(videos)]
    logger.info(f"{len(tasks)} videos to process → storing in {sfx_dir.name}")

    success = 0
    fail    = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for ok in ex.map(process_video, tasks):
            if ok: success += 1
            else:  fail    += 1

    logger.info(f"Finished: {success} succeeded, {fail} failed.")
