import requests
import logging
import os
from pathlib import Path

def perform_web_search(query, tavily_api_key, download_dir=None, num_results=5, search_for_images=True):
    """Performs a web search using Tavily API."""
    logging.info(f"Performing web search (Tavily) for: '{query}' (Images: {search_for_images})")
    if not tavily_api_key:
        logging.error("Tavily API key not provided to function.")
        return [], []
    downloaded_image_paths = []
    try:
        response = requests.post("https://api.tavily.com/search", json={
            "api_key": tavily_api_key,
            "query": query,
            "search_depth": "basic",
            "include_images": search_for_images,
            "max_results": num_results
        })
        response.raise_for_status()
        results = response.json()
        logging.info(f"Web search successful. Found {len(results.get('results', []))} web results.")
        if 'images' in results and results['images']:
            logging.info(f"Found {len(results['images'])} image URLs. Attempting download...")
            image_urls = results['images']
            if download_dir:
                Path(download_dir).mkdir(parents=True, exist_ok=True)
            for i, img_url in enumerate(image_urls[:num_results]):
                try:
                    img_response = requests.get(img_url, stream=True, timeout=10)
                    img_response.raise_for_status()
                    file_ext = Path(img_url).suffix.lower()
                    if file_ext not in ['.jpg', '.jpeg', '.png', '.webp']:
                        content_type = img_response.headers.get('content-type')
                        if content_type:
                            if 'jpeg' in content_type or 'jpg' in content_type: file_ext = '.jpg'
                            elif 'png' in content_type: file_ext = '.png'
                            elif 'webp' in content_type: file_ext = '.webp'
                            else: file_ext = '.jpg'
                        else: file_ext = '.jpg'
                    img_filename = f"image_{i+1}{file_ext}"
                    img_path = Path(download_dir) / img_filename if download_dir else Path(img_filename)
                    with open(img_path, 'wb') as f:
                        for chunk in img_response.iter_content(8192):
                            f.write(chunk)
                    logging.info(f"Downloaded image: {img_path.name}")
                    downloaded_image_paths.append(str(img_path))
                except requests.exceptions.RequestException as img_e:
                    logging.warning(f"Failed to download image {img_url}: {img_e}")
                except Exception as general_e:
                    logging.warning(f"Error processing image {img_url}: {general_e}")
        else:
            logging.info("No images found in search results.")
        return results.get('results', []), downloaded_image_paths
    except Exception as e:
        logging.error(f"Error during web search for '{query}': {e}")
        return [], []
