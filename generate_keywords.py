from email.mime import image
import os
import re
from re import I
import time
import base64
import os
from langchain_core.messages import ai
import photoscript
from photoscript import Album, PhotosLibrary, Photo

from langchain_community.llms import Ollama
from io import BytesIO
from PIL import Image

# ---------------------------------------------------------------------------------------------------------------------

OLLAMA_BASE_URL               = "http://127.0.0.1:11434"
LLM_IMAGE_MODEL               = "llava:v1.6"
LLM_TRANSLATE_MODEL           = "llama3.1:latest"

IPHOTO_KEYWORD_ALBUM          = "ai-keywords"
IPHOTO_KEYWORD_DONE_ALBUM     = "ai-keywords DONE"
IPHOTO_KEYWORD_NOT_DONE_ALBUM = "ai-keywords NOT DONE"

AI_KEYWORD_MARKER             = " (AI)"

TRANSLATE_KEYWORDS_TO         = "German"    # "None" or "German", "French", "Spanish", "Italian", "Portuguese", "Dutch", "Russian", "Chinese", "Japanese", "Korean"

LLM_IMAGE_PROMPT              = "Please find ten very precise keywords and separate them with commas."
LLM_TRANSLATE_PROMPT          = f"Please translate the word list to {TRANSLATE_KEYWORDS_TO} and separate them with commas. Response strictly with the list, without any other explanations or comments."

# This directory is used to temporary store the images for the LLM-Model, it is deleted after the run:
TEMPORARY_DIR_NAME            = "temporary_ai_photo_export"


# ---------------------------------------------------------------------------------------------------------------------

class AiAlbumKeyword():

    TEMPORARY_WORK_ALBUM_PREFIX = "temp. AiAlbumKeyword_"

    max_image_counter     = 0
    current_image_counter = 0
    
    image_model:     Ollama
    translate_model: Ollama

    photoslib:       PhotosLibrary
    album:           Album
    album_done:      Album
    album_not_done:  Album
    
    # -----------------------------------------------------------------------------------------------------------------

    def __init__(self):
        """
        Initializes the class with the following steps:
        1. Connects to the LLM models for image processing and optional translation.
        2. Connects to the iPhoto application using the PhotosLibrary.
        3. Accesses or creates the specified albums for keyword generation, completed tasks, and pending tasks.
        4. Creates a temporary directory for export or cleans up old files in the existing temporary directory.
        """
        # Connection to LLM-Models:
        self.image_model = Ollama(model=LLM_IMAGE_MODEL,     base_url=OLLAMA_BASE_URL, temperature=0, verbose=True, num_predict=200)

        # We are doing the translation in a second step to enhance the quality of the translation:
        if TRANSLATE_KEYWORDS_TO != "None":
            self.translate_model = Ollama(model=LLM_TRANSLATE_MODEL, base_url=OLLAMA_BASE_URL, temperature=0, verbose=True)

        # Access to the iPhoto-App:
        self.photoslib = photoscript.PhotosLibrary()

        # Activate the Photos-App:  // not needed
        #photoslib.activate()

        # Access the album for which the keywords are to be generated:
        self.album = self.photoslib.album(IPHOTO_KEYWORD_ALBUM)
        if self.album is None:
            self.album = self.photoslib.create_album(IPHOTO_KEYWORD_ALBUM)

        self.album_done = self.photoslib.album(IPHOTO_KEYWORD_DONE_ALBUM)
        if self.album_done is None:
            self.album_done = self.photoslib.create_album(IPHOTO_KEYWORD_DONE_ALBUM)
                
        self.album_not_done = self.photoslib.album(IPHOTO_KEYWORD_NOT_DONE_ALBUM)
        if self.album_not_done is None:
            self.album_not_done = self.photoslib.create_album(IPHOTO_KEYWORD_NOT_DONE_ALBUM)
            
        # Create a temp. directory for the export or cleanup old files:
        os.makedirs(TEMPORARY_DIR_NAME, exist_ok=True)  
        files_and_dirs = os.listdir(TEMPORARY_DIR_NAME)
        for item in files_and_dirs:
            os.remove(os.path.join(TEMPORARY_DIR_NAME, item))

    # -----------------------------------------------------------------------------------------------------------------

    def cleanup(self) -> None:
        os.rmdir(TEMPORARY_DIR_NAME)

    # -----------------------------------------------------------------------------------------------------------------

    def convert_to_base64(self, pil_image) -> str:
        """
        Convert a PIL image to a base64 encoded string.

        Args:
            pil_image (PIL.Image.Image): The PIL image to be converted.

        Returns:
            str: The base64 encoded string representation of the image.
        """
        buffered = BytesIO()
        rgb_im = pil_image.convert('RGB')
        rgb_im.save(buffered, format="JPEG") 
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return img_str

    # -----------------------------------------------------------------------------------------------------------------

    def split_album_to_work_albums(self) -> None:        
        """
        Splits the current album into multiple smaller albums, each containing up to 99 photos.
        This method is necessary because iPhoto requires user confirmation to delete photos from an album
        if it contains more than 99 photos. By splitting the album into smaller chunks, we can avoid this
        confirmation step. After processing, the original album is deleted and recreated.
        Delete a whole album with more than 99 photos is possible :-) 

        Steps:
        - Retrieve all photos from the album.
        - Split the photos into chunks of up to 99 photos.
        - Create temporary albums for each chunk and add the photos to these albums.
        - Delete the original album and recreate it to "remove' all photos.

        Note:
        - The temporary albums are named "temp. AiAlbumKeyword_{album_number}".

        Returns:
            None
        """
        start = time.perf_counter()
        photos = self.album.photos()
        end = time.perf_counter()
        print(f"Time to get {len(photos)} photos: {end - start:0.1f} seconds")
                
        current_photo = 0
        
        LIMIT_PHOTOS_PER_ALBUM = 99
        album_number = 0
        while True:
            work_album_name = f"{self.TEMPORARY_WORK_ALBUM_PREFIX}{album_number}"
            work_album = self.photoslib.album(work_album_name)
            if work_album is None:
                work_album = self.photoslib.create_album(work_album_name)
                
            space_in_album = LIMIT_PHOTOS_PER_ALBUM - len(work_album.photos())
            
            work_album.add(photos[current_photo:current_photo + space_in_album])
            current_photo += space_in_album


            album_number += 1
            if current_photo >= len(photos):
                break
        
        self.photoslib.delete_album(self.album)
        self.album = self.photoslib.create_album(IPHOTO_KEYWORD_ALBUM)

    # -----------------------------------------------------------------------------------------------------------------

    def keyword_generation(self) -> None:
        """
        Generates keywords for photos in albums that start with the temporary work album prefix.

        This method performs the following steps:
        - Identifies albums whose names start with the temporary work album prefix.
        - Processes each album to generate keywords for the photos it contains.

        Returns:
            None
        """
        working_albums = [a for a in self.photoslib.albums() if a.name.startswith(self.TEMPORARY_WORK_ALBUM_PREFIX)]

        # Count the number of images to process:
        self.max_image_counter = 0
        for work_album in working_albums:
            self.max_image_counter += len(work_album.photos())

        # Process the images:
        for work_album in working_albums:
            self.generate_keywords_for_album(work_album)

    # -----------------------------------------------------------------------------------------------------------------

    def generate_keywords_for_album(self, work_album: Album) -> None:
        """
        Generate keywords for all photos in the given album.
        This method processes photos in the given album in chunks of 10. For each photo, it attempts to handle the photo
        and add keywords. Processed photos are moved to either a "done" album or a "not done" album based on whether they
        were successfully handled. The method continues processing until all photos in the album have been handled.
        Args:
            work_album (Album): The album containing photos to be processed.
        Returns:
            None
        """
        print(f"Working on album: {work_album.title}")
        
        # Loop over all photos in the album in chunks of 10, to move the photos to the "done" album, in case of an abort:
        LIMIT = 10
        while True:
            start = time.perf_counter()
            photos = work_album.photos()
            end = time.perf_counter()
            print(f"Time to get {len(photos)} photos: {end - start:0.1f} seconds")

            if len(photos) == 0:
                break
            
            photos_done = [] 
            photos_not_done = [] 
            
            # Check the photos and add the keywords:
            for photo in photos:
                                
                self.current_image_counter += 1

                print(f"Working on {self.current_image_counter} of {self.max_image_counter} : {photo.uuid} {photo.filename}")
                
                photo_handled = self.handle_photo(photo)  
                  
                if photo_handled:
                    photos_done.append(photo)
                else:
                    photos_not_done.append(photo)

                print()
                                
                # Time for a break:
                if len(photos_done) + len(photos_not_done) >= LIMIT:
                    break

            # Move the photos to the "done"/"not done" album:
            print(f"Moving {len(photos_done)} photos to the DONE album.")   
            self.album_done.add(photos_done)
            print(f"Moving {len(photos_not_done)} photos to the NOT DONE album.")   
            self.album_not_done.add(photos_not_done)

            work_album = work_album.remove(photos_done + photos_not_done)
                
            photos_done.clear()
            photos_not_done.clear()
            
        # Delete work_album, because we can delete all photos from it:
        self.photoslib.delete_album(work_album)

    # -----------------------------------------------------------------------------------------------------------------

    def handle_photo(self, photo: Photo) -> bool:  
        """
        Processes a photo to generate AI-based keywords and updates the photo's keywords.
        Args:
            photo (Photo): The photo object to be processed.
        Returns:
            bool: True if the photo was successfully processed and keywords were generated, False otherwise.
            
        The function performs the following steps:
        - Exports the photo to a temporary directory.
        - Generates keywords using an AI model.
        - Appends the generated keywords to the photo's existing keywords, marking them with "(AI)".
        """
        # Skip movies, currently we are only interested in pictures:
        fn = repr(photo.filename).lower().strip('\'')
        if fn.endswith(".mov"):
            print(f"Skipping movie: {photo.uuid} {photo.filename}")
            return False

        start = time.perf_counter()
        photo.export(TEMPORARY_DIR_NAME, overwrite=True)
        end = time.perf_counter()
        print(f"Time to export: {end - start:0.1f} seconds")

        # Get the files, we are not depending on the filename, instead we look for the only file in the directory:
        files = os.listdir(TEMPORARY_DIR_NAME)
        if len(files) != 1:
            print(f"Error: Expected one file in directory, but found {len(files)} files.")
            for file in files:
                to_delete = os.path.join(TEMPORARY_DIR_NAME, file)
                print(f"Deleting garbage file : {to_delete}")
                os.remove(to_delete)
            return False
        
        image_filename = os.path.join(TEMPORARY_DIR_NAME, files[0])
        
        if (image_filename.endswith(".mov")):
            print(f"Skipping movie (live-image): {image_filename}")
            os.remove(image_filename)
            return False
        
        print(f"Working on file: {image_filename}")
        pil_image = Image.open(image_filename)

        # Scale to 672 Pixel:
        base_width= 672
        wpercent = (base_width / float(pil_image.size[0]))
        hsize = int((float(pil_image.size[1]) * float(wpercent)))
        pil_image = pil_image.resize((base_width, hsize), Image.Resampling.LANCZOS)

        # Convert image to Base64 and pass it to the model together with the prompt:
        image_b64 = self.convert_to_base64(pil_image)
        
        keywords_en = self.get_en_keywords(image_b64)

        if TRANSLATE_KEYWORDS_TO == "None":
            response_translated = keywords_en
        else:
            response_translated = self.translate_en_keywords(keywords_en)

        # Append "(AI)" to the keywords:
        new_keywords = []
        for s in photo.keywords:
            if AI_KEYWORD_MARKER not in s:
                new_keywords.append(s.strip())

        for kw in response_translated.split(','):
            new_keywords.append(kw.strip(" .-") + AI_KEYWORD_MARKER)

        print(f"New keywords: {new_keywords}")
        
        photo.keywords = new_keywords
        
        # All done, remove the file:
        os.remove(image_filename)
        return True 

    # -----------------------------------------------------------------------------------------------------------------

    def get_en_keywords(self, image_b64: str) -> str:
        """
        Generates English keywords from a base64-encoded image string using a language model.
        Args:
            image_b64 (str): The base64-encoded image string.
        Returns:
            str: A comma-separated string of filtered keywords in English.

        The function binds the provided image to a language model, invokes the model with a prompt,
        and processes the response to extract and filter keywords.
        """
        llm_with_image_context = self.image_model.bind(images=[image_b64])

        print(f"Request llm...")
        start = time.perf_counter()
        response = llm_with_image_context.invoke(LLM_IMAGE_PROMPT)
        end = time.perf_counter()
        print(f"Time to get keywords from LLM: {end - start:0.1f} seconds")

        keywords = response.split(',')

        # Filter and remove leading numbers and dots, if any:
        filtered_keywords = [re.sub(r"^\d+\.\s*", "", s) for s in keywords]
        
        print(f"Keywords EN = {filtered_keywords}")
        return filtered_keywords

    # -----------------------------------------------------------------------------------------------------------------

    def translate_en_keywords(self, keywords_en: str) -> str:                
        """
        Translates English keywords to another language using a translation model.
        Args:
            keywords_en (str): The English keywords to be translated.
        Returns:
            str: The translated keywords.
        """
        start = time.perf_counter()
        response_translated = self.translate_model.invoke(f"{LLM_TRANSLATE_PROMPT}\n\n{keywords_en}")
        end = time.perf_counter()
    
        print(f"Time to translate keywords: {end - start:0.1f} seconds")
        print(f"Keywords DE = {response_translated}")

        return response_translated

    # End of class AiAlbumKeyword

# -----------------------------------------------------------------------------------------------------------------


def main() -> None:
    ai_album_keyword = AiAlbumKeyword()
    ai_album_keyword.split_album_to_work_albums()
    ai_album_keyword.keyword_generation()
    ai_album_keyword.cleanup()


if __name__ == "__main__":
    main()

# End of file :-)
