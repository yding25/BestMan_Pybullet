from setuptools import setup, find_packages

setup(
    name='BestMan_Pybullet',
    version='1.0',
    packages=find_packages(),
    install_requires=[
        'pybullet',
        'numpy',
	    'networkx',
	    'matplotlib',
        'opencv-python',
        'shapely'
        # Add other dependencies here
    ],
)
