import os
import cv2  # OpenCV for video processing
import base64
import requests
import json
from pathlib import Path
import time

# --- Configuration ---
VIDEO_FOLDER = r"H:\dancers_content\API_OUTPUTS\sholay_Space_Opera_Galaxy_20250427_105047_VIDEO_20250428_174022\upscaled_videos"
VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.webm') # Add other extensions if needed
NUM_KEYFRAMES = 4
OLLAMA_API_URL = "http://localhost:11434/api/generate" # Default Ollama API endpoint
OLLAMA_MODEL = "gemma3:12b"
# --- End Configuration ---

def find_latest_video(folder_path):
    """Finds the most recently modified video file in a folder."""
    latest_file = None
    latest_mtime = 0

    try:
        video_files = [p for p in Path(folder_path).glob('*') if p.suffix.lower() in VIDEO_EXTENSIONS]
        if not video_files:
            print(f"Error: No video files found in {folder_path}")
            return None

        latest_file = max(video_files, key=lambda p: p.stat().st_mtime)
        print(f"Found latest video: {latest_file.name}")
        return str(latest_file) # Return as string path

    except FileNotFoundError:
        print(f"Error: Folder not found - {folder_path}")
        return None
    except Exception as e:
        print(f"Error finding latest video: {e}")
        return None

def extract_keyframes(video_path, num_frames):
    """Extracts a specified number of keyframes evenly spaced from a video."""
    frames_data = []
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        print(f"Error: Could not open video file: {video_path}")
        return None

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames < num_frames:
        print(f"Warning: Video has only {total_frames} frames, extracting all of them.")
        num_frames = total_frames # Adjust if video is shorter than requested frames

    if total_frames <= 0:
        print(f"Error: Video file seems to have no frames: {video_path}")
        cap.release()
        return None

    print(f"Video has {total_frames} total frames. Extracting {num_frames} keyframes...")

    # Calculate indices for evenly spaced frames (avoiding exact start/end)
    indices = [int(total_frames * (i + 1) / (num_frames + 1)) for i in range(num_frames)]

    count = 0
    for frame_index in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ret, frame = cap.read()
        if ret:
            frames_data.append(frame)
            count += 1
            print(f"  Extracted frame at index {frame_index} ({count}/{num_frames})")
        else:
            print(f"  Warning: Could not read frame at index {frame_index}")

    cap.release()

    if not frames_data:
        print("Error: Failed to extract any frames.")
        return None

    print(f"Successfully extracted {len(frames_data)} frames.")
    return frames_data

def encode_image_to_base64(frame):
    """Encodes an OpenCV frame (numpy array) to a Base64 string."""
    # Encode the frame to PNG format in memory
    success, buffer = cv2.imencode('.png', frame)
    if not success:
        print("Error: Failed to encode frame to PNG.")
        return None
    # Encode the PNG buffer to Base64
    base64_encoded = base64.b64encode(buffer).decode('utf-8')
    return base64_encoded

def ask_ollama_for_sfx(prompt, base64_images, model_name, api_url):
    """Sends the prompt and images to Ollama and returns the response."""
    payload = {
        "model": model_name,
        "prompt": prompt,
        "images": base64_images,
        "stream": False # Get the full response at once
    }

    headers = {'Content-Type': 'application/json'}

    print(f"\nSending request to Ollama (model: {model_name})...")
    try:
        response = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=300) # Increased timeout
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        response_data = response.json()
        print("Received response from Ollama.")
        return response_data.get("response", "Error: 'response' key not found in Ollama output.")

    except requests.exceptions.Timeout:
        print(f"Error: Request to Ollama timed out after 300 seconds.")
        return "Error: Ollama request timed out."
    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to Ollama API at {api_url}. Is Ollama running?")
        return "Error: Ollama connection failed."
    except requests.exceptions.RequestException as e:
        print(f"Error during Ollama API request: {e}")
        # Try to print detailed error from Ollama if available
        try:
            error_details = response.json()
            print(f"Ollama error details: {error_details}")
        except Exception:
            pass # Ignore if response is not JSON or doesn't exist
        return f"Error: Ollama request failed: {e}"
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON response from Ollama: {response.text}")
        return "Error: Invalid JSON response from Ollama."
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return f"Error: An unexpected error occurred: {e}"

# --- Main Execution ---
if __name__ == "__main__":
    start_time = time.time()

    # 1. Find the latest video
    latest_video_path = find_latest_video(VIDEO_FOLDER)

    if latest_video_path:
        # 2. Extract keyframes
        keyframes = extract_keyframes(latest_video_path, NUM_KEYFRAMES)

        if keyframes:
            # 3. Encode frames to Base64
            base64_frames = []
            print("\nEncoding frames to Base64...")
            for i, frame in enumerate(keyframes):
                encoded = encode_image_to_base64(frame)
                if encoded:
                    base64_frames.append(encoded)
                    print(f"  Encoded frame {i+1}/{len(keyframes)}")
                else:
                    print(f"  Failed to encode frame {i+1}")

            if len(base64_frames) == len(keyframes): # Check if all frames were encoded
                # 4. Prepare the prompt
                prompt = (
                    f"These {len(base64_frames)} images are sequential keyframes extracted from a "
                    f"short video clip (likely around 5-10 seconds long, judging by the number of frames). "
                    f"Please analyze the visual content, objects, setting, atmosphere, and any implied actions "
                    f"or events depicted in these frames. Based on your analysis, suggest a list of specific "
                    f"sound effects that would be appropriate to add to this video clip. "
                    f"For each suggestion, briefly explain why it's relevant and potentially where it might fit "
                    f"in the timeline (e.g., 'footsteps throughout', 'laser blast near frame 2', "
                    f"'ambient space hum', 'character dialogue snippet', 'impact sound between frame 3 and 4'). "
                    f"Be creative and consider both diegetic (sounds originating from within the scene) and "
                    f"non-diegetic (like background music or mood sounds) possibilities."
                )

                # 5. Send to Ollama and get response
                sfx_suggestions = ask_ollama_for_sfx(prompt, base64_frames, OLLAMA_MODEL, OLLAMA_API_URL)

                # 6. Print the result
                print("\n--- Ollama Sound Effect Suggestions ---")
                print(sfx_suggestions)
                print("--- End of Suggestions ---")

            else:
                print("\nError: Not all frames could be encoded to Base64. Aborting Ollama request.")
        else:
            print("\nError: Frame extraction failed. Cannot proceed.")
    else:
        print("\nError: Could not find a video file to process.")

    end_time = time.time()
    print(f"\nScript finished in {end_time - start_time:.2f} seconds.")