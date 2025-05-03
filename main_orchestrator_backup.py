# main_orchestrator.py
import ollama
import requests
import json
import os
import logging
import time
import shutil # Added for file operations
from dotenv import load_dotenv
from pathlib import Path
# from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip # Import later
import face_recognition  # Add this import at the top
import cv2
import google.generativeai as genai
from google.generativeai import types
import base64

# --- Basic Configuration --- (Keep as before)
# Ensure .env is loaded from the script directory
from pathlib import Path as _Path
DOTENV_PATH = _Path(__file__).parent / ".env"
load_dotenv(dotenv_path=DOTENV_PATH)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# DEBUG: Print loaded value and dump related env vars
print("DEBUG: TAVILY_API_KEY =", repr(TAVILY_API_KEY))
print("DEBUG: GEMINI_API_KEY =", repr(GEMINI_API_KEY))
import os as _os
for k, v in _os.environ.items():
    if k.startswith("TAVILY"):
        print(f"DEBUG ENV: {k} = {repr(v)}")
    if k.startswith("GEMINI"):
        print(f"DEBUG ENV: {k} = {repr(v)}")
if not TAVILY_API_KEY:
    logging.error("TAVILY_API_KEY not found after loading .env! Make sure .env is in the script directory and contains the correct key.")
else:
    logging.info("TAVILY_API_KEY loaded successfully from .env.")
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY not found after loading .env! Make sure .env is in the script directory and contains the correct key.")
else:
    logging.info("GEMINI_API_KEY loaded successfully from .env.")
OLLAMA_MODEL = 'gemma3:12b'
LOG_FILE = 'orchestrator_log.log'
PROJECTS_BASE_DIR = Path("Movie_Projects")
COMFYUI_URL = "http://127.0.0.1:8188"
