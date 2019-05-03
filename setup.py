import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="mutovis-control",
    use_scm_version=True,
    author="Grey Christoforo",
    author_email="grey@mutovis.com",
    description="Software for collecting electrical characterization data for solar cells",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/mutovis/control-software",
    packages=setuptools.find_packages(),
    entry_points = {
        'gui_scripts': ['mutovis-control-gui = mutovis_control.__main__:main', ],
        'console_scripts': ['mutovis-control = mutovis_control.__main__:main', 'wavelabs-relay-server = util.wavelabs_relay_server' ],
    },
    data_files=[('etc',['config/layouts.ini', 'config/gpib.conf', 'config/wavelabs-relay.service'])],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GPL-3.0",
        "Operating System :: OS Independent",
    ],
)
