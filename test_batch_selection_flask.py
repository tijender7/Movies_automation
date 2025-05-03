import os
import shutil
from flask import Flask, render_template, send_from_directory, request
import json
from pathlib import Path

app = Flask(__name__, template_folder='templates')

MOVIE_PROJECTS_DIR = Path('Movie_Projects')

# Get the latest project directory
def get_latest_project_dir():
    projects = [p for p in MOVIE_PROJECTS_DIR.iterdir() if p.is_dir()]
    if not projects:
        return None
    return max(projects, key=os.path.getmtime)

@app.route('/')
def batch_selection():
    latest_project = get_latest_project_dir()
    if not latest_project:
        return "No projects found."
    actors = {}
    char_root = latest_project / 'characters'
    if not char_root.exists():
        return "No characters folder found in latest project."
    for char_dir in char_root.iterdir():
        if char_dir.is_dir():
            actor_name = char_dir.name
            images_dir = char_dir / 'filtered_single_face_images'
            candidates = []
            if images_dir.exists():
                for img in images_dir.glob("*.jpg"):
                    candidates.append({
                        'relative_path': f"{actor_name}/{img.name}",
                        'full_path': str(img.resolve()),
                        'filename': img.name
                    })
            actors[actor_name] = {
                'candidates': candidates,
                'character_name': actor_name
            }
    # Pass actors as all_candidates to match select_all_images.html
    return render_template('select_all_images.html', all_candidates=actors)

@app.route('/images/<actor>/<filename>')
def serve_image(actor, filename):
    latest_project = get_latest_project_dir()
    img_dir = latest_project / 'characters' / actor / 'filtered_single_face_images'
    return send_from_directory(img_dir, filename)

@app.route('/submit_all', methods=['POST'])
def submit_all():
    # Find the latest project and its source_actors folder
    latest_project = get_latest_project_dir()
    if not latest_project:
        return "Cannot find project to save selections.", 500
    source_actors_path = latest_project / "source_actors"
    source_actors_path.mkdir(exist_ok=True)

    # Gather selections and copy images
    selections = {}
    copied_files = {}
    actors_processed = request.form.getlist('actors_processed') # Get list of actors presented

    for actor_name in actors_processed:
        selection_key = f"selection_{actor_name}"
        selected_value = request.form.get(selection_key)

        if selected_value and selected_value != "SKIP":
            try:
                src_path = Path(selected_value) # The value is the full path
                # --- Use Standardized Naming ---
                sanitized_name = "".join(c for c in actor_name if c.isalnum() or c in (' ', '_')).rstrip().replace(' ', '_')
                target_filename = f"{sanitized_name}{src_path.suffix}" # e.g., Amitabh_Bachchan.jpg
                dst_path = source_actors_path / target_filename
                # --- End Standardized Naming ---

                shutil.copy2(src_path, dst_path) # Use copy2 to preserve metadata
                print(f"Copied {src_path} to {dst_path}") # Add print/log for confirmation
                copied_files[actor_name] = str(dst_path)
                selections[actor_name] = str(src_path) # Record original path selected
            except FileNotFoundError:
                print(f"ERROR: Source file not found for {actor_name}: {src_path}")
                selections[actor_name] = f"ERROR: File not found {src_path}"
            except Exception as e:
                print(f"ERROR: Failed to copy file for {actor_name}: {e}")
                selections[actor_name] = f"ERROR: Copy failed {e}"

        elif selected_value == "SKIP":
            selections[actor_name] = "SKIP"
            print(f"Skipped actor: {actor_name}")
        else:
            # Handle case where no radio button was selected for an actor
            selections[actor_name] = "NO_SELECTION"
            print(f"No selection made for actor: {actor_name}")

    # In the real orchestrator, this data would go back via the queue
    # Here, we just display it
    return f"<h2>Selections Processed:</h2><pre>{json.dumps(selections, indent=2)}</pre><h2>Files Copied to {source_actors_path}:</h2><pre>{json.dumps(copied_files, indent=2)}</pre>"

if __name__ == '__main__':
    app.run(port=5002)
