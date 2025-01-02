from email.mime import image
import os
from re import I
import time
import base64
import os
import photoscript

from langchain_community.llms import Ollama
from io import BytesIO
from PIL import Image

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
TEMPORARY_DIR_NAME   = "temporary_ai_photo_export"

def convert_to_base64(pil_image) -> str:
    buffered = BytesIO()
    rgb_im = pil_image.convert('RGB')
    rgb_im.save(buffered, format="JPEG") 
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return img_str


def main() -> None:

    # Connection to LLM-Models:
    image_model = Ollama(model=LLM_IMAGE_MODEL,     base_url=OLLAMA_BASE_URL, temperature=0, verbose=True, num_predict=200)

    # We are doing the translation in a second step to enhance the quality of the translation:
    if TRANSLATE_KEYWORDS_TO != "None":
        translate_model = Ollama(model=LLM_TRANSLATE_MODEL, base_url=OLLAMA_BASE_URL, temperature=0, verbose=True)

    # Access to the iPhoto-App:
    photoslib = photoscript.PhotosLibrary()

    # Activate the Photos-App:  // not needed
    #photoslib.activate()

    #for album in photoslib.albums():
    #    print(f"Album {album.title}")

    # Access the album for which the keywords are to be generated:
    album = photoslib.album(IPHOTO_KEYWORD_ALBUM)
    if album is None:
        album = photoslib.create_album(IPHOTO_KEYWORD_ALBUM)

    album_done = photoslib.album(IPHOTO_KEYWORD_DONE_ALBUM)
    if album_done is None:
        album_done = photoslib.create_album(IPHOTO_KEYWORD_DONE_ALBUM)
            
    album_not_done = photoslib.album(IPHOTO_KEYWORD_NOT_DONE_ALBUM)
    if album_not_done is None:
        album_not_done = photoslib.create_album(IPHOTO_KEYWORD_NOT_DONE_ALBUM)
            
    # Create a temp. directory for the export or cleanup old files:
    os.makedirs(TEMPORARY_DIR_NAME, exist_ok=True)  
    files_and_dirs = os.listdir(TEMPORARY_DIR_NAME)
    for item in files_and_dirs:
        if item.endswith(".jpg") or item.endswith(".jpeg") or item.endswith(".mov"):
            os.remove(os.path.join(TEMPORARY_DIR_NAME, item))

    # Because the iPhoto can't remove photos from an album, we keep track of the photos we
    # have already handled.
    # If we have more than 100 photos we can't delete photos from the album, because iPhoto will
    # ask the "user" to confirm the deletion (implemented as rename and add the remaining photos) if he wants
    # to "add" (the remaining) the photos :-( :-(.
    handled_uuids = set()

    image_counter = 0
    max_image_counter = -1
    
    # Loop over all photos in the album in chunks of 10, to move the photos to the "done" album, in case of an abort:
    LIMIT = 10
    while True:
        start = time.perf_counter()
        photos = album.photos()
        end = time.perf_counter()
        print(f"Time to get {len(photos)} photos: {end - start:0.1f} seconds")

        if len(photos) == 0:
            break
        
        if max_image_counter == -1:
            max_image_counter = len(photos)
            
        photos_done = [] 
        photos_not_done = [] 
        
        # Check the photos and add the keywords:
        for photo in photos:
            
            # Skip photos we have already handled and which we can't delete from the album: :-(
            if photo.uuid in handled_uuids:
                continue
            handled_uuids.add(photo.uuid)
            
            image_counter += 1

            print(f"Image {image_counter} of {max_image_counter}")
            
            # Skip movies, currently we are only interested in pictures:
            fn = repr(photo.filename).lower().strip('\'')
            if fn.endswith(".mov"):
                print(f"Skipping movie: {photo.uuid} {photo.filename}")
                photos_not_done.append(photo)
                continue
            
            print(f"Working on    : {photo.uuid} {photo.filename} {fn}")
            start = time.perf_counter()
            photo.export(TEMPORARY_DIR_NAME, overwrite=True)
            end = time.perf_counter()
            print(f"Time to export: {end - start:0.1f} seconds")

            # Get the files, we are not depending on the filename, instead we look for the only file in the directory:
            files = os.listdir(TEMPORARY_DIR_NAME)
            if len(files) != 1:
                print(f"Error: Expected one file in directory, but found {len(files)} files.")
                continue
            
            image_filename = os.path.join(TEMPORARY_DIR_NAME, files[0])
            
            if (image_filename.endswith(".mov")):
                print(f"Skipping movie (live-image): {image_filename}")
                os.remove(image_filename)
                photos_not_done.append(photo)
                print()
                continue
            
            print(f"Working on file: {image_filename}")
            pil_image = Image.open(image_filename)

            # Scale to 672 Pixel:
            print(f"Scale image...")
            base_width= 672
            wpercent = (base_width / float(pil_image.size[0]))
            hsize = int((float(pil_image.size[1]) * float(wpercent)))
            pil_image = pil_image.resize((base_width, hsize), Image.Resampling.LANCZOS)

            # Convert image to Base64 and pass it to the model together with the prompt:
            print(f"Base64 image...")
            image_b64 = convert_to_base64(pil_image)
            llm_with_image_context = image_model.bind(images=[image_b64])

            print(f"Request llm...")
            start = time.perf_counter()
            response = llm_with_image_context.invoke(LLM_IMAGE_PROMPT)
            end = time.perf_counter()
            print(f"Time to get keywords from LLM: {end - start:0.1f} seconds")
            
            #response = response.replace(" ", "")
            keywords = response.split(',')
            print(f"Keywords EN = {response}")

            if TRANSLATE_KEYWORDS_TO == "None":
                response_translated = keywords
            else:
                start = time.perf_counter()
                response_translated = translate_model.invoke(f"{LLM_TRANSLATE_PROMPT}\n\n{keywords}")
                end = time.perf_counter()
                print(f"Time to translate keywords: {end - start:0.1f} seconds")
                print(f"Keywords DE = {response_translated}")

            # Append "(AI)" to the keywords:
            new_keywords = []
            for s in photo.keywords:
                if AI_KEYWORD_MARKER not in s:
                    new_keywords.append(s)

            for kw in response_translated.split(','):
                new_keywords.append(kw.strip(" .-") + AI_KEYWORD_MARKER)

            print(f"New keywords: {new_keywords}")
            
            photo.keywords = new_keywords
            
            photos_done.append(photo)
            
            # All done, remove the file:
            os.remove(image_filename)
            print()
            
            # Time for a break:
            if len(photos_done) + len(photos_not_done) >= LIMIT:
                break

        # Move the photos to the "done" album:
        print(f"Moving {len(photos_done)} photos to the DONE album.")   
        album_done.add(photos_done)
        album_not_done.add(photos_not_done)
        
        if max_image_counter > 99:
            print(f"Skipping removing photos from {IPHOTO_KEYWORD_ALBUM}, because we can't remove them from the album via apple script. :-(.)")
        else:
            album = album.remove(photos_done + photos_not_done)
        
        # Wait until iPhoto is done with the move:
        # max_seconds_to_wait = 60
        # while True:
        #     current_albums = photoslib.albums()
        #     if any(a.name.startswith("photoscript_") for a in current_albums):
        #         print(f"Waiting for iPhoto to finish the move... {max_seconds_to_wait} left.")
        #         max_seconds_to_wait -= 1
        #         time.sleep(1)
        #     else:
        #         break
            
        photos_done.clear()
        photos_not_done.clear()
        
    os.rmdir(TEMPORARY_DIR_NAME)

    # Close the iPhoto-App: // better not, hangs....
    #photoslib.quit()
    


if __name__ == "__main__":
    main()

# End of file :-)
