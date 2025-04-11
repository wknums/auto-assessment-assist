> **rag_this is a quick python based RAG accelerator**  built from a combination of code based on [liamca/GPT4oContentExtraction: Using Azure OpenAI GPT 4o to extract information such as text, tables and charts from Documents to Markdown](https://github.com/liamca/GPT4oContentExtraction) and the **Content Understanding** sample code  from this repo: [Azure-Samples/azure-ai-content-understanding-python](https://github.com/Azure-Samples/azure-ai-content-understanding-python)
> 
> Input can be individual .docx or pdf files (other formats like pptx may work but have not been tested)  which will be parsed by Azure content understanding and then chunked into logically related sections based on headings and paragraphs.
> Once chunked, the chunks are stored in a folder and embedding is performed for vector indexing. Once embedding is complete the embedded chunks (stored in json file) are indexed in Azure AI Search.
When you attempt to index an existing document, this will be detected and ignored.

When calling the rag_this.py program, you can specify --keep_index to retain the specified index and append the document - default is False ( i.e. if you don't add the --keep_index parameter, the index will be dropped and re-created)
> The entire RAG pipeline process can be run locally on a workstation 
> build your RAG environment with: 
> python rag_this.py parameters:  sourcefile [--keep_index]