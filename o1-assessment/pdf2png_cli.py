#!/usr/bin/env python


# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.


import os
import sys
import argparse
from pathlib import Path
from pdf2png_utils import extract_pdf_pages_to_images, join_images_in_pairs

def main():
    """
    Command-line interface for PDF to PNG extraction and image joining.
    """
    parser = argparse.ArgumentParser(
        description="Extract images from PDF documents and optionally join them in pairs"
    )
    parser.add_argument(
        "pdfsource", 
        type=str,
        help="Path to folder containing PDF files to process"
    )
    parser.add_argument(
        "--imagedir", 
        type=str, 
        default=os.path.join(".", "docimages"), 
        help="Parent output directory for extracted images"
    )
    parser.add_argument(
        "--joinv", 
        action="store_true", 
        help="Join images vertically in pairs"
    )
    parser.add_argument(
        "--joinh", 
        action="store_true", 
        help="Join images horizontally in pairs"
    )
    
    args = parser.parse_args()
    
    # Validate that pdfsource is a directory
    pdfsource = os.path.abspath(args.pdfsource)
    if not os.path.isdir(pdfsource):
        print(f"Error: {pdfsource} is not a valid directory")
        sys.exit(1)
    
    # Ensure the parent output directory exists
    imagedir = os.path.abspath(args.imagedir)
    os.makedirs(imagedir, exist_ok=True)
    
    # Process all PDF files in the directory
    pdf_files = [f for f in os.listdir(pdfsource) if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print(f"Error: No PDF files found in {pdfsource}")
        sys.exit(1)
        
    print(f"Found {len(pdf_files)} PDF files to process")
    
    # Process each PDF file
    for pdf_file in pdf_files:
        pdf_path = os.path.join(pdfsource, pdf_file)
        pdf_name = os.path.splitext(pdf_file)[0]  # Get filename without .pdf extension
        
        print(f"Processing {pdf_file}...")
        
        # Extract images from the PDF
        output_dir = os.path.join(imagedir, pdf_name)
        print(f"Extracting images to {output_dir}")
        extract_pdf_pages_to_images(pdf_path, imagedir)
        
        # Join images if requested
        if args.joinv and args.joinh:
            print("Error: Cannot specify both --joinv and --joinh")
            sys.exit(1)
        elif args.joinv or args.joinh:
            image_folder = os.path.join(imagedir, pdf_name)
            join_direction = 'vertical' if args.joinv else 'horizontal'
            print(f"Joining images {join_direction}ly from {image_folder}")
            join_images_in_pairs(image_folder, join_direction=join_direction)
    
    print("PDF processing complete!")

if __name__ == "__main__":
    main()