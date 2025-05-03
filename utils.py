# utils.py
# Helper functions shared across scripts

import logging
from pathlib import Path
import re
import os # Needed for getmtime

log = logging.getLogger(__name__) # Use logger if needed within helpers

def sanitize_name(name):
    """Sanitizes a string to be safe for filenames/folders."""
    if not isinstance(name, str): name = str(name)
    # Allow alphanumeric, space, underscore, hyphen. Replace others with underscore.
    sanitized = re.sub(r'[<>:"/\\|?*\']', '_', name)
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in (' ', '_', '-')).strip()
    # Replace spaces with underscores, collapse multiple underscores/hyphens
    sanitized = sanitized.replace(' ', '_')
    sanitized = re.sub(r'[_]+', '_', sanitized)
    sanitized = re.sub(r'[-]+', '-', sanitized)
    # Strip leading/trailing underscores/hyphens
    sanitized = sanitized.strip('_-')
    # Limit length
    max_len = 60
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip('_-')
    return sanitized if sanitized else "invalid_name"

def find_latest_project_dir(base_dir: Path, project_prefix: str = "mythic_movie") -> Path | None:
    """Finds the most recently modified project directory matching the prefix."""
    latest_dir = None
    latest_time = 0

    if not base_dir.is_dir():
        log.error(f"Base directory for projects not found: {base_dir}")
        return None

    try:
        for item in base_dir.iterdir():
            # Check if it's a directory and matches the naming convention (prefix_*)
            if item.is_dir() and item.name.startswith(project_prefix + "_"):
                try:
                    mtime = os.path.getmtime(item) # Get modification time
                    if mtime > latest_time:
                        latest_time = mtime
                        latest_dir = item
                except Exception as e:
                     log.warning(f"Could not get modification time for {item.name}: {e}")
                     continue # Skip directories we can't check
        if latest_dir:
             log.info(f"Found latest project directory: {latest_dir.name}")
        else:
             log.warning(f"No project directories found in {base_dir} matching prefix '{project_prefix}'.")

    except Exception as e:
        log.error(f"Error searching for latest project directory in {base_dir}: {e}")
        return None

    return latest_dir

# Add other shared helper functions here if needed in the future
# e.g., extract_theme_from_project_path if used by image gen script
# def extract_theme_from_project_path(project_path: Path, project_prefix: str = "mythic_movie") -> str:
#     """Extracts the theme/topic from the project folder name."""
#     name = project_path.name
#     if name.startswith(project_prefix + "_"):
#         parts = name.split('_')
#         if len(parts) >= 3: # prefix_theme_timestamp...
#             # Join parts between prefix and timestamp (handles themes with underscores)
#             theme_parts = parts[len(project_prefix.split('_')):-1]
#             return "_".join(theme_parts)
#     log.warning(f"Could not extract theme from project name: {name}")
#     return "Unknown Theme"