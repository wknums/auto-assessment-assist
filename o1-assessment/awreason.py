# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
# spme of the code was sourced and adapted from: the Python code snippet provided by Azure Foundry Chat Playground


import os
import base64
import argparse
import sys
from pathlib import Path
from openai import AzureOpenAI  
from azure.identity import DefaultAzureCredential, get_bearer_token_provider  
from dotenv import load_dotenv
import tempfile

# Import the PDF processing utilities
from pdf2png_utils import extract_pdf_pages_to_images, join_images_in_pairs

def encode_image_to_base64(image_path):
    """Encode an image file to base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def read_prompt_from_file(file_path):
    """Read prompt text from a file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        print(f"Error reading prompt file {file_path}: {e}")
        return None

def ensure_directory_exists(file_path):
    """Ensure the directory for the given file path exists."""
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")

def process_pdf_to_images(pdf_path, image_dir, join_images=None):
    """Process a PDF file to extract images and optionally join them.
    
    Args:
        pdf_path: Path to the PDF file
        image_dir: Directory to save extracted images
        join_images: None for no joining, 'vertical' or 'horizontal' for joining direction
        
    Returns:
        Path to the directory containing the processed images
    """
    # Extract images from the PDF
    extract_pdf_pages_to_images(pdf_path, image_dir)
    
    # Get the output directory that was created
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_dir = os.path.join(image_dir, pdf_name)
    
    # Join images if requested
    if join_images in ['vertical', 'horizontal']:
        print(f"Joining images {join_images}ly from {output_dir}")
        join_images_in_pairs(output_dir, join_direction=join_images)
        
        # Update the output directory to the joined images folder
        if join_images == 'horizontal':
            output_dir = output_dir + "_joined-horiz"
        else:
            output_dir = output_dir + "_joined-vert"
    
    return output_dir

def get_images_from_folder(folder_path):
    """Get all image files from a folder and return them as a list, sorted by numerical order."""
    image_folder = Path(folder_path)
    if not image_folder.exists() or not image_folder.is_dir():
        print(f"WARNING: Image folder does not exist or is not a directory: {folder_path}")
        return []
    
    # Find all image files in the folder (PNG, JPG, JPEG)
    image_files = list(image_folder.glob("*.png")) + list(image_folder.glob("*.jpg")) + list(image_folder.glob("*.jpeg"))
    
    if not image_files:
        print(f"WARNING: No image files found in {folder_path}")
    else:
        print(f"Found {len(image_files)} image files in {folder_path}")
    
    # Sort the image files by numerical order (for filenames like "1_2.png", "3_4.png", etc.)
    def extract_page_numbers(filename):
        # Extract numbers from filename (before extension)
        base_name = filename.stem
        # Try to extract the first number in the filename
        import re
        numbers = re.findall(r'\d+', base_name)
        if numbers:
            return int(numbers[0])
        return base_name  # Fall back to string sorting if no numbers found
    
    # Sort the images by the extracted page numbers
    image_files.sort(key=extract_page_numbers)
    
    return image_files

def find_pdfs_in_directory(directory_path):
    """Find all PDF files in a directory."""
    pdf_files = []
    
    if os.path.isdir(directory_path):
        pdf_files = [f for f in os.listdir(directory_path) if f.lower().endswith('.pdf')]
        if pdf_files:
            pdf_files = [os.path.join(directory_path, pdf) for pdf in pdf_files]
            print(f"Found {len(pdf_files)} PDF files in {directory_path}")
        else:
            print(f"No PDF files found in {directory_path}")
    else:
        print(f"The path {directory_path} is not a directory")
    
    return pdf_files

def save_response_to_file(response_text, file_path):
    """Save model response to the specified file path."""
    try:
        ensure_directory_exists(file_path)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(response_text)
        print(f"Response saved to: {file_path}")
        return True
    except Exception as e:
        print(f"Error saving response to {file_path}: {e}")
        return False

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Chat with O1 model using text and images')
    
    # Create a mutually exclusive group for image source options
    image_source_group = parser.add_mutually_exclusive_group()
    image_source_group.add_argument('--images_folder1', type=str, help='First folder containing image files')
    image_source_group.add_argument('--images_folder2', type=str, help='Second folder containing image files')
    image_source_group.add_argument('--pdf_source', type=str, help='Process all PDFs in this directory')
    image_source_group.add_argument('--pdf_file', type=str, help='Process a single PDF file')
    
    # Image joining options for PDF processing
    parser.add_argument('--join', choices=['vertical', 'horizontal'], help='Join extracted images in pairs')
    
    # Create a mutually exclusive group for prompt options
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument('--prompt', type=str, help='Text prompt to send to the model')
    prompt_group.add_argument('--promptfile', type=str, help='Path to file containing the prompt text')
    
    # Add required output filepath parameter
    parser.add_argument('--output', type=str, required=True, help='Path to save the model response')
    
    # Add temp directory option for PDF processing
    parser.add_argument('--tempdir', type=str, help='Temporary directory for extracted images (default: system temp)')
    
    args = parser.parse_args()

    # Get prompt text either directly or from file
    prompt_text = None
    if args.prompt:
        prompt_text = args.prompt
    elif args.promptfile:
        prompt_text = read_prompt_from_file(args.promptfile)
        if not prompt_text:
            print("Failed to read prompt from file. Exiting.")
            return

    # Set up image processing directories
    temp_dir = args.tempdir if args.tempdir else tempfile.mkdtemp()
    ensure_directory_exists(temp_dir)
    print(f"Using temporary directory: {temp_dir}")
    
    # Collect images based on the source option
    folder1_images = []
    folder2_images = []
    
    # Process PDF files if specified
    if args.pdf_source:
        # Find all PDFs in the directory
        pdf_files = find_pdfs_in_directory(args.pdf_source)
        
        if not pdf_files:
            print("No PDF files found. Exiting.")
            return

        # Process the first PDF
        if len(pdf_files) > 0:
            pdf1_dir = process_pdf_to_images(pdf_files[0], temp_dir, args.join)
            folder1_images = get_images_from_folder(pdf1_dir)
            print(f"Processed first PDF: {os.path.basename(pdf_files[0])}")
        
        # Process the second PDF if there's more than one
        if len(pdf_files) > 1:
            pdf2_dir = process_pdf_to_images(pdf_files[1], temp_dir, args.join)
            folder2_images = get_images_from_folder(pdf2_dir)
            print(f"Processed second PDF: {os.path.basename(pdf_files[1])}")
            
        # Warn if there are more than 2 PDFs
        if len(pdf_files) > 2:
            print(f"WARNING: Found {len(pdf_files)} PDFs, but only processing the first two to avoid token limits.")
    
    # Process a single PDF file
    elif args.pdf_file:
        if not os.path.isfile(args.pdf_file):
            print(f"Error: {args.pdf_file} is not a valid file. Exiting.")
            return
            
        pdf_dir = process_pdf_to_images(args.pdf_file, temp_dir, args.join)
        folder1_images = get_images_from_folder(pdf_dir)
        print(f"Processed PDF: {os.path.basename(args.pdf_file)}")
    
    # Use provided image folders
    elif args.images_folder1:
        folder1_images = get_images_from_folder(args.images_folder1)
        print(f"Collected {len(folder1_images)} images from folder 1")
    
    elif args.images_folder2:
        folder2_images = get_images_from_folder(args.images_folder2)
        print(f"Collected {len(folder2_images)} images from folder 2")
    
    # Check the total number of images
    image_count = len(folder1_images) + len(folder2_images)
    print(f"Total image count: {image_count}")
    
    if image_count > 50:
        print(f"ERROR: Found {image_count} images in total, which exceeds the maximum limit of 50 images.")
        print("The O1 model cannot process more than 50 images at once due to current limitations.")
        print("\nRecommendations:")
        print("1. Reduce the number of images to process by selecting a subset of the most important ones.")
        print("2. Consider using Retrieval Augmented Generation (RAG) for larger document collections.")
        print("3. Split your images into multiple separate requests.")
        print("\nExiting program.")
        sys.exit(1)
    elif image_count == 0:
        print("WARNING: No image files found based on the provided arguments.")
        print("Note: The O1 model can work with text-only prompts, but since this tool is designed")
        print("for visual reasoning, you might want to check your input paths.")

    load_dotenv(dotenv_path="..\.env",override=True)

    # Retrieve environment variables with defaults
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "o1")

    # Debug: Print assigned environment variables
    print("Azure OpenAI Endpoint:", endpoint)
    print("Deployment:", deployment)


    # Initialize Azure OpenAI client with Entra ID authentication  
    cognitiveServicesResource = "https://cognitiveservices.azure.com/"
    token_provider = get_bearer_token_provider(  
        DefaultAzureCredential(),  
        f'{cognitiveServicesResource}.default'  
    )  

    client = AzureOpenAI(  
        azure_endpoint=endpoint,  
        azure_ad_token_provider=token_provider,  
        api_version='2024-12-01-preview',  
    )
    
    # Set up the messages for the chat
    messages = [
        {"role": "system", "content": "You are a helpful assistant that can analyze both text and images. Provide detailed analysis when images are included."},
        {"role": "user", "content": []}
    ]
    
    # Modify the prompt text if both folders have images
    if folder1_images and folder2_images:
        folder1_name = Path(args.images_folder1 if args.images_folder1 else (args.pdf_source or args.pdf_file)).name
        folder2_name = Path(args.images_folder2 if args.images_folder2 else args.pdf_source).name
        
        # Enhance the prompt to explain which images belong to which document
        document_explanation = (
            f"\n\nIMPORTANT: You will be provided with images from two different documents."
            f"\n- The first {len(folder1_images)} images (from '{folder1_name}') belong to DOCUMENT 1."
            f"\n- The next {len(folder2_images)} images (from '{folder2_name}') belong to DOCUMENT 2."
            f"\nThe images are provided in their correct page order within each document."
            f"\nPlease analyze each document separately, maintaining awareness of which images belong to which document."
        )
        
        # Append document explanation to the original prompt
        prompt_text += document_explanation
        print(f"Enhanced prompt with document separation information")
    
    # Add the text prompt
    messages[1]["content"].append({"type": "text", "text": prompt_text})
    
    # Add all collected images to the message content
    total_images = len(folder1_images) + len(folder2_images)
    if total_images > 0:
        print(f"Adding {total_images} images to the request...")
        
        # Add images from folder 1 first (in their proper page order)
        if folder1_images:
            print(f"Adding {len(folder1_images)} images from folder 1 to the request...")
            for img_path in folder1_images:
                try:
                    base64_image = encode_image_to_base64(img_path)
                    messages[1]["content"].append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}"
                        }
                    })
                    print(f"Added image: {img_path.name}")
                except Exception as e:
                    print(f"Error processing image {img_path}: {e}")
        
        # Then add images from folder 2 (in their proper page order)
        if folder2_images:
            print(f"Adding {len(folder2_images)} images from folder 2 to the request...")
            for img_path in folder2_images:
                try:
                    base64_image = encode_image_to_base64(img_path)
                    messages[1]["content"].append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}"
                        }
                    })
                    print(f"Added image: {img_path.name}")
                except Exception as e:
                    print(f"Error processing image {img_path}: {e}")
    
    # Send the request to the o1 model
    print("\nSending request to o1 model...")
    completion = client.chat.completions.create(  
        model=deployment,  
        messages=messages,
        reasoning_effort="high",
        max_completion_tokens=15000,  # o1 requirement
        seed=31457  # o1 for more consistent results
    )

    # Get the response content
    response_content = completion.choices[0].message.content
    
    # Extract and report token usage
    prompt_tokens = completion.usage.prompt_tokens
    completion_tokens = completion.usage.completion_tokens
    total_tokens = completion.usage.total_tokens
    
    print(f"\nToken Usage Summary:")
    print(f"  Input tokens:  {prompt_tokens:,}")
    print(f"  Output tokens: {completion_tokens:,}")
    print(f"  Total tokens:  {total_tokens:,}")
    
    # Print the response
    print("\nO1 Response:")
    print(response_content)
    
    # Save the response to the specified output file
    save_response_to_file(response_content, args.output)
    
    # Clean up temporary directory if we created one
    if not args.tempdir and temp_dir and os.path.exists(temp_dir):
        import shutil
        try:
            shutil.rmtree(temp_dir)
            print(f"Cleaned up temporary directory: {temp_dir}")
        except Exception as e:
            print(f"Warning: Could not clean up temporary directory {temp_dir}: {e}")

if __name__ == "__main__":
    main()