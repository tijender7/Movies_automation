import face_recognition
import cv2
import logging
import shutil
from pathlib import Path

def filter_and_copy_single_face_images(source_image_paths, target_filtered_dir, required_faces=1):
    """
    Filters images for a specific face count and copies valid ones.
    """
    valid_image_paths_in_target = []
    if not source_image_paths:
        logging.warning("No source images provided for filtering.")
        return valid_image_paths_in_target
    logging.info(f"Filtering {len(source_image_paths)} images for exactly {required_faces} face(s)...")
    target_filtered_dir = Path(target_filtered_dir)
    target_filtered_dir.mkdir(parents=True, exist_ok=True)
    for i, img_path in enumerate(source_image_paths):
        try:
            image = face_recognition.load_image_file(str(img_path))
            face_locations = face_recognition.face_locations(image)
            num_faces = len(face_locations)
            logging.debug(f"Image: {Path(img_path).name}, Faces detected: {num_faces}")
            if num_faces == required_faces:
                target_path = target_filtered_dir / Path(img_path).name
                shutil.copy(img_path, target_path)
                logging.debug(f"  Copied valid image to: {target_path}")
                valid_image_paths_in_target.append(str(target_path))
        except Exception as e:
            logging.warning(f"Could not process image {Path(img_path).name} with face_recognition: {e}")
    logging.info(f"Finished filtering. Copied {len(valid_image_paths_in_target)} valid images to {target_filtered_dir}.")
    return valid_image_paths_in_target
