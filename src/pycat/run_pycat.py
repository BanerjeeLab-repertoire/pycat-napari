"""
Application Execution Module for PyCAT

PyCAT : Python Condensate Analysis Toolbox 

PyCAT is an open source application for the analysis of biomolecular condensates in biological images. It is 
diverse and robust enough for use in a wide range of bio-image analyses. The application is built on top of
the napari viewer, which allows for interactive visualization of images and annotations. PyCAT provides a
variety of tools for image processing, data analysis, and visualization, and is designed to be user-friendly
and accessible to researchers with a wide range of technical backgrounds.   

It provides a python native, no-code interface for the analysis of biological images. It serves not only as a 
stand-alone application, but as a platform for the development of new image analysis tools and methods. It is my
hope that PyCAT will be a valuable resource for the scientific community, and that it will help to advance our
understanding of the complex biological processes that underlie the formation and function of biomolecular condensates.
I hope it is useful to the community and that others will contribute to its development.

This module defines the run_pycat_func function, which is used to run the PyCAT application by creating a napari viewer
instance and initializing the CentralManager. The CentralManager acts as the central coordinating class for PyCAT,
integrating various components such as file input/output, data management, and user interface elements.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024

License
-------
Copyright (c) 2024, Christian Neureuter, Banerjee Lab, State University of New York at Buffalo
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the 
following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following 
disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following 
disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the University at Buffalo, the author, nor the names of its contributors may be used to endorse 
or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, 
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE 
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, 
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR 
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, 
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE 
USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

# Standard library imports
import sys 
import importlib.resources as resources

# Third party imports
import napari
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon 

# Local application imports
from pycat.central_manager import CentralManager





def run_pycat_func():
    """
    Function to run the PyCAT application by creating a napari viewer instance and initializing the CentralManager.
    """
    app = QApplication(sys.argv)  # sys.argv is necessary for proper app initialization

    try:
        # Use importlib.resources to get the path to the PyCAT logo
        logo_path = resources.files('pycat') / 'icons' / 'pycat_logo_512.png'
        with resources.as_file(logo_path) as icon_path:
            icon_path_str = str(icon_path)
        app.setWindowIcon(QIcon(icon_path_str))  # Set PyCAT logo as window icon
    except FileNotFoundError:
        print("The PyCAT logo file was not found.")
    except ModuleNotFoundError:
        print("The specified module 'pycat' was not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

    print("Running PyCAT")  # Print message to console
    CentralManager(napari.Viewer(title="PyCAT-Napari"))  # Initialize CentralManager and Napari Viewer
    napari.run()


def main():
    """
    Main function to run the PyCAT application. Serves as the entry point for the application.
    """
    run_pycat_func()

if __name__ == "__main__":
    main()





