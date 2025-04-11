cd infra\rag

REM build the knowledgebase for the sample project using the index name and other configurations set in the environment
python rag_this.py "..\..\sample_project\sample_data\1_London_Brochure.pdf"
python rag_this.py --keep_index "..\..\sample_project\sample_assessment\Quiz Team Panthers.docx"
python rag_this.py --keep_index "..\..\sample_project\sample_assessment\Quiz Team Hawks.docx" 