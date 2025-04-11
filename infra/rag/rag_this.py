#This code implementes the end-to-end process of passing one document to be ingested and indexed to Azure AI Search for use in the RAG model. The code is broken down into the following steps:

import os, sys
import argparse
import concurrent.futures  
from functools import partial  
# Utils
from doc2md_utils import *
from chunk_md import chunk_text, save_chunks
from doc2md import extract_md_from_doc
from dotenv import load_dotenv

def rag_this(input_file, md_target_folder="./rag_md_out", keep_index=False):
    """
    Process an input document for RAG ingestion by:
      1. Extracting markdown content from the source document.
      2. Chunking the extracted markdown.
      3. Converting chunks to JSON and indexing them in Azure AI Search.
    
    Parameters:
        input_file (str): The file path to the source document (e.g., .pdf or .docx).
        md_target_folder (str): Optional directory to store the extracted markdown and chunks.
                                  Defaults to "./rag_md_out".
        keep_index (bool): If True, reuse the existing search index; if False, drop and re-create it.
    """
    # Load environment variables
    load_dotenv(dotenv_path="../../.env", override=True)
    
    # Ensure the markdown target folder exists
    os.makedirs(md_target_folder, exist_ok=True)

    # Generate the output markdown filename based on the source document's basename.
    base_filename = os.path.basename(input_file)
    markdown_output = os.path.join(md_target_folder, base_filename + ".md")
    
    # Step 1: Extract markdown content from the source document
    extract_md_from_doc(input_file, markdown_output)
    
    # Step 2: Chunk the markdown content into smaller pieces
    chunked_output_folder = os.path.join(md_target_folder, base_filename)
    with open(markdown_output, 'r', encoding='utf-8') as f:
        markdown_text = f.read()
    chunks = chunk_text(markdown_text)
    save_chunks(chunks, chunked_output_folder)
    
    # Use the chunked output folder as the markdown path for indexing
    markdown_path = md_target_folder
    
    # Load the Index Schema as defined in your config and re-create the index unless keep_index is set to True
    create_azs_index(re_use=keep_index)

    # List entries in the markdown path
    entries = os.listdir(markdown_path)
    #print(f"Number of entries: {len(entries)}")
    #print(f"Entries: {entries}")
    directories = [entry for entry in entries if os.path.isdir(os.path.join(markdown_path, entry))]
    #print(f"Number of directories: {len(directories)}")
    #print(f"Directories: {directories}")

    # Retrieve doc_id and doc_name from the markdown folder
    doc_id = get_doc_id(markdown_path, base_filename)
    #lets first check if this doc_id already exists in the index: - exit if it does as we are done...
    if check_document_exists(doc_id):
        print(f"rag_this: Document with ID {doc_id} already exists in the index.")
        return
    
    #doc_name = doc2md_utils.get_doc_name(markdown_path) # This is not needed as we already have the base_filename
    doc_name = base_filename
    markdown_out_dir = os.path.join(markdown_path, doc_name)
    #print(f"get_doc_id:Markdown out directory: {markdown_out_dir}")
    #print(f"get_doc_id:Doc ID: {doc_id}")
    #print(f"get_doc_id: Doc Name: {doc_name}")

    # Get the markdown chunk files for further processing
    files = os.listdir(markdown_out_dir)
    txt_files = [f for f in files if f.endswith('.md')]
    total_files = len(txt_files)
    #print('rag_this:Total Markdown Files:', total_files)

    # Vectorize the JSON content in parallel
    max_workers = 15
    json_out_dir = os.path.join('json', doc_name)
    ensure_directory_exists(json_out_dir)


    partial_process_json = partial(
        process_json, 
        doc_id=doc_id, 
        doc_name=doc_name, 
        markdown_out_dir=markdown_out_dir, 
        json_out_dir=json_out_dir
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(partial_process_json, txt_files))
    #print("rag_this:",results)

    json_files = get_all_files(json_out_dir)
    total_json_files = len(json_files)
    #print('rag_this:Total New JSON Files:', total_json_files)

    # Index the JSON content to Azure AI Search
    index_content(json_files)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process a source document for RAG ingestion and index its content to Azure AI Search."
    )
    parser.add_argument(
        "input_file",
        help="The file path to the source document (e.g., .pdf or .docx) to be ingested."
    )
    parser.add_argument(
        "--md_folder",
        default="./rag_md_out",
        help="Optional folder where the extracted markdown file and chunks will be stored. Defaults to './rag_md_out'."
    )
    # Use store_true so that simply including --keep_index will set it to True; otherwise it will be False.
    parser.add_argument(
        "--keep_index",
        action="store_true",
        help="If set, reuse the existing search index; if not set, drop and re-create the index."
    )
    args = parser.parse_args()

    rag_this(args.input_file, args.md_folder, keep_index=args.keep_index)