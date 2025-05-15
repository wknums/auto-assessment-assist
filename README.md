
## Assess With Reasoning (AWReason)

is a sample AI accelerator to assist educators to ease their workload related to fairly and consistently grading and assessing assignments of their learners.

This accelerator makes use of the latest generation of OpenAI's multimodal advanced reasoning models available on Azure (such as o1).

The accelerator is build using the Python language (V3.11 or later) and a number of opensource and Azure libraries.

It is intended for use with subject matter that o1 has been trained on, so should be widely applicable to primary, secondary and tertiary education topics.

Note: The purpose of this Accelerator is NOT to hand over the grading of assignments to AI, but to have the advanced reasoning capabilities of the AI give a second opinion to the educator to help minimize potential biases and identify points of difference in the grading approach with the help of commentary from the AI on specific areas being assessed.

**The final grade must be assigned by the human educator.**

  
  

### Key Features:

**Configurable Grading Options using natural language prompt file**:

- `--prompt`: a quoted string that contains the full set of instructions to be used to assess the content being sent to the model. -Note: this is primarily for quick tests. -

- `--prompt_file`: read the grading instruction prompt from this text file

  

1.  **PDF Processing Options**:

-  `--pdf_source`: Process all PDFs in a directory

-  `--pdf_file`: Process a single PDF file

-  `--join`: Option to join extracted images in pairs (vertical or horizontal)

2.  **Image Handling Improvements**:

- Automatic extraction and processing of images from PDFs

- Support for processing up to two PDFs at once

- Intelligent sorting of image files by page number

- Added image count validation (with a limit of 50 images)

3.  **Temporary File Management**:

- Uses system temp directory by default for extracted images

- Option to specify custom temp directory with `--tempdir`

- Automatic cleanup of temporary files when processing completes

4.  **Enhanced Document Handling**:

- Automatic detection and labeling of which images belong to which document

- Maintains correct page order within each document

- Adds context to the prompt when processing multiple documents

-

### Deployment:

  

At this stage the accelerator is expected to be deployed manually only in development mode to be executed locally on a workstation that has the vscode interactive development environment as well as git and python (V3.11 or 3.12) as well as Azure Developer CLI (azd) installed.

  

clone the git repository to your local vscode environment.

configure a virtual python environment for use with this project
python -m venv .venv

on Azure Portal, create an Azure AI foundry environment, a project and deploy an Azure OpenAI o1 base model.

use the chat playground in Azure AI Foundry to ask o1 a question to ensure that this works for you before you continue.

create a copy of the .env_sample file and save it as .env

edit the .env file:


copy the endpoint of your Azure OpenAI resource and paste it into the value for the AZURE_OPENAI_ENDPOINT field.

save the .env file

  

Test that your environment is working (Note: this is assuming a Windows OS development environment - for Limux please adapt the paths to use forward / ):

in the vscode environment, open a terminal and execute the following commands to activate your virtual environment log in to azure and run a test:

    .venv\scripts\activate
    
    pip install -r requirements.txt

    az login
    
    cd o1-assessment

    python awreason.py --pdf_file1 ".\sample_pdfs\Managing your driving and vehicle licenses in Autoria.pdf" --promptfile ".\prompts\sample_prompt.txt" --output ".\sample_grading_results"

This should run a sample assessment against the sample pdf file provided, show the output in the terminal and the output directory used in the above command. Note that the --output paramter expects an output filepath, but if it points to a directory, it will generate a result file with default name startng with the source file name in that folder - if the folder does not exist it will create it.

  

When this works, you can start customizing the accelerator for your own use.

  

- create your own promptfile - using a similar approach to the sample_prompt.txt file provided.

- add a folder with a test pdf file to be assessed ....

- review the grading results against your own assessment and adapt your prompt to include some examples that illustrate how certain sections should be graded by o1...

- Happy Grading!!

  

  

### Usage Examples:

1. Process a single PDF file:

  

    *python awreason.py --pdf_file1 "path/to/sample.pdf" --join horizontal --prompt "Analyze this document" --output "results.txt"*

  

2. Process all PDFs in a directory (will use the first two):

  

    *python awreason.py --pdf_source1 "path/to/pdfs/" --join vertical --promptfile "path/to/prompt.txt" --output "results.txt"*

  

3. Use existing image folders (no PDF processing):

 
    *python awreason.py --images_folder1 "path/to/images" --prompt "Analyze these images" --output "results.txt"*

  
4. Here's how you can use the updated script with the JSON template feature:
 

    *python awreason.py --promptfile your_prompt.txt --images_folder1 your_images_folder --jsonout_template wcg_capenature_template.json --output analysis_results.json*

  
5. Here's how you can use the updated script with the JSON template feature using a pdf source:
 

    *python awreason.py --promptfile your_prompt.txt --pdf_file1 "..\my_project\wcg_data\my.pdf" --jsonout_template ..\my_project\my_structured_out_template.json --output ..\my_project\o1_analysis_results.json*

  

6. Here's how you can use the updated script with the JSON template feature using a markdown (.md) source:

  

    *python awreason.py --promptfile your_prompt.txt --md_file "..\my_project\wcg_data\my.md" --jsonout_template ..\my_project\my_structured_out_template.json --output ..\my_project\o1_analysis_results.json*

  

### Limitations:

Due to existing limits with the o1 model, the maximum number of images that the model can analyze in one request is 50. This repo concatenates 2 consecutive page images into a single image without noticeable loss in vision quality and therefore we can process pdfs with up to 100 pages. (combined total if in 2 documents with even page numbers as remainder pages are not combined)

## AutoAssess with AI

is a sample AI accelerator to help automate all kinds of document based assessments and value extractions from unstructured documents and completing an Excel based spreadsheet with the results from the assessment or value extraction. 
**Use cases include:**
- **assist educators to ease their workload** related to fairly and consistently grading and assessing assignments of their learners where the results need to be collected in an Excel spreadsheet range.
- **assist planning assessors** needing to automate the extraction of planning metrics from unstructured documents and consolidating these in an Excel spreadsheet range.
- **assist planning assessors** needing to automate the evaluation/ assessment of planning metrics from unstructured documents and consolidating these in an Excel spreadsheet range.
- **scoring quiz answers** using AI and consolidating these in an Excel spreadsheet range. (this is provided as an example configuration as "sample-project" in the repo)

This accelerator makes use of the Azure OpenAI's multimodal gpt-4o model and uses Azure AI Search to facilitate hybrid vector searches against the source documents using a retrieval Augmented Generation (RAG) pattern to perform the AI tasks.

The accelerator is highly configurable and requires some configuration to adapt it for the intended use.
	 The main steps to configure it for your use case are:
1. create a folder for your project in the repo - name it something line "xyz-project"  - use a "-project" postfix in the name.
2. collect the unstructured documents (pdf, docx) - and store them in a folder under the xyz-project calling it "xyz-data"
3. create a folder in the xyz-project folder called  "xyz-grading" and store your target xlsx spreadsheet file there. 
4. create a folder called "xzy-grading" and copy/create the grading rubric markdown (.md) or text (.txt) file there 
5. if you are using the accelerator to help you do assessments / scoring, then create a folder called "xyz-assessment" and store the files you need to have assessed there. 
6. create a folder called "xyz-prompts"  and  create a "system-prompt.json" file there.  you can use the one from the "sample-project" as an example and adapt it as you see fit.
7. in the "xyz-project" folder , create a file called "column_overrides.json"
				 this file can be an empty json file ( just  containing {} )  or it can contain pairs of names of specific spreadsheet columns (downshifted case and single -spaced whitespaces)  and an override value for that column header that is more meaningful for the AI assessment.  for example in the sample_project the column "team hawks score" is overwritten with "team hawks" when it seen by the AI prompt to work better.
8. also in the "xyz-project" folder, create a file called "prompt_rules.json"
				 this file contains any column specific prompts that you would like the AI to respond to. Note that the column names it requires/expects are the ones after they have been overridden by the column_overrides.json  (if they have not been overridden, they must contain the exact spreadsheet column name (downshifted case and single-spaced whitespaces))   again, see the  example file in the sample-project folder.
9. now you create your AI Search index: first you need to ingest the documents to create your searchable knowledgebase. - to do this you need to configure your environment (.env file) to be aware of the Azure resources you have available and can use.
					- Azure OpenAI  resource with a gpt-4o and an embedding-3-large model deployment
					- Azure AI search resource with semantic ranker    
					- Azure AI hub and resource with Content Understanding capability
					- use the `.env_sample` as a template, complete it with your own settings and then save it as `.env`  in the main folder of the repo.
					- once the environment is set up, you can:
					   adapt the "`sample_project\command_eg\build_knowledgebase.cmd`" to point to each your documents to be ingested.
						
			 
			 
			 

The accelerator is built using the Python language (V3.11 or later) and a number of opensource and Azure libraries.