import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="central-control",
    use_scm_version=True,
    author="Grey Christoforo",
    author_email="grey@christoforo.net",
    description="Instrument control software",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/greyltc/central-control",
    packages=setuptools.find_packages(),
    entry_points = {
        'console_scripts': ['central-control = central_control.__main__:main', 'wavelabs-relay-server = util.wavelabs_relay_server' ],
    },
    data_files=[('etc',['config/layouts.ini', 'config/gpib.conf', 'config/wavelabs-relay.service'])],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GPL-3.0",
        "Operating System :: OS Independent",
    ],
)
