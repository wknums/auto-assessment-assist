#!/bin/bash
# Bash script to start AWReason Frontend
# Run this script from the root folder of the project

echo -e "\033[0;32mStarting AWReason Frontend...\033[0m"

# Get the script's directory (root folder)
ROOT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Set the path to the o1-assessment directory
O1_ASSESSMENT_DIR="$ROOT_DIR/o1-assessment"

# Check if the directory exists
if [ ! -d "$O1_ASSESSMENT_DIR" ]; then
    echo -e "\033[0;31mERROR: o1-assessment directory not found at $O1_ASSESSMENT_DIR\033[0m"
    exit 1
fi

# Change to the o1-assessment directory
cd "$O1_ASSESSMENT_DIR"

# Check for and activate virtual environment
VENV_PATH="$ROOT_DIR/.venv"
VENV_ACTIVATE="$VENV_PATH/bin/activate"

if [ -f "$VENV_ACTIVATE" ]; then
    echo -e "\033[0;36mFound virtual environment. Activating...\033[0m"
    source "$VENV_ACTIVATE"
    echo -e "\033[0;32mVirtual environment activated: $VENV_PATH\033[0m"
else
    echo -e "\033[0;33mNo virtual environment found at $VENV_PATH\033[0m"
    echo -e "\033[0;33mUsing system Python installation...\033[0m"
fi

# remove any Azure-related environment variables to avoid conflicts
for var in $(env | grep '^AZURE' | awk -F= '{print $1}'); do
    unset $var
done

# Check if Python is available
if ! command -v python &> /dev/null; then
    if ! command -v python3 &> /dev/null; then
        echo -e "\033[0;31mERROR: Python not found. Please install Python first.\033[0m"
        exit 1
    else
        PYTHON_CMD="python3"
    fi
else
    PYTHON_CMD="python"
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
echo -e "\033[0;36mUsing Python: $PYTHON_VERSION\033[0m"

# Check if Streamlit is installed
echo -e "\033[0;36mChecking for Streamlit...\033[0m"
if ! $PYTHON_CMD -m pip show streamlit &> /dev/null; then
    echo -e "\033[0;33mStreamlit not found. Installing...\033[0m"
    $PYTHON_CMD -m pip install streamlit
fi

# Start the frontend using the run_frontend.py script
echo -e "\n\033[0;32mLaunching Streamlit application...\033[0m"
echo -e "\033[0;36mThe application will open in your default browser.\033[0m"
echo -e "\033[0;33mPress Ctrl+C to stop the server.\n\033[0m"

$PYTHON_CMD run_frontend.py
