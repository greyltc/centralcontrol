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
	python-pyvisa-git # change this to non-git once a release is cut
