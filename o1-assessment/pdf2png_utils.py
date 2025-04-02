# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.


import os, sys
import fitz
from pathlib import Path  
from PIL import Image

# Create directory if it does not exist
def ensure_directory_exists(directory_path):  
    path = Path(directory_path)  
    if not path.exists():  
        path.mkdir(parents=True, exist_ok=True)  
        #print(f"Directory created: {directory_path}")  
    #else:  
        #print(f"Directory already exists: {directory_path}")  


def extract_pdf_pages_to_images(pdf_path, image_dir):
    # Get PDF filename without extension for the subdirectory name
    pdf_filename = os.path.basename(pdf_path)
    pdf_name_no_ext = os.path.splitext(pdf_filename)[0]
    
    # Create a subdirectory with the PDF name (without extension) inside the image_dir
    pdf_image_dir = os.path.join(image_dir, pdf_name_no_ext)
    ensure_directory_exists(pdf_image_dir)

    # Open the PDF file and iterate pages
    print(f'Extracting images from PDF: {pdf_filename} to {pdf_image_dir}...')
    pdf_document = fitz.open(pdf_path)  

    for page_number in range(len(pdf_document)):  
        page = pdf_document.load_page(page_number)  
        image = page.get_pixmap()  
        image_out_file = os.path.join(pdf_image_dir, f'{page_number + 1}.png')
        image.save(image_out_file)  
        if page_number % 100 == 0 and page_number > 0:
            print(f'Processed {page_number} of {len(pdf_document)} pages...')
    
    print(f'Completed extracting {len(pdf_document)} pages from {pdf_filename}')

# Function to join images in pairs with specified direction
# This function takes a folder of images and combines them in pairs, saving the results in a new folder.
# join_direction can be 'horizontal' or 'vertical'
def join_images_in_pairs(source_folder: str, join_direction: str = 'vertical'):
    # Validate join_direction parameter
    if join_direction.lower() not in ['horizontal', 'vertical']:
        raise ValueError("join_direction must be either 'horizontal' or 'vertical'")
        
    if join_direction.lower() == "horizontal":
        joined_folder = source_folder + "_joined-horiz"
    else:
        joined_folder = source_folder + "_joined-vert"    
    os.makedirs(joined_folder, exist_ok=True)

    # Sort numerically so 1.png, 2.png, 3.png, etc.
    file_list = sorted(
        (f for f in os.listdir(source_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))),
        key=lambda x: int(os.path.splitext(x)[0])
    )
    print(f"Found {len(file_list)} images to process.")
    print(f"Joining images {join_direction}ly")
    
    for i in range(0, len(file_list), 2):
        first_image_path = os.path.join(source_folder, file_list[i])

        if i + 1 < len(file_list):
            second_image_path = os.path.join(source_folder, file_list[i + 1])
            img1 = Image.open(first_image_path)
            img2 = Image.open(second_image_path)
            
            if join_direction.lower() == 'vertical':
                # Vertical stacking (one on top of the other)
                width = max(img1.width, img2.width)
                height = img1.height + img2.height
                combined_img = Image.new('RGB', (width, height))
                combined_img.paste(img1, (0, 0))
                combined_img.paste(img2, (0, img1.height))
            else:  # horizontal
                # Horizontal stacking (side by side)
                width = img1.width + img2.width
                height = max(img1.height, img2.height)
                combined_img = Image.new('RGB', (width, height))
                combined_img.paste(img1, (0, 0))
                combined_img.paste(img2, (img1.width, 0))
            
            base_name = f"{os.path.splitext(file_list[i])[0]}_{os.path.splitext(file_list[i+1])[0]}"
            out_path = os.path.join(joined_folder, f"{base_name}.png")
            combined_img.save(out_path)
            print(f"Saved combined image: {out_path}")
        else:
            # If there's an odd number of images, just copy the last one as is.
            img1 = Image.open(first_image_path)
            out_path = os.path.join(joined_folder, file_list[i])
            img1.save(out_path)
            print(f"Copied single remaining image: {out_path}")
