# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
# some of the code was sourced and adapted from: the Python code snippet provided by Azure Foundry Chat Playground


import os
import base64
import argparse
import sys
import json
from pathlib import Path
from openai import AzureOpenAI  
from azure.identity import DefaultAzureCredential, AzureCliCredential, get_bearer_token_provider  
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

def convert_docx_to_md(docx_path, temp_dir):
    """
    Convert a .docx file to markdown using python-docx and markdownify libraries.
    Returns the path to the generated markdown file.
    """
    try:
        from docx import Document  # type: ignore
        from markdownify import markdownify as md  # type: ignore
    except ImportError:
        print("Please install python-docx and markdownify: pip install python-docx markdownify")
        raise ImportError("Required libraries not found")

    # Output directory for markdown
    output_dir = temp_dir
    os.makedirs(output_dir, exist_ok=True)
    
    # Output filename will be <docx_filename>.md
    base_filename = os.path.basename(docx_path)
    md_filename = os.path.join(output_dir, base_filename + ".md")

    try:
        # Convert DOCX to markdown directly
        print(f"Converting DOCX to markdown: {docx_path}")
        doc = Document(docx_path)
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        doc_text = "\n".join(full_text)
        md_text = md(doc_text)
        
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(md_text)
            
        print(f"Successfully converted DOCX to markdown: {md_filename}")
        return md_filename
        
    except Exception as e:
        print(f"Error converting DOCX to markdown: {e}")
        raise RuntimeError("DOCX conversion failed")

def read_markdown_file(file_path):
    """Read content from a Markdown file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        print(f"Error reading Markdown file {file_path}: {e}")
        return None

def ensure_directory_exists(file_path):
    """Ensure the directory for the given file path exists."""
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")

def ensure_output_path(output_path, source_path=None, alt_source_path=None):
    """
    Ensure all directories in the output path exist.
    If output_path is an existing folder, compose a filename using source_path.
    If alt_source_path is provided, use it as the base for the filename.
    Returns the full output file path.
    """
    output_path = os.path.abspath(output_path)
    if os.path.isdir(output_path):
        # Compose filename from alt_source_path if provided, else source_path
        base = None
        if alt_source_path:
            base = os.path.basename(alt_source_path)
            base = os.path.splitext(base)[0]
        elif source_path:
            base = os.path.basename(source_path)
            base = os.path.splitext(base)[0]
        else:
            base = "result"
        filename = f"{base}_result.html"
        output_file = os.path.join(output_path, filename)
    else:
        # If parent directory does not exist, create it
        parent_dir = os.path.dirname(output_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        output_file = output_path
    return output_file

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

def save_response_to_file(response_text, file_path, is_json=False):
    """Save model response to the specified file path."""
    try:
        ensure_directory_exists(file_path)
        with open(file_path, 'w', encoding='utf-8') as f:
            if is_json:
                # If it's already a dictionary, dump as JSON
                if isinstance(response_text, dict):
                    json.dump(response_text, f, indent=2)
                else:
                    # Try to parse as JSON before saving
                    try:
                        json_data = json.loads(response_text)
                        json.dump(json_data, f, indent=2)
                    except json.JSONDecodeError:
                        # If not valid JSON, save as plain text
                        f.write(response_text)
                        print("WARNING: Response was not valid JSON. Saving as plain text.")
            else:
                f.write(response_text)
        print(f"Response saved to: {file_path}")
        return True
    except Exception as e:
        print(f"Error saving response to {file_path}: {e}")
        return False

def read_json_template(template_path):
    """Read a JSON template file."""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading JSON template file {template_path}: {e}")
        return None

def main():
    print("\n" + "="*80)
    print("🔧 AWREASON.PY EXECUTION STARTED")
    print("="*80)
    print(f"Script location: {__file__}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Command line args: {sys.argv}")
    print("="*80)
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Chat with O1 model using text and images')

    # Accept up to two PDF files (both must be files, not directories)
    parser.add_argument('--pdf_file1', type=str, help='First PDF file to process (all pages will be extracted as images)')
    parser.add_argument('--pdf_file2', type=str, help='Second PDF file to process (all pages will be extracted as images)')
    
    # Image joining options for PDF processing
    parser.add_argument('--join', choices=['vertical', 'horizontal'], help='Join extracted images in pairs')
    
    # Create a mutually exclusive group for prompt options
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument('--prompt', type=str, help='Text prompt to send to the model')
    prompt_group.add_argument('--promptfile', type=str, help='Path to file containing the prompt text')
    
    # Add markdown file option
    parser.add_argument('--md_file', type=str, help='Path to Markdown file to add as context')
    
    # Add JSON template option for structured output
    parser.add_argument('--jsonout_template', type=str, help='Path to JSON template file for structured output')
    
    # Add required output filepath parameter
    parser.add_argument('--output', type=str, required=True, help='Path to save the model response')
    
    # Add temp directory option for PDF processing
    parser.add_argument('--tempdir', type=str, help='Temporary directory for extracted images (default: system temp)')
    # Add image folders as optional arguments (so they always exist in args)
    parser.add_argument('--images_folder1', type=str, default=None, help='First folder containing image files')
    parser.add_argument('--images_folder2', type=str, default=None, help='Second folder containing image files')

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
    
    # If a markdown file is provided, read its content
    markdown_content = None
    if args.md_file:
        md_file_path = args.md_file
        # If .docx, convert to markdown first
        if md_file_path.lower().endswith(".docx"):
            temp_dir = args.tempdir if args.tempdir else tempfile.mkdtemp()
            md_file_path = convert_docx_to_md(md_file_path, temp_dir)
        markdown_content = read_markdown_file(md_file_path)
        if not markdown_content:
            print("Failed to read Markdown file. Exiting.")
            return
        print(f"Loaded Markdown content from: {md_file_path}")

        # If we also have a prompt, combine them
        if prompt_text:
            prompt_text = f"{prompt_text}\n\nHere is additional context from the Markdown file:\n\n{markdown_content}"
        else:
            prompt_text = markdown_content
    
    # Load JSON template if specified
    json_template = None
    if args.jsonout_template:
        json_template = read_json_template(args.jsonout_template)
        if not json_template:
            print("Failed to read JSON template. Exiting.")
            return
        print(f"Loaded JSON template from: {args.jsonout_template}")
        
        # Check if we need to enhance the prompt with JSON template info
        if prompt_text and json_template:
            template_description = json_template.get("description", "")
            if template_description:
                prompt_text += f"\n\nYou will provide your analysis in a structured JSON format. {template_description}"
            
            prompt_text += "\n\nPlease match the exact structure of the following JSON template in your response:"
            prompt_text += f"\n```json\n{json.dumps(json_template, indent=2)}\n```\n"
            prompt_text += "\nYour response must be valid JSON that follows this exact structure."
    
    # Set up image processing directories
    temp_dir = args.tempdir if args.tempdir else tempfile.mkdtemp()
    ensure_directory_exists(temp_dir)
    print(f"Using temporary directory: {temp_dir}")
    
    # Collect images based on the source option
    folder1_images = []
    folder2_images = []
    folder1_name = None
    folder2_name = None

    # Process --pdf_file1 (if provided)
    if args.pdf_file1:
        if not (os.path.isfile(args.pdf_file1) and args.pdf_file1.lower().endswith('.pdf')):
            print(f"Error: {args.pdf_file1} is not a valid PDF file. Exiting.")
            return
        pdf1_dir = process_pdf_to_images(args.pdf_file1, temp_dir, args.join)
        folder1_images = get_images_from_folder(pdf1_dir)
        folder1_name = Path(args.pdf_file1).name
        print(f"Processed PDF from --pdf_file1: {os.path.basename(args.pdf_file1)}")

    # Process --pdf_file2 (if provided)
    if args.pdf_file2:
        if not (os.path.isfile(args.pdf_file2) and args.pdf_file2.lower().endswith('.pdf')):
            print(f"Error: {args.pdf_file2} is not a valid PDF file. Exiting.")
            return
        pdf2_dir = process_pdf_to_images(args.pdf_file2, temp_dir, args.join)
        folder2_images = get_images_from_folder(pdf2_dir)
        folder2_name = Path(args.pdf_file2).name
        print(f"Processed PDF from --pdf_file2: {os.path.basename(args.pdf_file2)}")

    # If image folders are provided, they override the PDFs
    if args.images_folder1:
        folder1_images = get_images_from_folder(args.images_folder1)
        folder1_name = Path(args.images_folder1).name
        print(f"Collected {len(folder1_images)} images from folder 1")
    if args.images_folder2:
        folder2_images = get_images_from_folder(args.images_folder2)
        folder2_name = Path(args.images_folder2).name
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
    elif image_count == 0 and not args.md_file:
        print("WARNING: No image files found based on the provided arguments.")
        print("Note: The O1 model can work with text-only prompts, but since this tool is designed")
        print("for visual reasoning, you might want to check your input paths.")

    # Cross-platform .env loading
    dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    dotenv_path = os.path.abspath(os.path.expanduser(dotenv_path))
    
    # Debug: Print .env file loading info
    print("\n" + "="*60)
    print(".ENV FILE LOADING DEBUG:")
    print("="*60)
    print(f"Looking for .env file at: {dotenv_path}")
    print(f".env file exists: {os.path.exists(dotenv_path)}")
    
    load_dotenv_result = load_dotenv(dotenv_path=dotenv_path, override=True)
    print(f"load_dotenv() returned: {load_dotenv_result}")
    
    # Debug: Show all AZURE_ environment variables
    print("\nAll AZURE_ environment variables loaded:")
    azure_vars = {k: v for k, v in os.environ.items() if k.startswith("AZURE_")}
    for key in sorted(azure_vars.keys()):
        # Mask sensitive values
        value = azure_vars[key]
        if "KEY" in key or "SECRET" in key:
            value = value[:10] + "..." if len(value) > 10 else "[MASKED]"
        print(f"  {key} = {value}")
    print("="*60)

    # Retrieve environment variables with defaults
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_O1", "o1")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    tenant_id = os.getenv("AZURE_TENANT_ID")
    subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")

    # Debug: Print assigned environment variables
    print("\n" + "="*60)
    print("Azure OpenAI Configuration Debug Info:")
    print("="*60)
    print(f"Azure OpenAI Endpoint: {endpoint}")
    print(f"Deployment Name: {deployment}")
    print(f"API Version: {api_version}")
    print(f"Azure Tenant ID: {tenant_id if tenant_id else 'Not set (using DefaultAzureCredential)'}")
    print(f"Base URL that will be used: {endpoint}")
    
    # Validate configuration
    if not endpoint:
        print("ERROR: AZURE_OPENAI_ENDPOINT environment variable is not set!")
        sys.exit(1)
    if not deployment:
        print("ERROR: AZURE_OPENAI_DEPLOYMENT_O1 environment variable is not set!")
        sys.exit(1)
    if not api_version:
        print("ERROR: AZURE_OPENAI_API_VERSION environment variable is not set!")
        sys.exit(1)
        
    print("Configuration validation passed.")
    print("="*60)

    # Initialize Azure OpenAI client
    # Use API key authentication if available, otherwise use Entra ID
    if api_key:
        print("\n🔑 Using API Key authentication")
        client = AzureOpenAI(  
            azure_endpoint=endpoint,  
            api_key=api_key,  
            api_version=api_version,  
        )
    else:
        print("\n🔐 Using Entra ID (Azure AD) authentication")
        cognitiveServicesResource = "https://cognitiveservices.azure.com/"
        
        # Ensure Azure CLI path is in the environment PATH
        az_cli_path = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"
        if az_cli_path not in os.environ.get("PATH", ""):
            os.environ["PATH"] = az_cli_path + os.pathsep + os.environ.get("PATH", "")
            print(f"Added Azure CLI to PATH: {az_cli_path}")
        
        # Use DefaultAzureCredential which tries multiple authentication methods
        # It will attempt (in order): Environment, ManagedIdentity, SharedTokenCache, 
        # VisualStudioCode, AzureCli, AzurePowerShell, AzureDeveloperCli
        if tenant_id:
            print(f"Using DefaultAzureCredential with tenant ID: {tenant_id}")
            print("Will attempt multiple authentication methods (CLI, PowerShell, VSCode, etc.)")
            # Set environment variables for DefaultAzureCredential
            os.environ["AZURE_TENANT_ID"] = tenant_id
            if subscription_id:
                os.environ["AZURE_SUBSCRIPTION_ID"] = subscription_id
            credential = DefaultAzureCredential(
                exclude_visual_studio_code_credential=False,
                exclude_cli_credential=False,
                exclude_powershell_credential=False,
                process_timeout=60  # Increase timeout to 60 seconds
            )
        else:
            print("Using DefaultAzureCredential without tenant ID")
            credential = DefaultAzureCredential(process_timeout=60)
        
        token_provider = get_bearer_token_provider(  
            credential,  
            f'{cognitiveServicesResource}.default'  
        )  

        client = AzureOpenAI(  
            azure_endpoint=endpoint,  
            azure_ad_token_provider=token_provider,  
            api_version=api_version,  
        )
    
    # Set up the messages for the chat
    messages = [
        {"role": "system", "content": "You are a helpful assistant that can analyze both text and images. Provide detailed analysis when images are included."},
        {"role": "user", "content": []}
    ]
    
    # Modify the prompt text if both folders have images
    if folder1_images and folder2_images:
        # Use detected names or fallback
        folder1_name = folder1_name or "folder1"
        folder2_name = folder2_name or "folder2"
        document_explanation = (
            f"\n\nIMPORTANT: You will be provided with images from two different documents."
            f"\n- The first {len(folder1_images)} images (from '{folder1_name}') belong to DOCUMENT 1."
            f"\n- The next {len(folder2_images)} images (from '{folder2_name}') belong to DOCUMENT 2."
            f"\nThe images are provided in their correct page order within each document."
            f"\nPlease analyze each document separately, maintaining awareness of which images belong to which document."
        )
        
        # Append document explanation to the original prompt
        prompt_text += document_explanation # type: ignore
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
    
    # Configure additional parameters for the O1 model
    completion_params = {
        "model": deployment,
        "messages": messages,
        #"temperature": 0.2,  # Lower temperature for more deterministic results - not supported by O1
        # Note: seed parameter is not supported by o1 models with vision (images)
    }
    
    # Check API version and adjust parameters accordingly
    print(f"\n📝 CONFIGURING REQUEST PARAMETERS:")
    print(f"Using API version: {api_version}")
    
    # For newer API versions (2024-02-01-preview and later)
    if api_version.startswith("2024"):
        completion_params["max_completion_tokens"] = 15000
        # Add reasoning_effort only if we're not using structured output
        if not json_template:
            completion_params["reasoning_effort"] = "high"
    else:
        # For older API versions
        completion_params["max_tokens"] = 15000
    
    # Add response_format parameter for JSON output if template was provided
    if json_template:
        completion_params["response_format"] = {"type": "json_object"}
        print("Requesting structured JSON output from the model")
    
    # Send the request to the o1 model
    print("\nSending request to o1 model...")
    
    # Print detailed debugging information before making the API call
    print("\n" + "="*60)
    print("API CALL DEBUG INFORMATION")
    print("="*60)
    print(f"Model/Deployment: {completion_params['model']}")
    print(f"API Endpoint: {endpoint}")
    print(f"API Version: {client._api_version}")
    print(f"Number of messages: {len(completion_params['messages'])}")
    print(f"Total images in request: {total_images}")
    
    # Print request parameters (excluding image data for brevity)
    debug_params = completion_params.copy()
    if 'messages' in debug_params:
        debug_messages = []
        for msg in debug_params['messages']:
            debug_msg = msg.copy()
            if 'content' in debug_msg and isinstance(debug_msg['content'], list):
                content_summary = []
                for item in debug_msg['content']:
                    if item.get('type') == 'text':
                        text_preview = item['text'][:100] + ('...' if len(item['text']) > 100 else '')
                        content_summary.append(f"text: '{text_preview}'")
                    elif item.get('type') == 'image_url':
                        content_summary.append("image: [base64 data]")
                debug_msg['content'] = content_summary
            debug_messages.append(debug_msg)
        debug_params['messages'] = debug_messages
    
    print(f"Request parameters: {json.dumps(debug_params, indent=2)}")
    print("="*60)
    
    try:
        completion = client.chat.completions.create(**completion_params)
    except Exception as e:
        # Print comprehensive error information
        print("\n" + "="*60)
        print("ERROR DETAILS - FIRST ATTEMPT FAILED")
        print("="*60)
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")
        
        # Check for specific error types and provide detailed diagnostics
        status_code = getattr(e, 'status_code', None)
        if status_code:
            print(f"HTTP Status Code: {status_code}")
        if hasattr(e, 'response') and getattr(e, 'response', None):
            response = getattr(e, 'response')  # type: ignore
            print(f"Response Headers: {dict(response.headers) if hasattr(response, 'headers') else 'N/A'}")
            try:
                response_text = response.text if hasattr(response, 'text') else str(response)
                print(f"Response Body: {response_text}")
            except:
                print("Could not read response body")
        
        # Provide specific guidance based on error type
        if "404" in str(e) or "Not Found" in str(e):
            print("\n🔍 404 ERROR DIAGNOSIS:")
            print(f"   • Endpoint: {endpoint}")
            print(f"   • Deployment: {deployment}")
            print(f"   • API Version: {client._api_version}")
            print("   • Possible causes:")
            print("     1. Deployment name is incorrect or doesn't exist")
            print("     2. API version is not supported for this deployment")
            print("     3. Endpoint URL is incorrect")
            print("     4. Deployment is not in the same region as the endpoint")
            print("     5. You don't have access to this deployment")
            print("\n💡 Troubleshooting steps:")
            print("   1. Check Azure Portal for correct deployment name")
            print("   2. Verify the deployment supports the API version")
            print("   3. Ensure the deployment is in 'Succeeded' state")
            print("   4. Check your access permissions")
            
        print("\nRetrying with simplified parameters...")
        print("="*60)
        
        # Create a simplified parameter set (removing reasoning_effort)
        fallback_params = {
            "model": deployment,
            "messages": messages
            #,            "temperature": 0.2   #not supported by O1
        }
        
        # Use appropriate max tokens parameter based on API version
        if api_version.startswith("2024"):
            fallback_params["max_completion_tokens"] = 15000
        else:
            fallback_params["max_tokens"] = 15000
            
        # Keep response_format if template was provided
        if json_template:
            fallback_params["response_format"] = {"type": "json_object"}
        
        # Print fallback parameters
        print("\nFALLBACK REQUEST DEBUG:")
        print(f"Fallback parameters: {json.dumps({k: v for k, v in fallback_params.items() if k != 'messages'}, indent=2)}")
        print(f"Messages count: {len(fallback_params['messages'])}")
        
        try:
            completion = client.chat.completions.create(**fallback_params)
            print("✅ Fallback request succeeded!")
        except Exception as fallback_error:
            print("\n" + "="*60)
            print("FALLBACK REQUEST ALSO FAILED")
            print("="*60)
            print(f"Fallback Error Type: {type(fallback_error).__name__}")
            print(f"Fallback Error Message: {str(fallback_error)}")
            
            if hasattr(fallback_error, 'status_code'):
                print(f"Fallback HTTP Status Code: {getattr(fallback_error, 'status_code')}")
            if hasattr(fallback_error, 'response') and getattr(fallback_error, 'response', None):
                try:
                    response = getattr(fallback_error, 'response')  # type: ignore
                    response_text = response.text if hasattr(response, 'text') else str(response)
                    print(f"Fallback Response Body: {response_text}")
                except:
                    print("Could not read fallback response body")
            
            # Check for specific error patterns
            if "tenant" in str(fallback_error).lower():
                print("\n🚨 TENANT AUTHENTICATION ISSUE DETECTED!")
                print("Please check your Azure authentication:")
                print("1. Run 'az account show' to see current subscription")
                print("2. Run 'az account list' to see all available subscriptions")
                print("3. Run 'az account set --subscription <correct-subscription-id>'")
                print("4. Ensure your Azure OpenAI resource is in the same tenant")
            
            print("\n❌ Both primary and fallback requests failed.")
            print("Please check your Azure OpenAI configuration and authentication.")
            print("="*60)
            raise Exception(f"Both API attempts failed. Primary: {str(e)}, Fallback: {str(fallback_error)}")

    # Success logging
    print("\n✅ API REQUEST SUCCESSFUL!")
    print("="*60)
    
    # Get the response content
    response_content = completion.choices[0].message.content
    
    # Extract and report token usage and response details
    prompt_tokens = completion.usage.prompt_tokens
    completion_tokens = completion.usage.completion_tokens
    total_tokens = completion.usage.total_tokens
    
    print(f"Response Details:")
    print(f"  Model used: {completion.model}")
    print(f"  Response ID: {completion.id}")
    print(f"  Finish reason: {completion.choices[0].finish_reason}")
    print(f"  Response length: {len(response_content)} characters")
    
    print(f"\nToken Usage Summary:")
    print(f"  Input tokens:  {prompt_tokens:,}")
    print(f"  Output tokens: {completion_tokens:,}")
    print(f"  Total tokens:  {total_tokens:,}")
    print("="*60)
    
    # Print the response
    print("\nO1 Response:")
    if len(response_content) > 1000:
        # Print first 500 and last 500 characters if response is long
        print(response_content[:500] + "...\n...\n..." + response_content[-500:])
    else:
        print(response_content)

    # Determine the output file path (fix for --output as directory)
    # If both pdf_file1 and pdf_file2 are provided and output is a directory, use pdf_file2 for output filename
    output_file = ensure_output_path(
        args.output,
        source_path=args.pdf_file1 or args.images_folder1 or args.images_folder2,
        alt_source_path=args.pdf_file2 if (args.pdf_file1 and args.pdf_file2 and os.path.isdir(os.path.abspath(args.output))) else None
    )

    # Save the response to the specified output file (as JSON if template was provided)
    save_response_to_file(response_content, output_file, is_json=bool(json_template))

    # Clean up temporary directory if we created one
    if not args.tempdir and temp_dir and os.path.exists(temp_dir):
        import shutil
        try:
            shutil.rmtree(temp_dir)
            print(f"Cleaned up temporary directory: {temp_dir}")
        except Exception as e:
            print(f"Warning: Could not clean up temporary directory {temp_dir}: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n{'='*80}")
        print("💥 UNHANDLED ERROR OCCURRED")
        print(f"{'='*80}")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")
        
        # Print the full traceback
        import traceback
        print(f"\n📋 Full Traceback:")
        traceback.print_exc()
        
        print(f"\n{'='*80}")
        print("🔍 ENVIRONMENT DEBUGGING INFO")
        print(f"{'='*80}")
        
        # Print environment variables for debugging
        print(f"AZURE_OPENAI_ENDPOINT: {os.getenv('AZURE_OPENAI_ENDPOINT', 'NOT SET')}")
        print(f"AZURE_OPENAI_DEPLOYMENT_O1: {os.getenv('AZURE_OPENAI_DEPLOYMENT_O1', 'NOT SET')}")
        print(f"AZURE_OPENAI_API_VERSION: {os.getenv('AZURE_OPENAI_API_VERSION', 'NOT SET (will use default)')}")
        
        # Print Python and OpenAI library versions
        print(f"\n🐍 Python Version: {sys.version}")
        try:
            import openai
            print(f"📦 OpenAI Library Version: {openai.__version__}")
        except:
            print("📦 OpenAI Library Version: Could not determine")
            
        print(f"\n📂 Current Working Directory: {os.getcwd()}")
        print(f"📄 Script Location: {os.path.abspath(__file__)}")
        
        # Check Azure CLI status
        print(f"\n☁️ AZURE AUTHENTICATION STATUS:")
        try:
            import subprocess
            result = subprocess.run(['az', 'account', 'show'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print("✅ Azure CLI authenticated")
                print(f"Current account info: {result.stdout[:200]}...")
            else:
                print("❌ Azure CLI not authenticated or error occurred")
                print(f"Error: {result.stderr}")
        except Exception as cli_error:
            print(f"❌ Could not check Azure CLI status: {cli_error}")
        
        print(f"\n{'='*80}")
        print("🚨 For tenant mismatch errors, try:")
        print("   az account list")
        print("   az account set --subscription <correct-subscription-id>")
        print("   az login --tenant <correct-tenant-id>")
        print(f"{'='*80}")
        sys.exit(1)