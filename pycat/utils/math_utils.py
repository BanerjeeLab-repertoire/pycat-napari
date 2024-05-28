"""
Math Utilities Module for PyCAT

This module contains utility functions for mathematical operations that are used in the PyCAT application.
The functions include outlier removal, R squared calculation, and Gaussian kernel generation. These functions
are used in various parts of the application for data processing and analysis. 

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np


def remove_outliers_iqr(data):
    """
    Remove outliers from a dataset using the Interquartile Range (IQR) method. This method
    calculates the IQR as the difference between the 75th and 25th percentiles of the data.
    Data points outside 1.5 times the IQR from the quartiles are considered outliers and are
    removed. This technique is robust to extreme values that could skew the data distribution.

    Parameters
    ----------
    data : numpy.ndarray
        A numpy array containing the dataset from which outliers will be removed. The array
        can be of any shape but will be flattened for processing.

    Returns
    -------
    filtered_data : numpy.ndarray
        A numpy array containing the data after outlier removal. The shape of `filtered_data`
        might be smaller than `data` if outliers were found and removed. The data is returned
        in the same shape it was input.

    Notes
    -----
    The IQR method is often preferred over z-score or standard deviation methods for outlier
    removal in cases where the data may not follow a normal distribution. This approach is
    based on quartile measurements, thus it is less sensitive to extreme values.

    The bounds for outlier detection are calculated as 1.5 times the IQR below the 25th percentile
    and 1.5 times the IQR above the 75th percentile. This method assumes that the data distribution
    is approximately symmetric around the median.

    Examples
    --------
    >>> data = np.array([1, 2, 3, 4, 5, 6, 100])
    >>> filtered_data = remove_outliers_iqr(data)
    >>> print(filtered_data)
    [1 2 3 4 5 6]
    """
    # Calculate the first and third quartiles (Q1 and Q3)
    q1 = np.percentile(data, 25)
    q3 = np.percentile(data, 75)
    # Calculate the Interquartile Range (IQR)
    iqr = q3 - q1

    # Determine the lower and upper bounds for outlier detection
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    # Filtering out the outliers
    filtered_data = data[(data >= lower_bound) & (data <= upper_bound)]

    return filtered_data


def calculate_r_squared(actual, predicted):
    """
    Calculate the coefficient of determination (R squared), which assesses the goodness of fit 
    between actual and predicted values from a regression model.

    R squared quantifies the proportion of variance in the dependent variable that is predictable 
    from the independent variables. A value of 1 indicates a perfect fit, a value of 0 indicates that 
    the model predicts none of the variability of the response data around its mean.

    Parameters
    ----------
    actual : numpy.array
        The actual values observed; the dependent variable.
    predicted : numpy.array
        The values predicted by a regression model; the independent variable predictions.

    Returns
    -------
    r_squared : float
        The R squared value, a statistic that ranges from 0 to 1, where higher values indicate a better fit.
    """
    # Calculate the residual sum of squares (difference between actual and predicted values)
    residual_sum_of_squares = np.sum((actual - predicted) ** 2)
    
    # Calculate the total sum of squares (variability of the actual values)
    total_sum_of_squares = np.sum((actual - np.mean(actual)) ** 2)
    
    # Compute R squared using its formula
    r_squared =  - (residual_sum_of_squares / total_sum_of_squares)

    return r_squared



def create_2d_gaussian_kernel(kernel_size, sigma=None):
    """
    Generate a 2D Gaussian kernel, which is commonly used as a Point Spread Function (PSF) in image processing
    applications, particularly for simulating a Gaussian blur effect.

    The kernel is a square matrix with dimensions defined by `kernel_size`, centered on the Gaussian peak.
    The sum of all elements in the kernel is normalized to 1, ensuring no change in the overall image brightness
    after convolution.

    Parameters
    ----------
    kernel_size : int
        The size (height and width) of the square Gaussian matrix.
    sigma : float, optional
        The standard deviation of the Gaussian distribution. Defaults to a calculation based on the kernel size
        that approximates the behavior of a Gaussian blur in image processing contexts.

    Returns
    -------
    kernel : numpy.array
        A 2D numpy array representing the Gaussian kernel. The kernel values follow a Gaussian distribution,
        centered in the matrix, and normalized such that the sum equals 1.

    Notes
    -----
    A default sigma is calculated if not provided, using a formula that balances between spread and central peak 
    intensity based on the kernel size.
    """
    
    # Calculate the default sigma value if not provided
    if sigma is None:
        sigma = 0.3*((kernel_size-1)*0.5 - 1) + 0.8 # Calculated as per OpenCV documentation
    # Create a 1D kernel
    ax = np.arange(-kernel_size // 2 + 1., kernel_size // 2 + 1.)
    # Create a 2D kernel by meshgrid
    xx, yy = np.meshgrid(ax, ax)
    # Create the Gaussian kernel using the 2D Gaussian formula
    kernel = np.exp(-0.5 * (np.square(xx) + np.square(yy)) / np.square(sigma))
    # Normalize the kernel to ensure the sum of all elements equals 1
    kernel = kernel / np.sum(kernel)

    return kernel