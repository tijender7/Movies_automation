# database_utils.py
import logging
from tmdbv3api import TMDb, Movie, Person # Import Person if needed later
import os
from pathlib import Path # Import Path if used elsewhere, not strictly needed here

# Setup logger for this module
log = logging.getLogger(__name__)
# Ensure basic config if run standalone or imported early
if not log.handlers:
     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s] - %(message)s')

def get_movie_details_tmdb(movie_name: str, tmdb_api_key: str) -> dict | None:
    """
    Fetches movie details (director, year, synopsis, cast) from TMDb.

    Args:
        movie_name: The name of the movie to search for.
        tmdb_api_key: Your TMDb API key.

    Returns:
        A dictionary containing movie info, or None on failure.
        Keys: 'director', 'release_year', 'brief_synopsis',
              'main_characters_actors' (list of dicts), 'source', 'tmdb_id'.
    """
    if not tmdb_api_key:
        log.error("TMDB API Key was not provided.")
        return None
    try:
        tmdb = TMDb()
        tmdb.api_key = tmdb_api_key
        tmdb.language = 'en'
        tmdb.debug = False # Set to True for more verbose logging of TMDb calls

        movie_searcher = Movie()
        log.info(f"Searching TMDb for movie: '{movie_name}'...")
        search_results = movie_searcher.search(movie_name)

        if not search_results:
            log.warning(f"No results found on TMDb for '{movie_name}'.")
            # Attempt search by removing potential year? Very basic fallback.
            parts = movie_name.split()
            if len(parts) > 1 and parts[-1].isdigit() and len(parts[-1]) == 4:
                 search_name_no_year = " ".join(parts[:-1])
                 log.info(f"Retrying TMDb search without year: '{search_name_no_year}'...")
                 search_results = movie_searcher.search(search_name_no_year)
                 if not search_results:
                      log.error(f"No TMDb results found even without year for '{movie_name}'.")
                      return None
            else:
                 log.error(f"No TMDb results found for '{movie_name}'.")
                 return None


        # Assume the first result is the most relevant
        # TODO: Could add logic here to pick best match if multiple results?
        first_result = search_results[0]
        movie_id = first_result.id
        log.info(f"Found TMDb match: '{first_result.title}' ({first_result.release_date[:4] if first_result.release_date else 'N/A'}) with ID {movie_id}. Fetching details...")

        # Get full movie details and credits in one go
        details = movie_searcher.details(movie_id, append_to_response='credits')

        if not details:
             log.error(f"Failed to fetch full details+credits for TMDb ID {movie_id}")
             return None

        # Extract Director from credits.crew
        director = "Unknown"
        if hasattr(details, 'credits') and 'crew' in details.credits:
            for crew_member in details.credits['crew']:
                if crew_member.get('job') == 'Director':
                    director = crew_member.get('name')
                    break
        else:
             log.warning(f"Credits or crew data missing in TMDb details for {movie_id}.")

        # Extract Release Year
        release_year = None
        if hasattr(details, 'release_date') and details.release_date:
            try: release_year = int(details.release_date[:4])
            except (ValueError, TypeError, IndexError):
                 log.warning(f"Could not parse release year from '{details.release_date}'.")

        # Extract Synopsis
        synopsis = getattr(details, 'overview', 'Synopsis unavailable.')
        if not synopsis: synopsis = 'Synopsis unavailable.'

        # Extract Cast (filter for actors with characters)
        cast_list = []
        if hasattr(details, 'credits') and 'cast' in details.credits:
             # Sort by 'order' to get main cast first, limit number
             cast_limit = 15 # Get top 15 credited actors initially
             sorted_cast = sorted(details.credits['cast'], key=lambda x: x.get('order', 999))

             for cast_member in sorted_cast[:cast_limit]:
                 actor_name = cast_member.get('name')
                 # TMDb often puts "(voice)" in character name, try to remove
                 char_name_raw = cast_member.get('character', '')
                 char_name = char_name_raw.replace(' (voice)', '').strip() if char_name_raw else ''

                 # Only include if we have both names and character isn't empty/just role description
                 if actor_name and char_name and len(char_name) > 0 and 'uncredited' not in char_name.lower() and 'self' not in char_name.lower():
                      cast_list.append({
                          "character_name": char_name,
                          "actor_name": actor_name
                      })
                 elif actor_name and char_name: # Log skipped ones
                      log.debug(f"Skipping potential minor role: Char='{char_name_raw}', Actor='{actor_name}'")

             # Optional: Further filter based on popularity or known roles if needed
             # For now, take the top credited ones with characters.
             # You might want to implement your own filtering logic here if TMDb list is too long/includes minor roles.
             # Limit to ~10?
             final_cast_limit = 10
             if len(cast_list) > final_cast_limit:
                 log.info(f"Truncating TMDb cast list from {len(cast_list)} to {final_cast_limit}.")
                 cast_list = cast_list[:final_cast_limit]

        else:
             log.warning(f"Credits or cast data missing in TMDb details for {movie_id}.")


        movie_data = {
            "director": director,
            "release_year": release_year,
            "brief_synopsis": synopsis,
            "main_characters_actors": cast_list,
            "source": "TMDb",
            "tmdb_id": movie_id
        }
        log.info(f"Successfully retrieved and structured data from TMDb for '{details.title}'. Found {len(cast_list)} cast members.")
        return movie_data

    except Exception as e:
        log.error(f"Error interacting with TMDb API for '{movie_name}': {e}", exc_info=True)
        return None