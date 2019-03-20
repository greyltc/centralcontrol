import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="mutovis-control",
    version="1.0.8",
    author="Grey Christoforo",
    author_email="grey@mutovis.com",
    description="Software for collecting electrical characterization data for solar cells",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/mutovis/control-software",
    packages=setuptools.find_packages(),
    entry_points = {
        'console_scripts': ['mutovis-control-cli=mutovis_control.launch_cli:main'],
    },
    data_files=[('etc',['config/layouts.ini', 'config/gpib.conf', 'config/wavelabs-relay.service']),('bin',['utilities/wavelabs-relay-server'])],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GPL-3.0",
        "Operating System :: OS Independent",
    ],
)
