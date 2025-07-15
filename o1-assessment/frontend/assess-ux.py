import streamlit as st
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import base64

# Get the absolute path to the o1-assessment directory
CURRENT_FILE = Path(__file__).resolve()
FRONTEND_DIR = CURRENT_FILE.parent
REPO_ROOT = FRONTEND_DIR.parent  # o1-assessment directory
O1_ASSESSMENT_DIR = REPO_ROOT    # same as o1-assessment directory
STATIC_DIR = FRONTEND_DIR / "static"  # Static assets directory

# Add the o1-assessment directory to the path to be able to import awreason
sys.path.append(str(O1_ASSESSMENT_DIR))

# Set page title and configuration
st.set_page_config(
    page_title="AWReason - AI Assessment Tool",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Add custom CSS for styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        margin-bottom: 1rem;
    }
    .section-header {
        font-size: 1.5rem;
        margin: 1rem 0;
    }
    .stButton button {
        width: 100%;
        background-color: #4CAF50;
        color: white;
        padding: 10px 15px;
        font-size: 1.2rem;
    }
    .info-box {
        background-color: #e0e0e0;
        color: #000000;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 1rem 0;
    }
    .output-area {
        margin-top: 2rem;
        padding: 1rem;
        border: 1px solid #e0e0e0;
        border-radius: 0.5rem;
    }
    .footer {
        margin-top: 3rem;
        text-align: center;
        color: #888;
    }
    .console-output {
        background-color: #333333;
        color: #cccccc;
        padding: 10px;
        border-radius: 5px;
        font-family: 'Courier New', monospace;
        height: 300px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-wrap: break-word;
    }
    .result-panel {
        background-color: #111111;
        color: #e0e0e0;
        padding: 20px;
        border-radius: 5px;
        margin-top: 20px;
        width: 100%;
    }
    .result-panel h2 {
        color: #ffffff;
        border-bottom: 1px solid #444444;
        padding-bottom: 10px;
    }
    .result-panel a {
        color: #4CAF50;
    }
</style>
""", unsafe_allow_html=True)

def get_binary_file_downloader_html(bin_file, file_label='File'):
    """Generate a download link for a binary file"""
    with open(bin_file, 'rb') as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    href = f'<a href="data:application/octet-stream;base64,{b64}" download="{os.path.basename(bin_file)}">{file_label}</a>'
    return href

def display_image(image_path, alt_text="AI Assessment Tool"):
    """Display an image from a file path with fallback"""
    try:
        if os.path.exists(image_path):
            # If the image exists, display it
            return st.image(image_path, caption=alt_text, use_container_width=True)
        else:
            # If the image doesn't exist, show a placeholder with the app name
            st.warning(f"Image file not found: {image_path}")
            # Create a simple text-based logo as fallback
            st.markdown(
                f"""
                <div style="text-align: center; padding: 20px; background-color: #f8f9fa; border-radius: 10px;">
                    <h1 style="color: #4CAF50;">{alt_text}</h1>
                    <p>AI-powered assessment tool</p>
                </div>
                """, 
                unsafe_allow_html=True
            )
    except Exception as e:
        st.error(f"Error displaying image: {e}")

def display_file_content(file_path):
    """Display the content of a file based on its extension"""
    _, file_extension = os.path.splitext(file_path)
    
    if file_extension.lower() in ['.json']:
        try:
            import json
            with open(file_path, 'r', encoding='utf-8') as f:
                content = json.load(f)
            st.json(content)
        except Exception as e:
            st.error(f"Error displaying JSON file: {e}")
            with open(file_path, 'r', encoding='utf-8') as f:
                st.text(f.read())
    elif file_extension.lower() in ['.txt', '.md', '.html']:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if file_extension.lower() == '.html':
            # Create a container with styling for HTML content
            st.markdown(
                f"""
                <div style="background-color: #222222; padding: 20px; border-radius: 5px; margin: 10px 0;">
                    {content}
                </div>
                """, 
                unsafe_allow_html=True
            )
        elif file_extension.lower() == '.md':
            st.markdown(content)
        else:
            st.text(content)
    else:
        st.warning(f"Preview not available for {file_extension} files. Please download to view.")

def run_assessment(prompt_file_path, pdf_files, join_option, json_template_path, output_dir, 
                  status_placeholder, console_placeholder, console_output):
    """Run the assessment using awreason.py script"""
    
    try:
        # The awreason.py script is in the parent directory of frontend
        awreason_path = REPO_ROOT / "awreason.py"
        
        # Update console output
        console_output += f"Using awreason.py at: {awreason_path}\n"
        console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
        
        if not awreason_path.exists():
            error_msg = f"Error: awreason.py not found at {awreason_path}"
            status_placeholder.error(error_msg)
            console_output += f"{error_msg}\n"
            console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
            return None
        
        # Build the command with the appropriate parameters
        cmd = [sys.executable, str(awreason_path)]
        
        # Add prompt file
        cmd.extend(["--promptfile", prompt_file_path])
        
        # Add PDF files (up to 2)
        if len(pdf_files) > 0:
            cmd.extend(["--pdf_file1", pdf_files[0]])
            if len(pdf_files) > 1:
                cmd.extend(["--pdf_file2", pdf_files[1]])
        
        # Add join option if selected
        if join_option:
            cmd.extend(["--join", join_option])
        
        # Add JSON template if provided
        if json_template_path:
            cmd.extend(["--jsonout_template", json_template_path])
        
        # Set output directory
        output_file = os.path.join(output_dir, "assessment_result.html")
        cmd.extend(["--output", output_file])
        
        # Update console with command
        command_str = " ".join(cmd)
        console_output += f"Executing command:\n{command_str}\n\n"
        console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
        
        # Run the command
        status_placeholder.info("Running assessment. This may take several minutes...")
        
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(O1_ASSESSMENT_DIR)
        )
        
        # Buffer for collecting output
        output_buffer = ""
        
        # Display output in real-time
        while True:
            output = process.stdout.readline()
            if not output and process.poll() is not None:
                break
            if output:
                # Append to buffer and update the console
                output_buffer += output
                console_output += output
                console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
        
        # Check for errors
        _, stderr = process.communicate()
        if process.returncode != 0:
            error_msg = f"Error running assessment: {stderr}"
            status_placeholder.error(error_msg)
            console_output += f"\n{error_msg}\n"
            console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
            return None
        
        # Determine the actual output file path
        if os.path.isdir(output_file):
            # Find the newest file in the directory
            files = [os.path.join(output_file, f) for f in os.listdir(output_file)]
            if not files:
                error_msg = "No output file was generated"
                status_placeholder.error(error_msg)
                console_output += f"\n{error_msg}\n"
                console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
                return None
            result_file = max(files, key=os.path.getmtime)
        else:
            # If a file extension was provided, use the exact file path
            if os.path.splitext(output_file)[1]:
                result_file = output_file
            else:
                # Otherwise, append .html extension
                result_file = output_file + ".html"
        
        # Ensure the result file has .html extension
        if not result_file.lower().endswith('.html'):
            new_result_file = os.path.splitext(result_file)[0] + '.html'
            try:
                # If the file exists but doesn't have .html extension, rename it
                if os.path.exists(result_file):
                    os.rename(result_file, new_result_file)
                    result_file = new_result_file
            except Exception as e:
                console_output += f"\nCould not rename result file to have .html extension: {e}\n"
                console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
        
        # Check if the result file exists
        if not os.path.exists(result_file):
            error_msg = f"Result file not found at: {result_file}"
            status_placeholder.error(error_msg)
            console_output += f"\n{error_msg}\n"
            console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
            return None
        
        # Update status
        status_placeholder.success("Assessment completed successfully!")
        console_output += f"\nAssessment completed. Result file saved to: {result_file}\n"
        console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
        
        return result_file
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        status_placeholder.error(error_msg)
        console_output += f"\n{error_msg}\n"
        console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
        return None

def main():
    # App header with logo
    header_col1, header_col2 = st.columns([1, 2])
    
    with header_col1:
        # Try to display the image from the static directory
        image_path = os.path.join(STATIC_DIR, "Ai-Grading.jpg")
        display_image(image_path, "AWReason")
    
    with header_col2:
        st.markdown("<h1 class='main-header'>AWReason - AI Assessment Tool</h1>", unsafe_allow_html=True)
        st.markdown("""
        <div class='info-box'>
        This tool helps educators assess assignments using Azure OpenAI's advanced reasoning models.
        Upload your assessment prompt and the documents to be assessed, then click 'Run Assessment'.
        </div>
        """, unsafe_allow_html=True)
    
    # Create tabs for different sections
    tab1, tab2, tab3 = st.tabs(["Assessment Setup", "Advanced Options", "Help & Info"])
    
    with tab1:
        st.markdown("<h2 class='section-header'>1. Upload Assessment Prompt</h2>", unsafe_allow_html=True)
        st.info("This is the instructions file that tells the AI how to assess the documents.")
        
        prompt_file = st.file_uploader(
            "Upload your prompt file (.txt)", 
            type=["txt", "md"],
            help="This file contains the instructions for how the AI should assess the documents."
        )
        
        st.markdown("<h2 class='section-header'>2. Upload Documents to Assess</h2>", unsafe_allow_html=True)
        st.info("Upload one or two PDF files that you want to assess. The tool can handle up to 2 PDFs at once.")
        
        uploaded_pdfs = st.file_uploader(
            "Upload PDF files to assess (max 2)", 
            type=["pdf"], 
            accept_multiple_files=True,
            help="Upload the PDF files that you want the AI to assess. Limited to 2 files."
        )
        
        # Validate PDF count
        if uploaded_pdfs and len(uploaded_pdfs) > 2:
            st.warning("⚠️ Only the first 2 PDF files will be used due to model limitations.")
            uploaded_pdfs = uploaded_pdfs[:2]
        
        # Display the number of uploaded PDFs
        if uploaded_pdfs:
            st.success(f"✅ {len(uploaded_pdfs)} PDF{'s' if len(uploaded_pdfs) > 1 else ''} uploaded")
        
        # Setup an output directory for results
        st.markdown("<h2 class='section-header'>3. Set Output Directory</h2>", unsafe_allow_html=True)
        
        output_dir = st.text_input(
            "Output Directory", 
            value=str(O1_ASSESSMENT_DIR / "grading_results"),
            help="Directory where assessment results will be saved."
        )
        
        # Create a run button
        run_button_disabled = not (prompt_file and uploaded_pdfs)
        
        if run_button_disabled:
            st.warning("Please upload both a prompt file and at least one PDF document to continue.")
        
        run_col1, run_col2 = st.columns([3, 1])
        with run_col1:
            run_button = st.button(
                "🚀 Run Assessment", 
                disabled=run_button_disabled,
                help="Start the assessment process",
                use_container_width=True
            )
        
    with tab2:
        st.markdown("<h2 class='section-header'>Advanced Configuration</h2>", unsafe_allow_html=True)
        
        # Image joining options
        st.subheader("Image Processing Options")
        join_option = st.radio(
            "Join extracted images in pairs",
            options=[None, "horizontal", "vertical"],
            format_func=lambda x: "No joining" if x is None else f"Join {x}ly",
            help="This option allows joining consecutive PDF pages into single images."
        )
        
        # JSON template for structured output
        st.subheader("Structured Output Options")
        json_template_file = st.file_uploader(
            "Upload JSON output template (optional)", 
            type=["json"],
            help="Optional JSON template to structure the assessment output."
        )
    
    with tab3:
        st.markdown("<h2 class='section-header'>Help & Information</h2>", unsafe_allow_html=True)
        
        st.markdown("""
        ### About AWReason
        
        AWReason is a sample AI accelerator to assist educators in grading and assessing assignments.
        
        #### Key Features:
        
        * **Configurable Grading Options** using natural language prompt files
        * **PDF Processing** with image extraction and joining capabilities
        * **Structured Output** with JSON templates
        * **Multiple Document Support** for comparing submissions
        
        #### Limitations:
        
        * Due to model limits, a maximum of 50 images can be analyzed in one request
        * For PDFs with more than 50 pages, consider using the joining option
        * For very large documents, consider providing the source document in DOCX format
        
        #### Tips for Good Results:
        
        * Create detailed prompt files with clear assessment criteria
        * Include examples in your prompt to guide the AI's assessment
        * Use the JSON template option for consistent, structured output
        * For large documents, use the joining option to reduce the number of images
        """)
    
    # Process files and run assessment when the button is clicked
    if run_button:
        # Create a two-column layout for the assessment process
        process_col1, process_col2 = st.columns([3, 2])
        
        with process_col1:
            st.subheader("Assessment Progress")
            status_placeholder = st.empty()
            status_placeholder.info("Preparing files for assessment...")
        
        with process_col2:
            st.subheader("Console Output")
            console_placeholder = st.empty()
            console_placeholder.markdown('<div class="console-output">Initializing assessment process...</div>', unsafe_allow_html=True)
        
        # Buffer for collecting console output
        console_output = "Initializing assessment process...\n"
        
        # Create a temporary directory to save uploaded files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            
            # Save prompt file
            prompt_path = temp_dir_path / prompt_file.name
            with open(prompt_path, "wb") as f:
                f.write(prompt_file.getbuffer())
            
            # Update status and console
            status_placeholder.info("Saving uploaded files...")
            console_output += f"Saved prompt file: {prompt_file.name}\n"
            console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
            
            # Save PDF files
            pdf_paths = []
            for pdf in uploaded_pdfs:
                pdf_path = temp_dir_path / pdf.name
                with open(pdf_path, "wb") as f:
                    f.write(pdf.getbuffer())
                pdf_paths.append(str(pdf_path))
                console_output += f"Saved PDF file: {pdf.name}\n"
                console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
            
            # Save JSON template if provided
            json_template_path = None
            if json_template_file:
                json_template_path = temp_dir_path / json_template_file.name
                with open(json_template_path, "wb") as f:
                    f.write(json_template_file.getbuffer())
                console_output += f"Saved JSON template: {json_template_file.name}\n"
                console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
            
            # Create output directory if it doesn't exist
            os.makedirs(output_dir, exist_ok=True)
            
            # Update status
            status_placeholder.info("Starting assessment. This may take a few minutes...")
            console_output += "Starting assessment process...\n"
            console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)
            
            # Run the assessment
            result_file = run_assessment(
                str(prompt_path), 
                pdf_paths, 
                join_option, 
                str(json_template_path) if json_template_path else None,
                output_dir,
                status_placeholder,
                console_placeholder,
                console_output
            )
            
            # Display results
            if result_file:
                # Clear the columns to make room for the results
                process_col1.empty()
                process_col2.empty()
                
                # Create a full-width result panel
                st.markdown('<div class="result-panel">', unsafe_allow_html=True)
                st.markdown("<h2>Assessment Results</h2>", unsafe_allow_html=True)
                
                # Display the content of the result file
                st.subheader("Result Preview:")
                display_file_content(result_file)
                
                # Provide a download link
                st.subheader("Download Result:")
                st.markdown(
                    get_binary_file_downloader_html(result_file, f"Download {os.path.basename(result_file)}"),
                    unsafe_allow_html=True
                )
                
                # Show file location
                st.info(f"Result saved to: {result_file}")
                st.markdown('</div>', unsafe_allow_html=True)
    
    # Footer
    st.markdown("""
    <div class='footer'>
        AWReason - An AI accelerator to assist educators in assessment. Powered by OpenAI's multimodal models.
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
