
Here lay both backend and frontend of dataset generation processes.

The files in here are statistical tools that try to make easier data analysis. "predict.py" has been
modded for bringing us some .npy files about information of the predicted flows, and then a section to visualize real flow value
versus predicted flow value for each iteration. Modded version of predict.py is not available at this repository but I have developed
some NPY visualizing tools and a statistical visualizer:

1. npy_distr_plotter.py: It's an easy and intuitive MatPlotLib-based CDF and PDF plotter, with multiple design options
	Extra: PDF can be also represented with KDE curves rather than with bars. You have to give as an input the file that returns "predictions.npy"

2. npy_painter.py: Tool for representing NPY RAW files in a plot; just that. 

3. averageplots.py : it takes a NPY file returned from predict.py and it gives you statistical informations like:
																					std deviation
																					mean
																					percentils
																					max values
																					mode
																					. 
																					. 
																					.
																					
These are post-prediction files. And each script has a .exe application associated; they have no back-end so they entirely run by itselves without any depedency