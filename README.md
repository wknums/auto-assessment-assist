## Assess With Reasoning (AWReason)
is a sample AI accelerator to assist educators to ease their workload related to fairly and consistently grading and assessing assignments of their learners.
This accelerator makes use of the latest generation of OpenAI's multimodal advanced reasoning models available on Azure (such as o1). 
The accelerator is build using the Python language (V3.11 or later) and a number of opensource and Azure libraries.
It is intended for use with subject matter that o1 has been trained on, so should be widely applicable to primary, secondary and tertiary education topics.
Note: The purpose of this Accelerator is NOT to hand over the grading of assignments to AI, but to have the advanced reasoning capabilities of the AI give a second opinion to the educator to help minimize potential biases and identify points of difference in the grading approach with the help of commentary from the AI on specific areas being assessed.
**The final grade must be assigned by the human educator.**


### Key Features:
 **Configurable Grading Options using natural language prompt file**:
    -   `--prompt`: a quoted string that contains the full set of instructions to be used to assess the content being sent to the model. -Note: this is primarily for quick tests. - 
    -   `--prompt_file`: read the grading instruction prompt from this text file

1.  **PDF Processing Options**:
    -   `--pdf_source`: Process all PDFs in a directory
    -   `--pdf_file`: Process a single PDF file
    -   `--join`: Option to join extracted images in pairs (vertical or horizontal)
2.  **Image Handling Improvements**:
     -   Automatic extraction and processing of images from PDFs
    -   Support for processing up to two PDFs at once
    -   Intelligent sorting of image files by page number
    -   Added image count validation (with a limit of 50 images)
3.  **Temporary File Management**:
     -   Uses system temp directory by default for extracted images
    -   Option to specify custom temp directory with  `--tempdir`
    -   Automatic cleanup of temporary files when processing completes
4.  **Enhanced Document Handling**:
    -   Automatic detection and labeling of which images belong to which document
    -   Maintains correct page order within each document
    -   Adds context to the prompt when processing multiple documents
    - 
### Deployment:

At this stage the accelerator is expected to be deployed manually only in development mode to be executed locally on a workstation that has the vscode interactive development environment as well as git and python installed.

clone the git repository to your local vscode environment.
configure a virtual python environment for use with this project 
on Azure Portal, create an Azure AI foundry environment, a project and deploy an Azure OpenAI o1 base model. 
use the chat playground in Azure AI Foundry to ask o1 a question to ensure that this works for you before you continue.
create a copy of the .env_sample file and save it as .env
edit the .env file:

  copy the endpoint of your Azure OpenAI resource and paste it into the value for the AZURE_OPENAI_ENDPOINT field.
  save the .env file

 Test that your environment is working:
  in the vscode environment, open a terminal and execute the following commands to activate your virtual environment and run a test:
  
 *.venv\scripts\activate
 pip install -r requirements.txt
 cd o1-assessment
 python awreason.py --pdf_file ".\sample_pdfs\Managing your driving and vehicle licenses in Autoria.pdf" --promptfile ".\prompts\sample_prompt.txt" --output ".\sample_grading_results"*
 
 This should run a sample assessment against the sample pdf file provided, show the output in the terminal and the output directory used in the above command.

When this works, you can start customizing the accelerator for your own use.

- create your own promptfile - using a similar approach to the sample_prompt.txt file provided.
- add a folder with a test pdf file to be assessed ....  
- review the grading results against your own assessment and adapt your prompt to include some examples that illustrate how certain sections should be graded by o1...
- Happy Grading!!

  

### Usage Examples:
1.  Process a single PDF file:

*python awreason.py --pdf_file "path/to/sample.pdf" --join horizontal --prompt "Analyze this document" --output "results.txt"*

2.  Process all PDFs in a directory (will use the first two):

*python awreason.py --pdf_source "path/to/pdfs/" --join vertical --promptfile "path/to/prompt.txt" --output "results.txt"*

3.  Use existing image folders (no PDF processing):

*python awreason.py --images_folder1 "path/to/images" --prompt "Analyze these images" --output "results.txt"*

### Limitations:
Due to existing limits with the o1 model, the maximum number of images that the model can analyze in one request is 50. This repo concatenates 2 consecutive page images into a single image without noticeable loss in vision quality and therefore we can process pdfs with up to 100 pages. (combined total if in 2 documents with even page numbers as remainder pages are not combined) 