#!/usr/bin/env python
# coding: utf-8
# some of this code is sourced from [liamca/GPT4oContentExtraction: Using Azure OpenAI GPT 4o to extract information such as text, tables and charts from Documents to Markdown](https://github.com/liamca/GPT4oContentExtraction)

import os
import re 
from tenacity import retry, wait_random_exponential, stop_after_attempt 
import shutil  
import json
import time
from dotenv import load_dotenv

# Azure OpenAI
from openai import AzureOpenAI , APIError
import io
import base64
import requests

# Image extraction from PDF
#import fitz  # PyMuPDF  
from pathlib import Path  
import uuid

# Load environment variables from .env file
load_dotenv(dotenv_path="../../.env", override=True)

# Azure AI Search Config
search_service_endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
if search_service_endpoint:
    search_service_name = search_service_endpoint.replace("https://", "").replace(".search.windows.net/", "")
else:
    search_service_name = os.getenv("AZURE_AI_SEARCH_SERVICE_NAME")
    
search_service_url = f"https://{search_service_name}.search.windows.net/"
search_admin_key = os.getenv("AZURE_AI_SEARCH_ADMIN_KEY")
index_name = os.getenv("AZURE_AI_SEARCH_INDEX")
index_schema_file = os.getenv("AZURE_AI_SEARCH_INDEX_SCHEMA_FILE", "index_schema.json")
search_index_text_content_field = os.getenv("AZURE_AI_SEARCH_TEXT_CONTENT_FIELD", "text")
search_index_vector_field = os.getenv("AZURE_AI_SEARCH_VECTOR_FIELD", "text_vector")
search_api_version = os.getenv("AZURE_AI_SEARCH_API_VERSION", "2024-05-01-preview")
search_headers = {  
    'Content-Type': 'application/json',  
    'api-key': search_admin_key  
}

# Azure OpenAI
openai_embedding_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
openai_embedding_api_key = os.getenv("AZURE_OPENAI_API_KEY")
openai_embedding_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
openai_embedding_model = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

# Initialize the embeddings client
embeddings_client = AzureOpenAI(
    api_version=openai_embedding_api_version,
    azure_endpoint=openai_embedding_endpoint,
    api_key=openai_embedding_api_key
)

# Use the same endpoint for GPT model
openai_gpt_endpoint = openai_embedding_endpoint
openai_gpt_api_key = openai_embedding_api_key
openai_gpt_api_version = openai_embedding_api_version
openai_gpt_model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

gpt_client = AzureOpenAI(
    api_key=openai_gpt_api_key,
    api_version=openai_gpt_api_version,
    azure_endpoint=openai_gpt_endpoint
)

supported_conversion_types = ['.pptx', '.ppt', '.docx', '.doc', '.xlsx', '.xls', '.pdf']

print ('Search Service Name:', search_service_name)
print ('Index Name:', index_name)
print ('Azure OpenAI GPT Base URL:', openai_gpt_endpoint)
print ('Azure OpenAI GPT Model:', openai_gpt_model)
print ('Azure OpenAI Embeddings Base URL:', openai_embedding_endpoint)
print ('Azure OpenAI Embeddings Model:', openai_embedding_model)


def reset_local_dirs():
    if os.path.exists('json'):
        remove_directory('json')
    if os.path.exists('images'):
        remove_directory('images')
    if os.path.exists('markdown'):
        remove_directory('markdown')
    if os.path.exists('pdf'):
        remove_directory('pdf')
    if os.path.exists('merged'):
        remove_directory('merged')
    if os.path.exists('tmp'):
        remove_directory('tmp')

# Create directory if it does not exist
def ensure_directory_exists(directory_path):  
    path = Path(directory_path)  
    if not path.exists():  
        path.mkdir(parents=True, exist_ok=True)  
        #print(f"Directory created: {directory_path}")  
    #else:  
        #print(f"Directory already exists: {directory_path}")  
  
# Remove a dir and sub-dirs
def remove_directory(directory_path):  
    try:  
        if os.path.exists(directory_path):  
            shutil.rmtree(directory_path)  
            #print(f"Directory '{directory_path}' has been removed successfully.")  
        else:  
            print(f"Directory '{directory_path}' does not exist.")  
    except Exception as e:  
        print(f"An error occurred while removing the directory: {e}")  
    
# Convert to PDF
"""
def convert_to_pdf(input_path):  
    file_suffix = pathlib.Path(input_path).suffix.lower()
    
    if file_suffix in supported_conversion_types:
        ensure_directory_exists('pdf')  
        
        output_file = input_path.replace(pathlib.Path(input_path).suffix, '')
        output_file = os.path.join('pdf', output_file + '.pdf')
    
        print ('Converting', input_path, 'to', output_file)
        if os.path.exists(output_file):
            os.remove(output_file)
    
        if file_suffix == '.pdf':
            # No need to convert, just copy
            shutil.copy(input_path, output_file)  
        else:
            # Command to convert pptx to pdf using LibreOffice  
            command = [  
                'soffice',  # or 'libreoffice' depending on your installation  
                '--headless',  # Run LibreOffice in headless mode (no GUI)  
                '--convert-to', 'pdf',  # Specify conversion format  
                '--outdir', os.path.dirname(output_file),  # Output directory  
                input_path  # Input file  
            ]  
              
            # Run the command  
            subprocess.run(command, check=True)  
            print(f"Conversion complete: {output_file}")  
    else:
        print ('File type not supported.')  
        return ""
    
    return output_file

# Convert pages from PDF to images
def extract_pdf_pages_to_images(pdf_path, image_dir):
    # Validate image_out directory exists
    doc_id = str(uuid.uuid4())
    image_out_dir = os.path.join(image_dir, doc_id)
    ensure_directory_exists(image_out_dir)  

    # Open the PDF file and iterate pages
    print ('Extracting images from PDF...')
    pdf_document = fitz.open(pdf_path)  

    for page_number in range(len(pdf_document)):  
        page = pdf_document.load_page(page_number)  
        image = page.get_pixmap()  
        image_out_file = os.path.join(image_out_dir, f'{page_number + 1}.png')
        image.save(image_out_file)  
        if page_number % 100 == 0:
            print(f'Processed {page_number} images...')  

    return doc_id
"""

# Base64 encode images
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
        
# Find all files in a dir
def get_all_files(directory_path):  
    files = []  
    for entry in os.listdir(directory_path):  
        entry_path = os.path.join(directory_path, entry)  
        if os.path.isfile(entry_path):  
            files.append(entry_path)  
    return files  
'''
@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
def extract_markdown_from_image(image_path):
    try:
        base64_image = encode_image(image_path)
        response = gpt_client.chat.completions.create(
            model=openai_gpt_model,
            messages=[
                { "role": "system", "content": "You are a helpful assistant." },
                { "role": "user", "content": [  
                    { 
                        "type": "text", 
                        "text": """Extract everything you see in this image to markdown. 
                            Convert all charts such as line, pie and bar charts to markdown tables and include a note that the numbers are approximate.
                        """ 
                    },
                    {
                        "type": "image_url", 
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                    }
                ] } 
            ],
            max_tokens=2000 
        )
        return response.choices[0].message.content
    except Exception as ex:
        return ""

def process_image(file, markdown_out_dir):
    if '.png' in file:
        print ('Processing:', file)
        markdown_file_out = os.path.join(markdown_out_dir, os.path.basename(file).replace('.png', '.txt'))
        print(markdown_file_out)
        if os.path.exists(markdown_file_out) == False:
            markdown_text = extract_markdown_from_image(file)
            with open(markdown_file_out, 'w', encoding='utf-8') as md_out:
                md_out.write(markdown_text)
        else:
            print ('Skipping processed file.')
    else:
        print ('Skipping non PNG file:', file)

    return file
'''

def extract_numeric_value(filename):  
    # Extract numeric value from filename using regular expression  
    match = re.search(r'(\d+)', filename)  
    return int(match.group(1)) if match else float('inf') 
    
#######################################
# Indexing Azure AI Search Utils
#######################################
def create_azs_index(re_use=True):
    """
    Create (or re-use) the Azure AI Search index.

    Parameters:
        re_use (bool): If True (default), the function checks if the index exists and exits if it does.
                       If False, the function drops any existing index and re-creates it.
    """
    dims = len(generate_embedding('That quick brown fox.'))
    print('Dimensions in Embedding Model:', dims)

    # Check for existing index if re_use is True
    get_index_url = f"{search_service_url}/indexes/{index_name}?api-version={search_api_version}"
    get_response = requests.get(get_index_url, headers=search_headers)
    if re_use:
        if get_response.status_code == 200:
            print(f"Index {index_name} exists. Reusing existing index because re_use is set to True.")
            return
        else:
            print(f"Index {index_name} does not exist. Proceeding to create a new index.")
    else:
        # If re_use is False and index exists, drop it.
        if get_response.status_code == 200:
            print(f"Index {index_name} exists. Dropping index because re_use is set to False.")
            delete_url = f"{search_service_url}/indexes/{index_name}?api-version={search_api_version}"
            del_response = requests.delete(delete_url, headers=search_headers)
            if del_response.status_code == 204:
                print(f"Index {index_name} deleted successfully.")
            else:
                print("Error deleting index, it may not exist.")

    # Load index schema from file and update its values
    with open(index_schema_file, "r") as f_in:
        index_schema = json.loads(f_in.read())
        index_schema['name'] = index_name
        index_schema['vectorSearch']['vectorizers'][0]['azureOpenAIParameters']['resourceUri'] = openai_embedding_endpoint
        index_schema['vectorSearch']['vectorizers'][0]['azureOpenAIParameters']['deploymentId'] = openai_embedding_model
        index_schema['vectorSearch']['vectorizers'][0]['azureOpenAIParameters']['apiKey'] = openai_embedding_api_key

    # Create the index by making a POST request
    create_index_url = f"{search_service_url}/indexes?api-version={search_api_version}"
    response = requests.post(create_index_url, headers=search_headers, json=index_schema)
      
    # Check the response  
    if response.status_code == 201:
        print(f"Index {index_name} created successfully.")
    else:
        print(f"Error creating index {index_name} :")
        print(response.json())


def extract_numeric_value(filename):  
    # Extract numeric value from filename using regular expression  
    match = re.search(r'(\d+)', filename)  
    return int(match.group(1)) if match else float('inf') 
        
# Function to generate vectors for title and content fields, also used for query vectors
max_attempts = 6
max_backoff = 60
@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(max_attempts))
def generate_embedding(text):
    if text == None:
        return None
        
    if len(text) < 10:
        return None
        
    client = AzureOpenAI(
        api_version=openai_embedding_api_version,
        azure_endpoint=openai_embedding_endpoint,
        api_key=openai_embedding_api_key
    )    
    counter = 0
    incremental_backoff = 1   # seconds to wait on throttline - this will be incremental backoff
    while True and counter < max_attempts:
        try:
            response = client.embeddings.create(
                input=text,
                model=openai_embedding_model
            )
            return json.loads(response.model_dump_json())["data"][0]['embedding']
        except APIError as ex:
            # Handle throttling - code 429
            if str(ex.code) == "429":
                incremental_backoff = min(max_backoff, incremental_backoff * 1.5)
                print ('Waiting to retry after', incremental_backoff, 'seconds...')
                time.sleep(incremental_backoff)
            elif str(ex.code) == "content_filter":
                print ('API Error', ex.code)
                return None
        except Exception as ex:
            counter += 1
            print ('Error - Retry count:', counter, ex)
    return None


def process_json_orig(file, doc_id, doc_name, markdown_out_dir, json_out_dir):
    if '.md' in file:
        with open(os.path.join(markdown_out_dir, file), 'r') as c_in:
            content = c_in.read()

        json_data = {
            'doc_id': doc_id, 
            'doc_name': doc_name, 
            'chunk_number': int(file.replace('chunk_', '').replace('.md', '')),
            'content': content
            }

        json_data['vector'] = generate_embedding(json_data['content'])


        with open(os.path.join(json_out_dir, file.replace('.md', '.json')), 'w') as c_out:
            c_out.write(json.dumps(json_data, indent=4))

    else:
        print ('Skipping non JSON file:', file)

    return file

def process_json(file, doc_id, doc_name, markdown_out_dir, json_out_dir):
    if '.md' in file:
        with open(os.path.join(markdown_out_dir, file), 'r') as c_in:
            content = c_in.read()

        json_data = {
            'doc_id': doc_id, 
            'doc_name': doc_name, 
            'chunk_number': int(file.replace('chunk_', '').replace('.md', '')),
            f'{search_index_text_content_field}': content
            }

        json_data[f'{search_index_vector_field}'] = generate_embedding(json_data[f'{search_index_text_content_field}'])


        with open(os.path.join(json_out_dir, file.replace('.md', '.json')), 'w') as c_out:
            c_out.write(json.dumps(json_data, indent=4))

    else:
        print ('Skipping non JSON file:', file)

    return file

def check_document_exists(doc_id):
    """
    Check if a document with the specified doc_id exists in the Azure AI Search index.

    This function sends a GET request to the Azure AI Search endpoint using a filter query
    to check for documents with a matching doc_id key. The filter used is:
        "doc_id eq '<doc_id>'"
    If one or more documents are found, the function returns True; otherwise, it returns False.

    Parameters:
        doc_id (str): The document ID to check. This should be URL-safe encoded if required.

    Returns:
        bool: True if a document with the given doc_id exists in the index, otherwise False.
    """
    # Construct the search URL with a filter on the doc_id field
    search_filter = f"doc_id eq '{doc_id}'"
    search_url = f"{search_service_url}/indexes/{index_name}/docs?api-version={search_api_version}&$filter={search_filter}"
    
    try:
        response = requests.get(search_url, headers=search_headers)
        if response.status_code == 200:
            result = response.json()
            # Check if there are any documents returned in the "value" field
            if "value" in result and len(result["value"]) > 0:
                print(f"Document with id {doc_id} exists in the index.")
                return True
            else:
                print(f"No document with id {doc_id} found in the index.")
                return False
        else:
            print(f"Error checking document existence. Status code: {response.status_code}")
            print(response.json())
            return False
    except Exception as e:
        print("Exception occurred during document existence check:", e)
        return False

def delete_doc_from_index_by_doc_name(doc_name):
    """
    Delete all documents from the Azure AI Search index that have the specified doc_name.

    This function sends a GET request with a filter to retrieve all documents with a matching
    doc_name field. It then builds a batch deletion payload using the doc_id of each matching document
    and sends a POST request to the indexing endpoint to delete them in a single batch.
    
    Parameters:
        doc_name (str): The document name to match against the "doc_name" field in the index.
        
    Returns:
        bool: True if the deletion batch call was successful (HTTP 200 or 204), False otherwise.
    """
    # Construct the search filter for the doc_name field
    search_filter = f"doc_name eq '{doc_name}'"
    search_url = f"{search_service_url}/indexes/{index_name}/docs?api-version={search_api_version}&$filter={search_filter}"
    
    try:
        # Get all documents that match the doc_name
        response = requests.get(search_url, headers=search_headers)
        if response.status_code == 200:
            result = response.json()
            if "value" in result and len(result["value"]) > 0:
                # Build batch delete payload using the doc_ids of matching documents
                delete_payload = {
                    "value": [
                        {
                            "@search.action": "delete",
                            "doc_id": doc["doc_id"]
                        }
                        for doc in result["value"]
                    ]
                }
                delete_url = f"{search_service_url}/indexes/{index_name}/docs/index?api-version={search_api_version}"
                del_response = requests.post(delete_url, headers=search_headers, json=delete_payload)
                if del_response.status_code in (200, 204):
                    print(f"Documents with doc_name '{doc_name}' deletion initiated successfully.")
                    return True
                else:
                    print(f"Error deleting documents with doc_name '{doc_name}':")
                    print(del_response.json())
                    return False
            else:
                print(f"No documents with doc_name '{doc_name}' found in the index.")
                return True
        else:
            print(f"Error retrieving documents for deletion. Status code: {response.status_code}")
            print(response.json())
            return False
    except Exception as e:
        print("Exception occurred during deletion by doc_name:", e)
        return False

def delete_doc_from_index_by_doc_id(doc_id):
    """
    Delete a document from the Azure AI Search index using the specified doc_id as the key.

    This function sends a batch delete request to the Azure AI Search indexing endpoint. The 
    payload specifies a delete action for the document with the given doc_id. If the deletion 
    is successful (HTTP 200 or 204), it prints a success message and returns True; otherwise, 
    it prints an error and returns False.

    Parameters:
        doc_id (str): The URL-safe encoded document ID for the document to be deleted.
    
    Returns:
        bool: True if deletion was successful, otherwise False.
    """
    delete_payload = {
        "value": [
            {
                "@search.action": "delete",
                "doc_id": doc_id
            }
        ]
    }
    
    delete_url = f"{search_service_url}/indexes/{index_name}/docs/index?api-version={search_api_version}"
    response = requests.post(delete_url, headers=search_headers, json=delete_payload)
    
    if response.status_code in (200, 204):
        print(f"Document with id {doc_id} deletion initiated successfully..")
        return True
    else:
        print(f"Error deleting document with id {doc_id}:")
        print(response.json())
        return False


def index_content(json_files):
    """
    Index the content of a set of JSON files in Azure Cognitive Search.

    This function reads a list of JSON files that represent chunks of documents intended for indexing.
    It performs the following steps:
      1. Constructs the URL for indexing documents using the search service URL, the index name,
         and the API version.
      2. Creates an empty "documents" dictionary with a key "value" that will be used to store
         individual JSON document objects (each representing a document chunk to be indexed).
      3. Iterates over each file in the provided list. For every file with a '.json' extension:
         - Opens the file and loads its JSON content.
         - Modifies the 'doc_id' field by appending a hyphen and the chunk_number (this helps
           uniquely identify each chunk).
         - Adds the modified JSON data to the list under "documents['value']".
      4. Once the list of documents reaches the batch size (50 in this case), it makes a POST
         request to the Azure Search indexing endpoint.
         - If the response status code is 200, it prints a success message.
         - If not, it prints an error message along with the response.
         - The documents dictionary is then reset for the next batch.
      5. After processing all files, any remaining documents in the current batch are also sent
         to the service.
    
    There is no return value – the function prints status messages to indicate whether indexing was
    successful or if errors occurred.

    Example index definition (loaded via the 'index_schema_file') is expected to have this structure:
    
        {
          "name": "<index_name>",
          "fields": [
            { "name": "doc_id", "type": "Edm.String", "key": true, "searchable": false },
            { "name": "doc_name", "type": "Edm.String", "searchable": true },
            { "name": "text", "type": "Edm.String", "searchable": true },
            { "name": "chunk_number", "type": "Edm.Int32", "searchable": false },
            // Additional field definitions...
          ],
          "vectorSearch": {
            "algorithmConfiguration": {
                "name": "defaultConfiguration",
                "similaryFunction": "cosineSimilarity"
            },
            "vectorizers": [
              {
                "name": "azureOpenAIVectorizer",
                "azureOpenAIParameters": {
                  "resourceUri": "<openai_embedding_api_base>",
                  "deploymentId": "<openai_embeddings_model>",
                  "apiKey": "<openai_embedding_api_key>"
                }
              }
            ]
          }
        }
    
    In this index definition:
      - "name" specifies the index name.
      - "fields" is a list of definitions of fields (with properties such as key, searchable, etc.).
      - "vectorSearch" contains settings needed for vector-based search, including the configuration
        for using an OpenAI embedding model as the vectorizer.
    
    Parameters:
        json_files (list of str): List of file paths to JSON files that need to be indexed.
    """
    batch_size = 50
    index_doc_url = f"{search_service_url}/indexes/{index_name}/docs/index?api-version={search_api_version}"
    documents = {"value": []}

    for file in json_files:
        if '.json' in file:
            with open(file, 'r') as j_in:
                json_data = json.loads(j_in.read())
            # Modify the document ID to include the chunk number, ensuring unique doc_ids
            json_data['doc_id'] = json_data['doc_id'] + '-' + str(json_data['chunk_number'])
            documents["value"].append(json_data)
            if len(documents["value"]) == batch_size:
                response = requests.post(index_doc_url, headers=search_headers, json=documents)
                # Check the response
                if response.status_code == 200:
                    print(f"Document batch of {batch_size} documents indexed successfully.")
                    # Uncomment to print detailed response:
                    # print(json.dumps(response.json(), indent=2))
                else:
                    print(f"index_content:Error indexing document {file} :")
                    print("index_content: response:",response.json())
                documents = {"value": []}
                
    # Process any remaining documents
    response = requests.post(index_doc_url, headers=search_headers, json=documents)
    if response.status_code == 200:
        print(f"index_content: remaining Documents Indexed successfully.")
        # Uncomment to print detailed response:
        # print(json.dumps(response.json(), indent=2))
    else:
        print(f"Error indexing documents {file} :")
        print(response.json())
    documents = {"value": []}

def get_doc_name(dir_path):   #Obsolete///
    """
    Retrieve the most recent document identifier from a given directory.

    This function takes a path to a directory and lists its contents. It then filters those
    contents to include only subdirectories. If one or more subdirectories exist, it prints
    and returns the name of the first subdirectory in the list. If no subdirectories are found,
    it prints an error message and returns None.
    
    Parameters:
        dir_path (str): The path to the directory where document IDs (folders) are stored.
    
    Returns:
        str or None: The name of the first subdirectory (document ID) found, or None if no
                     subdirectory exists.
    """
    entries = os.listdir(dir_path)
    directories = [entry for entry in entries if os.path.isdir(os.path.join(dir_path, entry))]
    if len(directories) > 0:
        print('doc_id:', directories[0])
        return directories[0]
    else:
        print('Could not find most recent doc_id')
        return None

def get_doc_id(dir_path, doc_name):
    """
    Retrieve the most recent document identifier from a given directory that contains 
    the provided doc_name, and return a URL-safe Base64 encoded version of it.

    This function takes a path to a directory and lists its contents. It then filters those
    contents to include only subdirectories whose names contain the doc_name value (case-insensitive).
    If one or more matching subdirectories are found, the first matching directory name is encoded 
    using URL-safe Base64 encoding, printed, and returned. If no such subdirectory is found, it 
    prints an error message and returns None.
    
    Parameters:
        dir_path (str): The path to the directory where document ID folders are stored.
        doc_name (str): The substring to match in the subdirectory names.
    
    Returns:
        str or None: The URL-safe Base64 encoded document ID, or None if no matching subdirectory exists.
    """
    import base64
    entries = os.listdir(dir_path)
    directories = [entry for entry in entries 
                   if os.path.isdir(os.path.join(dir_path, entry)) and doc_name.lower() in entry.lower()]
    if len(directories) > 0:
        raw_doc_id = directories[0]
        encoded_doc_id = base64.urlsafe_b64encode(raw_doc_id.encode("utf-8")).decode("utf-8")
        print('doc_id (urlsafe encoded):', encoded_doc_id)
        return encoded_doc_id
    else:
        print('Could not find a document ID containing:', doc_name)
        return None