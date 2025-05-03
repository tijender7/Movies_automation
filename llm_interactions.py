import ollama
import google.generativeai as genai
import json
import logging
import os
from pathlib import Path

# call_ollama_stream

def call_ollama_stream(prompt_content, task_description, ollama_model_name):
    """
    Calls Ollama, streams response, logs interaction, returns parsed JSON.
    Can handle text-only or text+image prompts.

    Args:
        prompt_content (dict): Can be a simple dict like {"role": "user", "content": "text"}
                              OR a dict with images like
                              {"role": "user", "content": "text", "images": ["/path/to/img.png"]}
        task_description (str): Description for logging.
        ollama_model_name (str): Name of the Ollama model to use.
    """
    log = logging.getLogger(__name__)
    log.info(f"--- Starting Ollama Task: {task_description} ---")
    # Log differently based on whether images are present
    if "images" in prompt_content and prompt_content["images"]:
        log.info(f"Sending prompt text and {len(prompt_content['images'])} image(s) to Ollama ({ollama_model_name}).")
        log.debug(f"Prompt Text Context:\n{prompt_content.get('content', '')[:500]}...") # Log start of text
        log.debug(f"Image Paths: {prompt_content['images']}")
    else:
        log.info(f"Sending text prompt to Ollama ({ollama_model_name}):\n{prompt_content.get('content', '')[:500]}...")

    full_response = ""
    try:
        # Use the single message dictionary directly
        stream = ollama.chat(
            model=ollama_model_name,
            messages=[prompt_content], # Pass the prepared dictionary
            stream=True,
            # format='json' # Request JSON - seems hit-or-miss with Ollama models, parse manually
        )

        print(f"Ollama response stream ({task_description}):") # Keep user informed
        for chunk in stream:
            if 'message' in chunk and 'content' in chunk['message']:
                content_chunk = chunk['message']['content']
                print(content_chunk, end='', flush=True)
                full_response += content_chunk
            elif chunk.get('done'): pass # Handle completion if needed
            else: log.warning(f"Received unexpected chunk structure: {chunk}")
        print("\n--- Stream End ---")

        # --- Parsing the response ---
        # Clean potential markdown ```json ... ``` markers
        cleaned_response = full_response.strip()
        if cleaned_response.startswith("```json"):
            cleaned_response = cleaned_response[7:]
        if cleaned_response.endswith("```"):
            cleaned_response = cleaned_response[:-3]
        cleaned_response = cleaned_response.strip()

        try:
            parsed_json = json.loads(cleaned_response)
            log.info(f"Ollama Task '{task_description}' completed. Parsed JSON.")
            log.debug(f"Full Response JSON:\n{json.dumps(parsed_json, indent=2)}")
            return parsed_json
        except json.JSONDecodeError as json_err:
            log.error(f"Ollama Task '{task_description}' response was not valid JSON. Error: {json_err}")
            log.error(f"Raw Response (cleaned):\n{cleaned_response}")
            return None

    except Exception as e:
        log.error(f"Error during Ollama call for '{task_description}': {e}", exc_info=True)
        # Check for specific vision error messages if possible
        if "does not support images" in str(e).lower():
             log.error(f"*** The model {ollama_model_name} reported it does not support images. ***")
        return None


def get_movie_info_google_search(movie_name, gemini_api_key):
    """
    Uses Google GenAI API to get structured movie info.
    Requires the API key to be passed.
    """
    logging.info(f"--- Starting Google GenAI Task: Get Movie Info for {movie_name} ---")
    if not gemini_api_key:
        logging.error("GEMINI_API_KEY not provided to function.")
        return None
    try:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        prompt = f"""
Analyze the movie \"{movie_name}\". Using your available tools (like web search) to ensure accuracy, provide the following information strictly in JSON format:
1.  `director`: The main director's name (string).
2.  `release_year`: The original release year (integer YYYY).
3.  `brief_synopsis`: A concise 1-2 sentence summary of the plot.
4.  `main_characters_actors`: A JSON list containing **at least 6-8 prominent main AND iconic supporting characters** and their corresponding actors. Include characters widely recognized even if their screen time isn't the longest. Each element in the list should be a JSON object with keys 'character_name' (string) and 'actor_name' (string).

Example JSON output format:
{{
  "director": "Example Director Name",
  "release_year": 1999,
  "brief_synopsis": "A brief plot summary goes here.",
  "main_characters_actors": [
    {{"character_name": "Main Character 1", "actor_name": "Actor A"}},
    # ... more examples ...
  ]
}}

Provide only the JSON object in your response.
"""
        logging.info(f"Sending prompt to Google GenAI ({model.model_name})...")
        response = model.generate_content(prompt)
        raw_response_text = response.text.strip()
        cleaned_response_text = raw_response_text # Placeholder for cleaning
        parsed_json = json.loads(cleaned_response_text)
        return parsed_json
    except Exception as e:
        logging.error(f"Error during Google GenAI API call for '{movie_name}': {e}")
        return None


def get_character_details(movie_name, character_name, actor_name, ollama_model_name):
    """Gets specific details for one character using Ollama."""
    prompt_data = {
        # ... fill in as per your prompt ...
    }
    details = call_ollama_stream(prompt_data, f"Get Details for {character_name}", ollama_model_name)
    if details:
        logging.info(f"Successfully retrieved details for {character_name}.")
        return details
    else:
        logging.error(f"Failed to retrieve details for {character_name}.")
        return None

# --- NEW FUNCTION ---
def generate_flux_prompt(character_name, actor_name, character_data, theme, ollama_model_name):
    """
    Generates a descriptive image prompt for Flux, STRONGLY emphasizing a
    front-facing or slightly angled face suitable for face swapping, within a wider shot.
    """
    logging.info(f"--- Starting Ollama Task: Generate Face-Swap Ready Flux Prompt for {character_name} ({theme}) ---")
    traits = character_data.get("key_personality_traits", ["unknown traits"])
    description = character_data.get("physical_description_in_movie", "standard appearance")
    iconic_scene = character_data.get("iconic_scene_description", "a typical scene")
    identifier = "person" # Default
    if "male" in description.lower() or any(t in traits for t in ["Heroic", "Stoic", "Brave", "Father"]):
         identifier = "man"
    elif "female" in description.lower() or any(t in traits for t in ["Vivacious", "Graceful", "Mother", "Heroine"]):
         identifier = "woman"

    prompt_data = {
        "task": "generate_detailed_image_prompt_for_flux_face_swap", # More specific task
        "character_inspiration": {
            "identifier": identifier,
            "actor_inspiration_name": actor_name,
            "original_character_name": character_name,
            "traits": traits,
            "original_description": description,
            "iconic_scene_context": iconic_scene
        },
        "style_theme": theme,
        "target_model": "Flux (Image Generation)",
        "instructions": (
            f"Generate a highly detailed and evocative text prompt for the Flux image generation model. "
            f"The prompt must describe a **single-character scene** featuring the '{identifier}' inspired by '{character_name}' (played by '{actor_name}'), reimagined within the '{theme}' aesthetic."
            f"\n**CRITICAL REQUIREMENTS FOR FACE SWAPPING:**" # Make requirements very clear
            f"\n1. **FACE ANGLE:** The character's face **MUST** be clearly visible and oriented **directly towards the camera (front-facing)** or, at most, a **slight three-quarter (3/4) view**. **NO PROFILES, NO looking away, NO significantly tilted heads.** The face swap needs a clear view."
            f"\n2. **SHOT TYPE:** Use **'medium shot', 'cowboy shot', or 'upper body portrait'**. While showing context is good, ensure the face remains large and clear enough within the frame, adhering to Requirement 1."

            f"\n**Describe the following elements in detail, ensuring they support the critical requirements:**"
            f"\n- **Appearance:** Detailed futuristic clothing, gear, cybernetics, etc., appropriate for the '{theme}'. Visible from the chosen shot type."
            f"\n- **Pose/Action:** A specific pose or subtle action that **ALLOWS for the required front-facing or slight 3/4 face angle**. Examples: 'standing alert facing forward', 'leaning slightly against a wall looking towards viewer', 'adjusting collar while facing camera', 'holding an object but head turned towards viewer'."
            f"\n- **Facial Expression/Emotion:** Clearly specify the emotion visible on the **correctly oriented face** (e.g., 'stern gaze directed at camera', 'subtle smirk towards viewer', 'thoughtful expression looking slightly past camera')."
            f"\n- **Environment:** A detailed background matching the '{theme}', providing context without obscuring the character."
            f"\n- **Lighting & Atmosphere:** Ensure lighting illuminates the face clearly. Describe mood."
            f"\n- **Composition:** Explicitly state the shot type (medium, cowboy, upper body) AND reinforce the **front-facing or slight 3/4 face orientation** (e.g., 'medium shot, character facing camera', 'cowboy shot, head turned slightly towards viewer (3/4 view)')."
            f"\n**IMPORTANT:** Do NOT use original character/actor names directly in the description. Focus on VISUALS meeting the face angle requirement."
            f"\nUse descriptive, comma-separated concepts suitable for Flux, aiming for high detail and face clarity."
        ),
         "output_format_instructions": "Respond strictly with a JSON object containing a single key 'flux_image_prompt' with the generated prompt string.",
         "example_output": {
             # Updated example emphasizing face angle
            "flux_image_prompt": "cinematic lighting, detailed cyberphunk alleyway, rain puddles reflecting neon, medium shot of a stoic man inspired by Amitabh Bachchan's 1970s look, wearing worn black synth-leather duster coat with glowing circuitry, face clearly visible looking directly at camera, stern expression, augmented reality glasses active, dynamic background, high detail, 8k"
        }
    }

    # Call Ollama
    message = {
        "role": "user",
        "content": json.dumps(prompt_data, ensure_ascii=False)
    }
    response = call_ollama_stream(message, f"Generate Flux Prompt for {character_name}", ollama_model_name)
    # ... (Parse and return response['flux_image_prompt'] or None) ...
    if response and isinstance(response.get("flux_image_prompt"), str) and response["flux_image_prompt"]:
        logging.info(f"Successfully generated Flux prompt for {character_name}.")
        return response["flux_image_prompt"]
    else:
        logging.error(f"Failed to generate valid Flux prompt for {character_name}. Response: {response}")
        return None

def generate_flux_prompt_full_body(character_name, actor_name, character_data, theme, ollama_model_name):
    """
    Generates a descriptive image prompt for Flux specifically requesting a FULL BODY shot,
    while still attempting to maintain a clear face angle.
    """
    logging.info(f"--- Starting Ollama Task: Generate Face-Swap Ready FULL BODY Flux Prompt for {character_name} ({theme}) ---")
    traits = character_data.get("key_personality_traits", ["unknown traits"])
    description = character_data.get("physical_description_in_movie", "standard appearance")
    iconic_scene = character_data.get("iconic_scene_description", "a typical scene")
    identifier = "person"
    if "male" in description.lower() or any(t in traits for t in ["Heroic", "Stoic", "Brave", "Father"]):
        identifier = "man"
    elif "female" in description.lower() or any(t in traits for t in ["Vivacious", "Graceful", "Mother", "Heroine"]):
        identifier = "woman"

    prompt_data = {
        "task": "generate_detailed_image_prompt_for_flux_face_swap_full_body",
        "character_inspiration": {
            "identifier": identifier,
            "actor_inspiration_name": actor_name,
            "original_character_name": character_name,
            "traits": traits,
            "original_description": description,
            "iconic_scene_context": iconic_scene
        },
        "style_theme": theme,
        "target_model": "Flux (Image Generation)",
        "instructions": (
            f"Generate a highly detailed and evocative text prompt for Flux. "
            f"Describe a **single-character scene** featuring '{identifier}' inspired by '{character_name}' in the '{theme}' aesthetic."
            f"\n**CRITICAL REQUIREMENTS:**"
            f"\n1. **SHOT TYPE:** MUST be a **'full body shot'** showing the character from head to toe."
            f"\n2. **FACE ANGLE:** The face **MUST** still be clearly visible and oriented **front-facing or slight 3/4 view**. NO PROFILES. Adapt pose/action to achieve this within the full body shot."
            f"\n**Describe the following, ensuring consistency with critical requirements:**"
            f"\n- **Appearance:** Detailed futuristic clothing/gear from **head to toe**."
            f"\n- **Pose/Action:** A **full body pose** or action reflecting traits/scene, ensuring the required face angle is maintained."
            f"\n- **Facial Expression:** Clear emotion visible on the correctly oriented face."
            f"\n- **Environment:** Detailed background providing context for the full body shot."
            f"\n- **Lighting & Atmosphere:** Describe lighting and mood."
            f"\n- **Composition:** Explicitly state **'full body shot'** AND the **'front-facing or slight 3/4 face orientation'**."
            f"\n**IMPORTANT:** Focus on VISUALS meeting BOTH shot type and face angle requirements."
            f"\nUse descriptive, comma-separated concepts."
        ),
        "output_format_instructions": "Respond strictly with a JSON object containing a single key 'flux_image_prompt' with the generated prompt string.",
        "example_output": {
            "flux_image_prompt": "cinematic lighting, detailed cyberphunk street, rain, full body shot of a stoic man inspired by Amitabh Bachchan's 1970s look, wearing worn black synth-leather duster coat, armored boots, face clearly visible looking directly at camera, stern expression, holding a futuristic weapon low, dynamic background, high detail, 8k"
        }
    }
    message = {
        "role": "user",
        "content": json.dumps(prompt_data, ensure_ascii=False)
    }
    response = call_ollama_stream(message, f"Generate Full Body Flux Prompt for {character_name}", ollama_model_name)
    if response and isinstance(response.get("flux_image_prompt"), str) and response["flux_image_prompt"]:
        logging.info(f"Successfully generated Full Body Flux prompt for {character_name}.")
        return response["flux_image_prompt"]
    else:
        logging.error(f"Failed to generate valid Full Body Flux prompt for {character_name}. Response: {response}")
        return None

# --- NEW FUNCTION for Video Prompt Generation ---
def generate_wan_video_prompt_from_image(
    approved_image_path: Path,
    movie_name: str,
    theme: str,
    character_name: str,
    actor_name: str,
    character_data: dict,
    ollama_model_name: str
    ):
    """
    Uses Ollama Vision to generate **ACTION-ORIENTED** positive and negative
    prompts suitable for WanVideo based on an image and character context.
    Aims for more dynamic movement than just subtle shifts.

    Returns:
        tuple[str | None, str | None]: (positive_video_prompt, negative_video_prompt) or (None, None)
    """
    task_desc = f"Generate ACTION WanVideo Prompts for {character_name} ({approved_image_path.name})"
    log = logging.getLogger(__name__)
    log.info(f"--- Starting Ollama Vision Task: {task_desc} ---")

    if not approved_image_path.is_file():
        log.error(f"Approved image file not found: {approved_image_path}")
        return None, None

    traits = character_data.get("key_personality_traits", [])

    # --- ACTION-FOCUSED PROMPT FOR OLLAMA ---
    action_prompt_instructions = f"""
Analyze the provided image showing '{character_name}' (inspired by {actor_name}) in a '{theme}' style.
Based on the image's pose, character traits ({', '.join(traits)}), and potential implied action, suggest a **dynamic but short (1-3 second loopable) action** the character could perform next.

Think TRAILER MOMENT - not subtle breathing. Consider:
- **Heroic:** Quick readying of a weapon, determined stride forward (just starting), turning sharply towards danger, activating a tech gadget.
- **Villainous:** Throwing head back in a menacing laugh (starts laughing), slamming fist onto a surface (just the impact), pointing aggressively, a dramatic cloak swirl.
- **Energetic/Playful:** A quick, stylish spin, blowing a kiss, striking a sudden pose, a joyful leap starting.
- **Object Interaction:** Dramatically raising or activating an object, throwing a small object.
- **Camera Movement:** Instead of subtle, suggest a more noticeable camera move if no character action fits: "fast whip pan reveals...", "rapid zoom into eyes", "dynamic orbiting shot".

Task:
1. Choose ONE dynamic, short, loopable action or camera move based on the image and context.
2. Create a concise POSITIVE prompt phrase describing ONLY that action (e.g., "man quickly draws plasma pistol", "woman spins gracefully throwing sparkles", "camera zooms rapidly into glowing cybernetic eye", "villain slams fist down"). Aim for clear, strong verbs. Keep it reasonably short (under 15 words).
3. Create a standard NEGATIVE prompt focusing on common video artifacts. Start with `NEG: `.

Output Format: Respond strictly with a JSON object containing exactly two keys: 'positive_prompt' and 'negative_prompt'. NO other text or commentary.

Example JSON Response:
{{"positive_prompt": "cyborg warrior dramatically raises energy shield", "negative_prompt": "NEG: blurry, noisy, text, watermark, deformation, bad quality, duplicate limbs"}}
"""
    # --- END ACTION-FOCUSED PROMPT ---

    log.info(f"Sending image {approved_image_path.name} and ACTION instructions to Ollama Vision ({ollama_model_name})...")

    try:
        image_path_str = str(approved_image_path.resolve())
        ollama_payload = {
            "role": "user",
            "content": action_prompt_instructions,
            "images": [image_path_str]
        }

        # Use the adapted call_ollama_stream expecting JSON
        response_json = call_ollama_stream(ollama_payload, task_desc, ollama_model_name)

        # Validate the specific structure we requested
        if response_json and \
           isinstance(response_json.get("positive_prompt"), str) and \
           isinstance(response_json.get("negative_prompt"), str) and \
           response_json["positive_prompt"] and \
           response_json["negative_prompt"].startswith("NEG:"):

            pos_prompt = response_json["positive_prompt"].strip()
            neg_prompt = response_json["negative_prompt"].strip()
            log.info(f"Successfully generated ACTION WanVideo prompts:")
            log.info(f"  Positive: '{pos_prompt}'")
            log.info(f"  Negative: '{neg_prompt}'")
            return pos_prompt, neg_prompt
        else:
            log.error(f"Failed to generate valid structured WanVideo prompts (pos/neg keys missing or invalid). Response: {response_json}")
            return None, None

    except Exception as e:
        log.error(f"Error during Ollama Vision call for ACTION WanVideo prompts ({approved_image_path.name}): {e}", exc_info=True)
        return None, None

# --- NEW FUNCTION for LTX Prompt Generation ---
def generate_ltx_prompt_from_image(
    approved_image_path: Path,
    movie_name: str,
    theme: str,
    character_name: str,
    actor_name: str,
    character_data: dict,
    original_flux_prompt: str,
    ollama_model_name: str
    ):
    """
    Uses Ollama Vision to generate a detailed LTX-style cinematic video prompt
    based on an image and context.
    Returns the prompt string or None.
    """
    task_desc = f"Generate LTX Prompt for {character_name} ({approved_image_path.name})"
    log = logging.getLogger(__name__)
    log.info(f"--- Starting Ollama Vision Task: {task_desc} ---")

    if not approved_image_path.is_file():
        log.error(f"Approved image file not found: {approved_image_path}")
        return None

    # Prepare context (same as before, maybe slightly more detail if helpful)
    traits = character_data.get("key_personality_traits", [])
    flux_prompt_snippet = original_flux_prompt[:400] + "..." if len(original_flux_prompt) > 400 else original_flux_prompt
    iconic_scene = character_data.get("iconic_scene_description", "")

    # --- LTX Specific Prompt Instructions ---
    ltx_instructions = f"""
You are an expert cinematic director and prompt engineer specializing in text-to-video generation, specifically for models requiring highly detailed prompts like LTX. You receive an image and detailed context. Your task is to imagine and describe a natural visual action or camera movement that could realistically unfold from the provided still image over the next ~5 seconds. Focus exclusively on visual storytelling—do not include sound, music, inner thoughts, or dialogue.

**Image Context:**
The provided image depicts '{character_name}' (inspired by actor '{actor_name}') within the '{theme}' aesthetic of the movie '{movie_name}'.
Character Traits: {', '.join(traits)}.
Original Static Image Prompt Intent (Snippet): {flux_prompt_snippet}.

**Your Task:**
Analyze the image closely (pose, gaze, posture, environment). Infer a logical and expressive short action, gesture, or camera movement based on the visual cues and character context. Describe this unfolding moment with precision and cinematic detail in a single paragraph.

**Structure & Content:**
- Start with the first clear motion or camera cue (e.g., "The camera slowly pushes in as...", "Her eyes flicker towards...", "He subtly shifts his weight...").
- Build with specific gestures, body language changes, evolving facial expressions, and any physical interaction with the environment or props visible/implied.
- Detail the environment, framing changes (if any), lighting shifts, and atmospheric details during the action.
- Be highly descriptive about the subject's appearance (clothing textures, cybernetics glowing/moving, hair shifting) and intricate details of the scene.
- Finish with relevant cinematic style references (e.g., "in the moody, rain-slicked style of Blade Runner", "shot handheld with a shallow depth of field like Children of Men", "cinematic lighting, dramatic composition, shot on Arri Alexa").

**Example Output (Single Paragraph):**
"The camera slowly dollies forward, focusing on the stoic man's face. His cybernetic eye subtly scans left, data streams flickering faster across his augmented reality glasses. He takes a slow, deliberate breath, the worn collar of his synth-leather duster coat shifting slightly in a non-existent breeze under the flickering neon sign. Rain continues to streak down, catching the dim light. Cinematic composition, gritty realism, shallow depth of field, in the style of a dark cyberpunk thriller."

**Generate ONLY the single descriptive paragraph.**
"""
    # --- End LTX Specific Prompt Instructions ---

    log.info(f"Sending image {approved_image_path.name} and LTX context to Ollama Vision ({ollama_model_name})...")

    try:
        image_path_str = str(approved_image_path.resolve())
        ollama_payload = {
            "role": "user",
            "content": ltx_instructions, # Use the new detailed instructions
            "images": [image_path_str]
        }

        # Use ollama.chat directly here for potentially longer generation, non-JSON output
        response = ollama.chat(
             model=ollama_model_name,
             messages=[ollama_payload]
             # No format='json' needed here
        )

        if response and response.get('message') and response['message'].get('content'):
             ltx_prompt = response['message']['content'].strip()
             # Basic check: does it look like a paragraph?
             if len(ltx_prompt) > 30 and "\n" not in ltx_prompt[:100]: # Heuristic check
                  log.info(f"Successfully generated LTX prompt for {approved_image_path.name}.")
                  log.debug(f"Generated LTX Prompt: {ltx_prompt}")
                  return ltx_prompt
             else:
                  log.warning(f"Generated LTX prompt seems too short or improperly formatted: '{ltx_prompt[:100]}...'")
                  return None # Treat unexpected format as failure
        else:
             log.error(f"Failed to get valid content from LTX prompt generation response: {response}")
             return None

    except Exception as e:
        log.error(f"Error during Ollama Vision call for LTX prompt ({approved_image_path.name}): {e}", exc_info=True)
        return None
