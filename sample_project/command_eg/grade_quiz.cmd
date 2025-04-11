cd . 
REM This command file is used to run the auto_assess.py script with specific parameters for grading a quiz.
python auto_assess.py --target-file "sample_project\sample_grading\SampleQuiz.xlsx" --project-dir "sample_project" --grading-rubric "sample_project\sample_rubric\rubric.md" --sheet-name Sheet1 --start-row "2" --end-row "4" --start-col "2" --end-col "3"