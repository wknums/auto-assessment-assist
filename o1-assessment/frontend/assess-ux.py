import streamlit as st
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import base64
import json
from datetime import datetime

# Import Azure OpenAI client utilities
try:
    from azure_openai_client import (
        initialize_azure_openai_client,
        send_chat_completion,
        truncate_context_to_fit
    )
    AZURE_OPENAI_AVAILABLE = True
except ImportError as e:
    AZURE_OPENAI_AVAILABLE = False
    print(f"Warning: Azure OpenAI client not available: {e}")

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
    .chat-message {
        padding: 1rem;
        margin: 0.5rem 0;
        border-radius: 0.5rem;
    }
    .user-message {
        background-color: #2b5278;
        margin-left: 2rem;
    }
    .assistant-message {
        background-color: #1e1e1e;
        margin-right: 2rem;
    }
    .chat-container {
        max-height: 500px;
        overflow-y: auto;
        padding: 1rem;
        border: 1px solid #444;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
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
    .stTextArea textarea {
        font-family: 'Courier New', monospace;
        font-size: 0.9rem;
        resize: both !important;
    }
    .stTextArea label {
        font-weight: 600;
        color: #e0e0e0;
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
                  status_placeholder, console_placeholder, console_output, md_file_path=None, image_folder=None):
    """Run the assessment using awreason.py script.

    Added support for optional markdown context file (passed via --md_file to awreason.py).
    Added support for optional image folder (passed via --images_folder1 to awreason.py).
    """
    
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
        
        # Add image folder if provided (when no PDFs or as supplement)
        if image_folder:
            if len(pdf_files) == 0:
                cmd.extend(["--images_folder1", image_folder])
            elif len(pdf_files) == 1:
                cmd.extend(["--images_folder2", image_folder])

        # Add markdown file if provided
        if md_file_path:
            cmd.extend(["--md_file", md_file_path])
        
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

def initialize_chat_session():
    """Initialize chat session state variables"""
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'chat_memory_limit' not in st.session_state:
        st.session_state.chat_memory_limit = 15
    if 'chat_base_context' not in st.session_state:
        st.session_state.chat_base_context = {
            'prompt_content': None,
            'pdf_files': [],
            'image_files': [],
            'context_file_content': None,
            'assessment_result': None
        }
    if 'azure_openai_client' not in st.session_state:
        # Initialize Azure OpenAI client once per session
        if AZURE_OPENAI_AVAILABLE:
            try:
                st.session_state.azure_openai_client = initialize_azure_openai_client()
                st.session_state.client_error = None
            except Exception as e:
                st.session_state.azure_openai_client = None
                st.session_state.client_error = str(e)
        else:
            st.session_state.azure_openai_client = None
            st.session_state.client_error = "Azure OpenAI client module not available"

def get_chat_context():
    """Get the current chat context based on memory limit"""
    if not st.session_state.chat_history:
        return []
    
    # Filter only user messages for counting
    user_messages = [msg for msg in st.session_state.chat_history if msg['role'] == 'user']
    
    # Get the last N user messages based on memory limit
    if len(user_messages) > st.session_state.chat_memory_limit:
        # Find the index of the (total - limit)th user message
        cutoff_count = len(user_messages) - st.session_state.chat_memory_limit
        user_count = 0
        cutoff_index = 0
        
        for i, msg in enumerate(st.session_state.chat_history):
            if msg['role'] == 'user':
                user_count += 1
                if user_count == cutoff_count:
                    cutoff_index = i + 1
                    break
        
        return st.session_state.chat_history[cutoff_index:]
    
    return st.session_state.chat_history

def build_base_context_message():
    """Build the base context message from uploaded files and assessment results"""
    context_parts = []
    
    base_context = st.session_state.chat_base_context
    
    if base_context['prompt_content']:
        context_parts.append("=== Assessment Prompt ===")
        context_parts.append(base_context['prompt_content'])
    
    if base_context['context_file_content']:
        context_parts.append("\n=== Additional Context Document ===")
        context_parts.append(base_context['context_file_content'])
    
    if base_context['pdf_files']:
        context_parts.append(f"\n=== PDF Documents ===")
        context_parts.append(f"Number of PDF files available: {len(base_context['pdf_files'])}")
        for i, pdf_name in enumerate(base_context['pdf_files'], 1):
            context_parts.append(f"{i}. {pdf_name}")
    
    if base_context['image_files']:
        context_parts.append(f"\n=== Image Files ===")
        context_parts.append(f"Number of image files available: {len(base_context['image_files'])}")
        for i, img_name in enumerate(base_context['image_files'], 1):
            context_parts.append(f"{i}. {img_name}")
    
    if base_context['assessment_result']:
        context_parts.append("\n=== Assessment Result ===")
        context_parts.append("The following is the assessment result from analyzing the uploaded documents:")
        context_parts.append(base_context['assessment_result'])
    
    if context_parts:
        return "\n".join(context_parts)
    return None

def send_chat_message(user_input, system_prompt=None):
    """Send a message to the chat and get a response"""
    # Adapt system prompt based on whether assessment results are available
    if system_prompt is None:
        if st.session_state.chat_base_context.get('assessment_result'):
            system_prompt = ("You are a helpful AI assistant for educational assessment. You have access to "
                           "the assessment prompt, uploaded documents, and the completed assessment results. "
                           "Help educators understand the assessment findings, clarify grades or feedback, "
                           "identify patterns, and provide insights about student work. Reference specific "
                           "parts of the assessment when relevant.")
        else:
            system_prompt = ("You are a helpful AI assistant for educational assessment. You help educators "
                           "understand assessment criteria, analyze student work, and provide guidance on "
                           "grading practices. Help them prepare their assessment setup and prompts.")
    
    """Send a message to the chat and get a response"""
    """Send a message to the chat and get a response"""
    # Add user message to history
    st.session_state.chat_history.append({
        'role': 'user',
        'content': user_input,
        'timestamp': datetime.now().isoformat()
    })
    
    # Check if client is available
    if not st.session_state.azure_openai_client:
        error_msg = f"Unable to connect to Azure OpenAI: {st.session_state.client_error}"
        st.session_state.chat_history.append({
            'role': 'assistant',
            'content': f"❌ Error: {error_msg}",
            'timestamp': datetime.now().isoformat(),
            'error': True
        })
        return error_msg
    
    try:
        # Get context based on memory limit
        context_messages = get_chat_context()
        
        # Build messages for the API call
        messages = [{'role': 'system', 'content': system_prompt}]
        
        # Add base context from uploaded files as a system message
        base_context = build_base_context_message()
        if base_context:
            messages.append({
                'role': 'system',
                'content': f"You have access to the following uploaded content for context:\n\n{base_context}"
            })
        
        # Add conversation history
        messages.extend([{'role': msg['role'], 'content': msg['content']} for msg in context_messages])
        
        # Truncate if necessary to avoid token limits
        messages = truncate_context_to_fit(messages, max_tokens=100000)
        
        # Call Azure OpenAI API
        assistant_response, usage_info = send_chat_completion(
            st.session_state.azure_openai_client,
            messages,
            max_tokens=4000
        )
        
        # Add assistant response to history with token usage
        st.session_state.chat_history.append({
            'role': 'assistant',
            'content': assistant_response,
            'timestamp': datetime.now().isoformat(),
            'usage': usage_info
        })
        
        return assistant_response
        
    except Exception as e:
        error_msg = f"Error calling Azure OpenAI: {str(e)}"
        st.session_state.chat_history.append({
            'role': 'assistant',
            'content': f"❌ Error: {error_msg}",
            'timestamp': datetime.now().isoformat(),
            'error': True
        })
        return error_msg

def display_chat_history():
    """Display the chat history in a formatted way"""
    chat_container = st.container()
    
    with chat_container:
        for msg in st.session_state.chat_history:
            if msg['role'] == 'user':
                st.markdown(
                    f"""<div class="chat-message user-message">
                    <strong>You:</strong> {msg['content']}
                    </div>""",
                    unsafe_allow_html=True
                )
            else:
                # Check if this is an error message
                is_error = msg.get('error', False)
                content = msg['content']
                
                st.markdown(
                    f"""<div class="chat-message assistant-message">
                    <strong>Assistant:</strong> {content}
                    </div>""",
                    unsafe_allow_html=True
                )
                
                # Display token usage if available
                if 'usage' in msg and not is_error:
                    usage = msg['usage']
                    st.caption(
                        f"📊 Tokens: {usage['prompt_tokens']:,} prompt + "
                        f"{usage['completion_tokens']:,} completion = "
                        f"{usage['total_tokens']:,} total"
                    )

def main():
    # Initialize chat session
    initialize_chat_session()
    
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
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Assessment Setup", "Advanced Options", "Batch Processing (Beta) ⚠️", "Chat Assistant", "Help & Info"])
    
    with tab1:
        st.markdown("<h2 class='section-header'>1. Upload Assessment Prompt</h2>", unsafe_allow_html=True)
        st.info("This is the instructions file that tells the AI how to assess the documents.")
        
        prompt_file = st.file_uploader(
            "Upload your prompt file (.txt)", 
            type=["txt", "md"],
            help="This file contains the instructions for how the AI should assess the documents."
        )
        
        # Display and allow editing of prompt content
        if prompt_file:
            try:
                # Read the file content
                prompt_content = prompt_file.getvalue().decode('utf-8')
                
                # Store original content if not already stored
                if 'original_prompt_content' not in st.session_state or st.session_state.get('last_prompt_file') != prompt_file.name:
                    st.session_state.original_prompt_content = prompt_content
                    st.session_state.last_prompt_file = prompt_file.name
                
                # Create an editable text area with the prompt content
                st.markdown("#### Review and Edit Prompt")
                
                with st.expander("💡 Editing Tips", expanded=False):
                    st.markdown("""
                    - **Refine instructions**: Clarify assessment criteria or add examples
                    - **Add context**: Include specific rubric details or grading notes
                    - **Adjust tone**: Make instructions more formal or conversational
                    - **Test variations**: Try different prompts without re-uploading
                    - **Resize**: Drag the bottom-right corner to resize the text area
                    """)
                
                edited_prompt = st.text_area(
                    "Prompt Content (editable)",
                    value=st.session_state.original_prompt_content,
                    height=300,
                    help="You can edit the prompt content here before running the assessment. The text area is resizable by dragging the bottom-right corner.",
                    key="prompt_editor"
                )
                
                # Show character count
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    if edited_prompt != st.session_state.original_prompt_content:
                        st.caption("✏️ Prompt has been modified")
                with col2:
                    st.caption(f"📊 {len(edited_prompt)} characters")
                with col3:
                    if st.button("↺ Reset to Original", key="reset_prompt"):
                        st.session_state.original_prompt_content = prompt_content
                        st.rerun()
                
                # Store edited content for use in assessment and chat
                st.session_state.edited_prompt_content = edited_prompt
                
            except Exception as e:
                st.error(f"Error reading prompt file: {e}")
                st.session_state.edited_prompt_content = None
        else:
            st.session_state.edited_prompt_content = None
        
        st.markdown("<h2 class='section-header'>2. Upload Documents to Assess</h2>", unsafe_allow_html=True)
        st.info("Upload PDF files, images (PNG/JPG/JPEG), and optionally a Markdown (.md) or Word (.docx) file for additional text context. The backend supports up to 2 PDFs (or image sets) plus 1 context file (MD/DOCX). DOCX will be converted to Markdown internally.")

        uploaded_docs = st.file_uploader(
            "Upload PDF, images, and/or context files",
            type=["pdf", "png", "jpg", "jpeg", "md", "docx"],
            accept_multiple_files=True,
            help="Select up to 2 PDFs/image sets plus optionally ONE context file (.md or .docx). Images are treated as visual content. DOCX files are auto-converted to Markdown before being appended to the prompt." 
        )

        uploaded_pdfs = []
        uploaded_images = []
        uploaded_context = None  # can be .md or .docx
        ignored_files = []
        if uploaded_docs:
            for f in uploaded_docs:
                name_lower = f.name.lower()
                if name_lower.endswith('.pdf'):
                    if len(uploaded_pdfs) < 2:
                        uploaded_pdfs.append(f)
                    else:
                        ignored_files.append(f.name)
                elif name_lower.endswith(('.png', '.jpg', '.jpeg')):
                    uploaded_images.append(f)
                elif name_lower.endswith('.md') or name_lower.endswith('.docx'):
                    if uploaded_context is None:
                        uploaded_context = f
                    else:
                        ignored_files.append(f.name)
                else:  # should not happen due to type filter
                    ignored_files.append(f.name)

        if uploaded_pdfs:
            st.success(f"✅ {len(uploaded_pdfs)} PDF{'s' if len(uploaded_pdfs) > 1 else ''} uploaded")
        if uploaded_images:
            st.success(f"✅ {len(uploaded_images)} image file{'s' if len(uploaded_images) > 1 else ''} uploaded")
        if uploaded_context:
            st.success(f"✅ Context file: {uploaded_context.name}")
        if ignored_files:
            st.warning("Ignored extra/unsupported files: " + ", ".join(ignored_files))
        
        # Update chat base context with uploaded files (use edited content if available)
        if prompt_file:
            try:
                # Use edited content if available, otherwise use original
                st.session_state.chat_base_context['prompt_content'] = st.session_state.get('edited_prompt_content') or prompt_file.getvalue().decode('utf-8')
            except:
                st.session_state.chat_base_context['prompt_content'] = None
        
        if uploaded_pdfs:
            st.session_state.chat_base_context['pdf_files'] = [pdf.name for pdf in uploaded_pdfs]
        else:
            st.session_state.chat_base_context['pdf_files'] = []
        
        if uploaded_images:
            st.session_state.chat_base_context['image_files'] = [img.name for img in uploaded_images]
        else:
            st.session_state.chat_base_context['image_files'] = []
        
        if uploaded_context:
            try:
                content = uploaded_context.getvalue().decode('utf-8')
                st.session_state.chat_base_context['context_file_content'] = content
            except:
                st.session_state.chat_base_context['context_file_content'] = None
        else:
            st.session_state.chat_base_context['context_file_content'] = None
        
        # Setup an output directory for results
        st.markdown("<h2 class='section-header'>3. Set Output Directory</h2>", unsafe_allow_html=True)
        
        output_dir = st.text_input(
            "Output Directory", 
            value=str(O1_ASSESSMENT_DIR / "grading_results"),
            help="Directory where assessment results will be saved."
        )
        
        # Create a run button
        run_button_disabled = not (prompt_file and (uploaded_pdfs or uploaded_images or uploaded_context))

        if run_button_disabled:
            st.warning("Please upload a prompt file and at least one document (PDF, images, Markdown, or DOCX) to continue.")

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
        st.markdown("<h2 class='section-header'>Batch Document Processing (Beta)</h2>", unsafe_allow_html=True)
        st.warning("⚠️ **Beta Feature**: This batch processing capability has not been fully tested. Please verify results carefully.")
        st.info("Upload multiple .docx or .pdf files and process them all at once using a common prompt and optional JSON template.")
        
        # Batch prompt file
        st.markdown("### 1. Upload Assessment Prompt")
        batch_prompt_file = st.file_uploader(
            "Upload prompt file for batch processing (.txt, .md)", 
            type=["txt", "md"],
            help="This prompt will be used for all documents in the batch.",
            key="batch_prompt"
        )
        
        # Display and allow editing of batch prompt
        if batch_prompt_file:
            try:
                batch_prompt_content = batch_prompt_file.getvalue().decode('utf-8')
                
                if 'batch_original_prompt' not in st.session_state or st.session_state.get('last_batch_prompt_file') != batch_prompt_file.name:
                    st.session_state.batch_original_prompt = batch_prompt_content
                    st.session_state.last_batch_prompt_file = batch_prompt_file.name
                
                st.markdown("#### Review and Edit Batch Prompt")
                batch_edited_prompt = st.text_area(
                    "Batch Prompt Content (editable)",
                    value=st.session_state.batch_original_prompt,
                    height=250,
                    key="batch_prompt_editor"
                )
                
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    if batch_edited_prompt != st.session_state.batch_original_prompt:
                        st.caption("✏️ Prompt has been modified")
                with col2:
                    st.caption(f"📊 {len(batch_edited_prompt)} characters")
                with col3:
                    if st.button("↺ Reset", key="reset_batch_prompt"):
                        st.session_state.batch_original_prompt = batch_prompt_content
                        st.rerun()
                
                st.session_state.batch_edited_prompt = batch_edited_prompt
                
            except Exception as e:
                st.error(f"Error reading batch prompt file: {e}")
                st.session_state.batch_edited_prompt = None
        else:
            st.session_state.batch_edited_prompt = None
        
        # JSON template (optional)
        st.markdown("### 2. Upload JSON Template (Optional)")
        batch_json_template = st.file_uploader(
            "Upload common JSON template (.json)", 
            type=["json"],
            help="Optional: This template will be used for all documents in the batch. If not provided, output will be in HTML format.",
            key="batch_json_template"
        )
        
        # Upload multiple documents
        st.markdown("### 3. Upload Documents (.docx or .pdf)")
        batch_doc_files = st.file_uploader(
            "Upload .docx or .pdf files for batch processing",
            type=["docx", "pdf"],
            accept_multiple_files=True,
            help="Select multiple .docx or .pdf files to process in batch",
            key="batch_doc_files"
        )
        
        if batch_doc_files:
            docx_count = sum(1 for f in batch_doc_files if f.name.lower().endswith('.docx'))
            pdf_count = sum(1 for f in batch_doc_files if f.name.lower().endswith('.pdf'))
            st.success(f"✅ {len(batch_doc_files)} file(s) uploaded ({docx_count} .docx, {pdf_count} .pdf)")
            with st.expander("Uploaded Files", expanded=False):
                for idx, f in enumerate(batch_doc_files, 1):
                    file_type = "📄 DOCX" if f.name.lower().endswith('.docx') else "📑 PDF"
                    st.write(f"{idx}. {file_type} - {f.name}")
        
        # Output directory
        st.markdown("### 4. Set Output Directory")
        batch_output_dir = st.text_input(
            "Batch Output Directory", 
            value=str(O1_ASSESSMENT_DIR / "batch_results"),
            help="Directory where all batch results will be saved.",
            key="batch_output_dir"
        )
        
        # Run batch processing button
        batch_run_disabled = not (batch_prompt_file and batch_doc_files)
        
        if batch_run_disabled:
            st.warning("Please upload a prompt file and at least one document file (.docx or .pdf) to start batch processing.")
        
        st.markdown("### 5. Run Batch Processing")
        batch_col1, batch_col2 = st.columns([3, 1])
        with batch_col1:
            run_batch_button = st.button(
                "🚀 Run Batch Processing",
                disabled=batch_run_disabled,
                help="Process all uploaded .docx files",
                use_container_width=True,
                key="run_batch_button"
            )
        with batch_col2:
            if 'batch_results' in st.session_state and st.session_state.batch_results:
                if st.button("📊 View Results", use_container_width=True, key="view_batch_results"):
                    st.session_state.show_batch_results = True
        
        # Process batch if button clicked
        if run_batch_button:
            st.markdown("---")
            st.subheader("Batch Processing Progress")
            
            # Create progress indicators
            progress_bar = st.progress(0)
            status_text = st.empty()
            console_output_batch = st.empty()
            
            console_log = "Starting batch processing...\n"
            console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
            
            # Create temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir_path = Path(temp_dir)
                
                # Save prompt file
                batch_prompt_path = temp_dir_path / batch_prompt_file.name
                prompt_content_to_save = st.session_state.get('batch_edited_prompt', None)
                if prompt_content_to_save:
                    with open(batch_prompt_path, "w", encoding='utf-8') as f:
                        f.write(prompt_content_to_save)
                else:
                    with open(batch_prompt_path, "wb") as f:
                        f.write(batch_prompt_file.getbuffer())
                console_log += f"Saved prompt file: {batch_prompt_file.name}\n"
                console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                
                # Save JSON template if provided
                batch_json_path = None
                if batch_json_template:
                    batch_json_path = temp_dir_path / batch_json_template.name
                    with open(batch_json_path, "wb") as f:
                        f.write(batch_json_template.getbuffer())
                    console_log += f"Saved JSON template: {batch_json_template.name}\n"
                    console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                else:
                    console_log += "No JSON template provided - output will be in HTML format\n"
                    console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                
                # Create output directory
                os.makedirs(batch_output_dir, exist_ok=True)
                
                # Process each document file
                batch_results = []
                total_files = len(batch_doc_files)
                
                for idx, doc_file in enumerate(batch_doc_files, 1):
                    file_base_name = Path(doc_file.name).stem
                    file_ext = Path(doc_file.name).suffix.lower()
                    is_pdf = file_ext == '.pdf'
                    
                    progress = idx / total_files
                    progress_bar.progress(progress)
                    status_text.info(f"Processing {idx}/{total_files}: {doc_file.name}")
                    
                    console_log += f"\n{'='*60}\n"
                    console_log += f"Processing file {idx}/{total_files}: {doc_file.name} ({'PDF' if is_pdf else 'DOCX'})\n"
                    console_log += f"{'='*60}\n"
                    console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                    
                    try:
                        # Save document file
                        doc_path = temp_dir_path / doc_file.name
                        with open(doc_path, "wb") as f:
                            f.write(doc_file.getbuffer())
                        
                        md_path = None
                        pdf_path = None
                        
                        # Step 1: Convert DOCX to markdown (skip for PDF)
                        if not is_pdf:
                            md_file_name = f"{doc_file.name}.md"
                            md_path = Path(batch_output_dir) / md_file_name
                            
                            console_log += f"Step 1: Converting to markdown...\n"
                            console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                            
                            docx2md_path = REPO_ROOT / "docx2md.py"
                            cmd_convert = [
                                sys.executable, str(docx2md_path),
                                "--docx_file", str(doc_path),
                                "--md_file", str(md_path),
                                "--mode", "convert"
                            ]
                            
                            result = subprocess.run(cmd_convert, capture_output=True, text=True, cwd=str(O1_ASSESSMENT_DIR))
                            console_log += result.stdout
                            if result.stderr:
                                console_log += result.stderr
                            console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                            
                            if result.returncode != 0:
                                error_msg = f"ERROR: Failed to convert {doc_file.name} to markdown\n"
                                console_log += error_msg
                                console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                                batch_results.append({
                                    'file': doc_file.name,
                                    'status': 'failed',
                                    'error': 'Markdown conversion failed',
                                    'output': None
                                })
                                continue
                        else:
                            console_log += f"Step 1: Using PDF directly (no conversion needed)...\n"
                            console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                            pdf_path = doc_path
                        
                        # Step 2: Run analysis
                        output_ext = ".json" if batch_json_path else ".html"
                        output_file_name = f"{file_base_name}-analysis{output_ext}"
                        output_file_path = Path(batch_output_dir) / output_file_name
                        
                        console_log += f"Step 2: Running analysis...\n"
                        console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                        
                        awreason_path = REPO_ROOT / "awreason.py"
                        cmd_analyze = [
                            sys.executable, str(awreason_path),
                            "--promptfile", str(batch_prompt_path)
                        ]
                        
                        # Add PDF or markdown file
                        if is_pdf:
                            cmd_analyze.extend(["--pdf_file1", str(pdf_path)])
                        else:
                            cmd_analyze.extend(["--md_file", str(md_path)])
                        
                        # Add JSON template if provided
                        if batch_json_path:
                            cmd_analyze.extend(["--jsonout_template", str(batch_json_path)])
                        
                        # Add output file
                        cmd_analyze.extend(["--output", str(output_file_path)])
                        
                        result = subprocess.run(cmd_analyze, capture_output=True, text=True, cwd=str(O1_ASSESSMENT_DIR))
                        console_log += result.stdout
                        if result.stderr:
                            console_log += result.stderr
                        console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                        
                        if result.returncode != 0:
                            error_msg = f"ERROR: Failed to analyze {doc_file.name}\n"
                            console_log += error_msg
                            console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                            batch_results.append({
                                'file': doc_file.name,
                                'status': 'failed',
                                'error': 'Analysis failed',
                                'output': None
                            })
                            continue
                        
                        # Success
                        console_log += f"✓ COMPLETED: {doc_file.name} -> {output_file_name}\n"
                        console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                        
                        result_data = {
                            'file': doc_file.name,
                            'status': 'success',
                            'output': str(output_file_path)
                        }
                        if md_path:
                            result_data['markdown'] = str(md_path)
                        batch_results.append(result_data)
                        
                    except Exception as e:
                        error_msg = f"ERROR: Exception processing {doc_file.name}: {str(e)}\n"
                        console_log += error_msg
                        console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                        batch_results.append({
                            'file': doc_file.name,
                            'status': 'failed',
                            'error': str(e),
                            'output': None
                        })
                
                # Complete
                progress_bar.progress(1.0)
                status_text.success(f"✅ Batch processing complete! Processed {total_files} file(s).")
                console_log += f"\n{'='*60}\n"
                console_log += f"Batch processing complete!\n"
                console_log += f"{'='*60}\n"
                console_output_batch.markdown(f'<div class="console-output">{console_log}</div>', unsafe_allow_html=True)
                
                # Store results in session state
                st.session_state.batch_results = batch_results
                st.session_state.show_batch_results = True
        
        # Display batch results
        if st.session_state.get('show_batch_results') and st.session_state.get('batch_results'):
            st.markdown("---")
            st.markdown("### Batch Results Summary")
            
            results = st.session_state.batch_results
            success_count = sum(1 for r in results if r['status'] == 'success')
            failed_count = len(results) - success_count
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Files", len(results))
            with col2:
                st.metric("Successful", success_count)
            with col3:
                st.metric("Failed", failed_count)
            
            # Show detailed results
            st.markdown("#### Detailed Results")
            for result in results:
                with st.expander(f"{'✅' if result['status'] == 'success' else '❌'} {result['file']}", expanded=False):
                    if result['status'] == 'success':
                        st.success("Processing completed successfully")
                        output_type = "JSON" if result['output'].endswith('.json') else "HTML"
                        st.write(f"**Output {output_type}:** {result['output']}")
                        if 'markdown' in result:
                            st.write(f"**Markdown:** {result['markdown']}")
                        
                        # Preview output
                        if os.path.exists(result['output']):
                            with st.container():
                                st.markdown("**Preview:**")
                                display_file_content(result['output'])
                            
                            # Download link
                            st.markdown(
                                get_binary_file_downloader_html(result['output'], f"Download {os.path.basename(result['output'])}"),
                                unsafe_allow_html=True
                            )
                    else:
                        st.error(f"Processing failed: {result.get('error', 'Unknown error')}")
    
    with tab4:
        st.markdown("<h2 class='section-header'>Chat Assistant</h2>", unsafe_allow_html=True)
        
        # Show Azure OpenAI connection status
        if st.session_state.azure_openai_client:
            st.success("✅ Connected to Azure OpenAI")
        else:
            st.error(f"❌ Azure OpenAI not available: {st.session_state.client_error}")
            st.info("💡 Make sure your .env file is configured with AZURE_OPENAI_ENDPOINT and you're authenticated with Azure CLI (`az login`)")
        
        # Chat configuration
        with st.expander("⚙️ Chat Configuration", expanded=False):
            new_memory_limit = st.number_input(
                "Short-term memory (number of user prompts to retain)",
                min_value=1,
                max_value=100,
                value=st.session_state.chat_memory_limit,
                help="Controls how many of your previous messages are included in the conversation context"
            )
            if new_memory_limit != st.session_state.chat_memory_limit:
                st.session_state.chat_memory_limit = new_memory_limit
                st.success(f"Memory limit updated to {new_memory_limit} prompts")
            
            st.info(f"Current context: {len([m for m in st.session_state.chat_history if m['role'] == 'user'])} user messages in history")
            
            # Show what context is loaded
            base_ctx = st.session_state.chat_base_context
            if base_ctx['prompt_content'] or base_ctx['pdf_files'] or base_ctx['image_files'] or base_ctx['context_file_content'] or base_ctx['assessment_result']:
                st.success("✅ Base context loaded from Assessment Setup tab")
                if base_ctx['prompt_content']:
                    st.write(f"📄 Prompt file loaded ({len(base_ctx['prompt_content'])} characters)")
                if base_ctx['pdf_files']:
                    st.write(f"📑 {len(base_ctx['pdf_files'])} PDF file(s): {', '.join(base_ctx['pdf_files'])}")
                if base_ctx['image_files']:
                    st.write(f"🖼️ {len(base_ctx['image_files'])} image file(s): {', '.join(base_ctx['image_files'])}")
                if base_ctx['context_file_content']:
                    st.write(f"📝 Context document loaded ({len(base_ctx['context_file_content'])} characters)")
                if base_ctx['assessment_result']:
                    st.write(f"✅ Assessment result available ({len(base_ctx['assessment_result'])} characters)")
            else:
                st.warning("⚠️ No base context loaded. Upload files in the Assessment Setup tab to provide context for the chat.")
            
            clear_col1, clear_col2 = st.columns(2)
            with clear_col1:
                if st.button("🗑️ Clear Chat History", use_container_width=True):
                    st.session_state.chat_history = []
                    st.rerun()
            with clear_col2:
                if st.button("🔄 Clear Assessment Result", use_container_width=True, 
                           disabled=not st.session_state.chat_base_context.get('assessment_result')):
                    st.session_state.chat_base_context['assessment_result'] = None
                    st.success("Assessment result cleared from chat context")
                    st.rerun()
        
        # Chat interface
        st.markdown("### Chat with AI Assistant")
        if st.session_state.chat_base_context.get('assessment_result'):
            st.info("Ask questions about the assessment results, grading criteria, or get clarification on the analysis. The assistant has access to your prompt, documents, and the completed assessment.")
        else:
            st.info("Ask questions about assessment, the uploaded documents, or get help with grading criteria. The assistant has access to your uploaded prompt and context files. Run an assessment first to discuss the results.")
        
        # Display chat history
        if st.session_state.chat_history:
            st.markdown('<div class="chat-container">', unsafe_allow_html=True)
            display_chat_history()
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="chat-container"><p style="color: #888;">No messages yet. Start a conversation below!</p></div>', unsafe_allow_html=True)
        
        # Chat input
        chat_col1, chat_col2 = st.columns([5, 1])
        with chat_col1:
            user_message = st.text_input(
                "Your message:",
                key="chat_input",
                placeholder="Ask about the assessment, documents, or grading criteria...",
                label_visibility="collapsed"
            )
        with chat_col2:
            send_button = st.button("Send 📨", use_container_width=True)
        
        # Handle message sending
        if send_button and user_message:
            with st.spinner("Thinking..."):
                send_chat_message(user_message)
            st.rerun()
        
        # Quick action buttons
        st.markdown("### Quick Actions")
        
        # Show different actions based on whether assessment has been run
        if st.session_state.chat_base_context.get('assessment_result'):
            quick_col1, quick_col2, quick_col3, quick_col4 = st.columns(4)
            
            with quick_col1:
                if st.button("📊 Summarize Results", use_container_width=True):
                    send_chat_message("Please provide a concise summary of the assessment results.")
                    st.rerun()
            
            with quick_col2:
                if st.button("🎯 Key Findings", use_container_width=True):
                    send_chat_message("What are the key findings and main points from this assessment?")
                    st.rerun()
            
            with quick_col3:
                if st.button("⚠️ Areas of Concern", use_container_width=True):
                    send_chat_message("Identify any areas of concern or issues highlighted in the assessment.")
                    st.rerun()
            
            with quick_col4:
                if st.button("✨ Strengths", use_container_width=True):
                    send_chat_message("What are the main strengths identified in this assessment?")
                    st.rerun()
        else:
            quick_col1, quick_col2, quick_col3 = st.columns(3)
            
            with quick_col1:
                if st.button("💡 Summarize Prompt", use_container_width=True):
                    if st.session_state.chat_base_context['prompt_content']:
                        send_chat_message("Please summarize the assessment prompt that was uploaded.")
                        st.rerun()
                    else:
                        st.warning("No prompt file uploaded yet")
            
            with quick_col2:
                if st.button("📋 Extract Criteria", use_container_width=True):
                    if st.session_state.chat_base_context['prompt_content']:
                        send_chat_message("Extract and list the key grading criteria from the assessment prompt.")
                        st.rerun()
                    else:
                        st.warning("No prompt file uploaded yet")
            
            with quick_col3:
                if st.button("❓ Help with Setup", use_container_width=True):
                    send_chat_message("What information do I need to provide to run an assessment?")
                    st.rerun()
    
    with tab5:
        st.markdown("<h2 class='section-header'>Help & Information</h2>", unsafe_allow_html=True)
        
        st.markdown("""
        ### About AWReason
        
        AWReason is a sample AI accelerator to assist educators in grading and assessing assignments.
        
        #### Key Features:
        
        * **Configurable Grading Options** using natural language prompt files
        * **Live Prompt Editing** - Review and modify prompts before assessment
        * **PDF Processing** with image extraction and joining capabilities
        * **Direct Image Upload** supporting PNG, JPG, and JPEG formats
        * **Structured Output** with JSON templates
        * **Multiple Document Support** for comparing submissions
        
        #### Limitations:
        
        * Due to model limits, a maximum of 50 images can be analyzed in one request
        * For PDFs with more than 50 pages, consider using the joining option
        * For very large documents, consider providing the source document in DOCX format
        
        #### Tips for Good Results:
        
        * Create detailed prompt files with clear assessment criteria
        * **Review and edit your prompt** in the UI before running the assessment
        * Include examples in your prompt to guide the AI's assessment
        * Use the JSON template option for consistent, structured output
        * For large documents, use the joining option to reduce the number of images
        * You can upload images directly (PNG, JPG, JPEG) instead of PDFs for faster processing
        * Combine document types: use images for visual content and DOCX/MD files for text context
        * The prompt editor is resizable - drag the corner to expand the view
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
            
            # Save prompt file (use edited content if available)
            prompt_path = temp_dir_path / prompt_file.name
            prompt_content_to_save = st.session_state.get('edited_prompt_content', None)
            if prompt_content_to_save:
                with open(prompt_path, "w", encoding='utf-8') as f:
                    f.write(prompt_content_to_save)
                console_output += f"Saved edited prompt file: {prompt_file.name}\n"
            else:
                with open(prompt_path, "wb") as f:
                    f.write(prompt_file.getbuffer())
                console_output += f"Saved prompt file: {prompt_file.name}\n"
            
            # Update status and console
            status_placeholder.info("Saving uploaded files...")
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
            
            # Save image files to a dedicated folder
            image_paths = []
            if 'uploaded_images' in locals() and uploaded_images:
                images_dir = temp_dir_path / "uploaded_images"
                images_dir.mkdir(exist_ok=True)
                for img in uploaded_images:
                    img_path = images_dir / img.name
                    with open(img_path, "wb") as f:
                        f.write(img.getbuffer())
                    image_paths.append(str(img_path))
                    console_output += f"Saved image file: {img.name}\n"
                    console_placeholder.markdown(f'<div class="console-output">{console_output}</div>', unsafe_allow_html=True)

            # Save Markdown file if provided
            md_path = None
            if 'uploaded_context' in locals() and uploaded_context:
                md_path = temp_dir_path / uploaded_context.name
                with open(md_path, "wb") as f:
                    f.write(uploaded_context.getbuffer())
                console_output += f"Saved context file: {uploaded_context.name}\n"
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
                console_output,
                md_file_path=str(md_path) if md_path else None,
                image_folder=str(images_dir) if image_paths else None
            )
            
            # Display results
            if result_file:
                # Read and store the result content for chat context
                try:
                    with open(result_file, 'r', encoding='utf-8') as f:
                        result_content = f.read()
                        st.session_state.chat_base_context['assessment_result'] = result_content
                except Exception as e:
                    console_output += f"\nNote: Could not read result file for chat context: {e}\n"
                    st.session_state.chat_base_context['assessment_result'] = f"Assessment completed. Result saved to: {result_file}"
                
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
                
                # Encourage using chat to discuss results
                st.success("💬 **Tip:** Go to the 'Chat Assistant' tab to discuss these results, ask questions, or get clarification on the assessment!")
                
                st.markdown('</div>', unsafe_allow_html=True)
    
    # Footer
    st.markdown("""
    <div class='footer'>
        AWReason - An AI accelerator to assist educators in assessment. Powered by OpenAI's multimodal models.
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
