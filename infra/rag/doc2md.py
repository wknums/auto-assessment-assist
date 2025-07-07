import logging
import json
import os
import sys
import uuid
from pathlib import Path
from loguru import logger
from datetime import datetime
#from dotenv import find_dotenv, load_dotenv
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

timestamp = datetime.now().strftime("%Y-%m-%d")
#logger.remove()  # To not show the logs in the console
logger.add(f"../../logs/logs_{timestamp}.log", rotation="23:59", compression="zip")

config = json.load(open("../../config.json"))

# Azure AI Search Config
AZURE_AI_ENDPOINT = config["azure_ai_endpoint"]
AZURE_AI_API_VERSION = config.get("azure_ai_api_version", "2024-12-01-preview")
#load_dotenv(find_dotenv())
logging.basicConfig(level=logging.INFO)

#AZURE_AI_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT")
#AZURE_AI_API_VERSION = os.getenv("AZURE_AI_API_VERSION", "2024-12-01-preview")
#print(f"Using Azure AI endpoint: {AZURE_AI_ENDPOINT}")
#print(f"Using Azure AI API version: {AZURE_AI_API_VERSION}")

# Add the parent directory to the path to use shared modules
parent_dir = Path(Path.cwd()).parent
sys.path.append(str(parent_dir))
from python.content_understanding_client import AzureContentUnderstandingClient

credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

client = AzureContentUnderstandingClient(
    endpoint=AZURE_AI_ENDPOINT,
    api_version=AZURE_AI_API_VERSION,
    token_provider=token_provider,
    x_ms_useragent="azure-ai-content-understanding-python/content_extraction",  # telemetry header
)

# Utility function to save images
from PIL import Image
from io import BytesIO
import re

def save_image(image_id: str, response):
    raw_image = client.get_image_from_analyze_operation(
        analyze_response=response,
        image_id=image_id
    )
    image = Image.open(BytesIO(raw_image))
    Path(".cache").mkdir(exist_ok=True)
    image.save(f".cache/{image_id}.jpg", "JPEG")

def extract_md_from_doc(source_doc, markdown_filename):
    """
    Extract markdown content from a document using Azure Content Understanding.

    Uses a predefined analyzer template (content_document.json) to create an analyzer,
    analyze the source document, and extract markdown content from the API result.
    The extracted markdown is saved to the specified output file.

    Parameters:
        source_doc (str): The path to the source document (e.g. .pdf or .docx).
        markdown_filename (str): The path (including filename) where the markdown output
                                 will be saved.
    """
    ANALYZER_ID = "content-doc-sample-" + str(uuid.uuid4())
    ANALYZER_TEMPLATE_FILE = './content_document.json'
    print("extract_md_from_doc: Content Understanding client source document: ", source_doc, " md_filename: ",markdown_filename,"\n ")
    #print("extract_md_from_doc: Content Understanding client ANALYZER_FILE: ", ANALYZER_TEMPLATE_FILE, "ANALYZER_ID: ", ANALYZER_ID ,"\n ")
    logger.info(f"Content Understanding client source document: '{source_doc}' md_filename: '{markdown_filename}' ANALYZER_TEMPLATE_FILE: '{ANALYZER_TEMPLATE_FILE}'")
    # Use the provided source document for analysis
    with open(ANALYZER_TEMPLATE_FILE, 'r') as f:
        analyzer_template = json.load(f)

    # Create analyzer
    try:
        response = client.begin_create_analyzer(ANALYZER_ID, analyzer_template=analyzer_template)
        result = client.poll_result(response)
    except Exception as e:
        print(f"extract_md_from_doc: Error creating analyzer: {e}")
        logger.error(f"Error creating analyzer: {e}")
    # Run the analyzer on the source document
    try:
        response = client.begin_analyze(ANALYZER_ID, file_location=source_doc)
        result = client.poll_result(response)
    except Exception as e:
        print(f"extract_md_from_doc: Error running analyzer: {e}")
        logger.error(f"Error running analyzer: {e}")
        
        
    json_result = json.dumps(result, indent=2)
    #print("extract_md_from_doc: Debug json extracted:" , json_result[0:500])

    # Extract markdown content from the API result
    contents = result.get("result", {}).get("contents", [])
    if contents:
        markdown_value = contents[0].get("markdown", "")
        #print(markdown_value)
        with open(markdown_filename, "w") as f:
            f.write(markdown_value)   
        print(f"extract_md_from_doc: Markdown content extracted and saved to '{markdown_filename}'") 
        logger.info(f"Markdown content extracted and saved to {markdown_filename}")   
    else:
        print("extract_md_from_doc: No markdown content found.")
        logger.info("No markdown content found.")   

    client.delete_analyzer(ANALYZER_ID)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract markdown from a document and save it to a file."
    )
    parser.add_argument(
        "source_doc",
        help="The file path to the source document (e.g. .pdf, .docx, or .md) to process."
    )
    parser.add_argument(
        "target_directory",
        help="The target directory where the output markdown file will be stored. "
             "The output filename is generated by appending '.md' to the source document's basename."
    )

    args = parser.parse_args()

    # Ensure target directory exists
    os.makedirs(args.target_directory, exist_ok=True)

    # Generate the output markdown filename based on the source document's basename.
    base_filename = os.path.basename(args.source_doc)
    markdown_output = os.path.join(args.target_directory, base_filename + ".md")

    # If the input is already a markdown file, just copy it
    if args.source_doc.lower().endswith(".md"):
        import shutil
        shutil.copy(args.source_doc, markdown_output)
        print(f"Copied markdown file to '{markdown_output}'")
    # If the input is a .docx file, convert it to markdown using python-docx and markdownify
    elif args.source_doc.lower().endswith(".docx"):
        try:
            from docx import Document
            from markdownify import markdownify as md
        except ImportError:
            print("Please install python-docx and markdownify: pip install python-docx markdownify")
            sys.exit(1)
        doc = Document(args.source_doc)
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        doc_text = "\n".join(full_text)
        md_text = md(doc_text)
        with open(markdown_output, "w", encoding="utf-8") as f:
            f.write(md_text)
        print(f"Converted DOCX to markdown and saved to '{markdown_output}'")
    else:
        # Default: use Azure Content Understanding for .pdf and other supported formats
        extract_md_from_doc(args.source_doc, markdown_output)