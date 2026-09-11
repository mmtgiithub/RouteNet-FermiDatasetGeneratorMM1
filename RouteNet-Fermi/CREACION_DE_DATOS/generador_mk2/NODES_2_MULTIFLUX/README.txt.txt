[Spanish]--> Aquí se encuentra el backend y el frontend de la generción de los archivos. Las explicaciones de los programas serán en inglés para que pueda leer el lector general

[English]--> Here lays both backend and frontend of dataset generation processes. The following lines will be in English

FRONTEND:
	DatasetGenerator.exe (GUI for creating the datasets)
	
	generate_ds_samples_gui.py (execute "pyInstaller --onefile --windowed --icon "icono.ico" generate_ds_samples_gui.py" 
								and then you will have a built version running in an exe file, THAT IS THE FRONT-END / GUI)
								It derives from the next script: GENERATE_DATASET_SAMPLES.py
								
				
	GENERATE_DATASET_SAMPLES.py (is the command-line tool for creating datasets in case of problems with GUI;
										it is very impractical to run this script)
										
BACKEND:
			
	GENERATE_INPUT_FILES.py  (is the script responsible for creating the file "input_files.txt")
	generate_stability.py (is the script responsible for creating the file "stability.txt")
	NODES_2_GENERATE_LINK_USAGE.py (is the script responsible for creating the file linkUsage.txt")
	NODES_2_GENERATE_SIMULATIONRESULTS.py (is the script responsible for creating the file simulationResults.txt")
	NODES_2_GENERATE_TRAFFIC.py (is the script responsible for creating the file traffic.txt")
	
	
IMPORTANT: You must check all the scripts and make the necessary changes to the paths, which are actually hardcoded.