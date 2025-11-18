#!/usr/bin/env python
"""
Launch script for the AWReason Streamlit frontend.
This script provides a convenient way to start the Streamlit application.
"""

import os
import sys
import subprocess
from pathlib import Path

# Determine the path to the frontend directory
SCRIPT_DIR = Path(__file__).parent
FRONTEND_DIR = SCRIPT_DIR / "frontend"
APP_SCRIPT = FRONTEND_DIR / "assess-ux.py"

def main():
    """Launch the Streamlit application."""
    
    # Check if Streamlit is installed
    try:
        import streamlit
        print("Streamlit is installed. Starting application...")
    except ImportError:
        print("Streamlit is not installed. Installing Streamlit...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "streamlit"])
        print("Streamlit has been installed.")
    
    # Start the Streamlit application
    print(f"Launching Streamlit app from: {APP_SCRIPT}")
    subprocess.call([sys.executable, "-m", "streamlit", "run", str(APP_SCRIPT)])

if __name__ == "__main__":
    main()
