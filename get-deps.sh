#!/usr/bin/env bash

# install offical packages
sudo pacman -Sy --needed \
	python \
	python-paho-mqtt

# install unofficial ones
yay -Sy --needed \
	python-pyftdi \
	python-setuptools-scm-git-archive \
	python-pyvisa-py-git \ # change this to non-git once a release is cut
	python-pyvisa \ # change this to non-git once a release is cut (actually -git is broken now)
	linux-gpib-svn \
	python-gpib-ctypes
