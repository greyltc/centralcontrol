import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="mutovis-control",
    version="0.9.7",
    author="Grey Christoforo",
    author_email="grey@mutovis.com",
    description="Software for collecting electrical characterization data for solar cells",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/mutovis/control-software",
    packages=setuptools.find_packages(),
    entry_points = {
        'console_scripts': ['mutovis-control-cli=mutovis_control.cli:run'],
    },
    data_files=[('config',['etc/layouts.ini'])],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GPL-3.0",
        "Operating System :: OS Independent",
    ],
)
