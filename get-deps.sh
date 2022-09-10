#!/usr/bin/env bash

# install offical packages
sudo pacman -Sy --needed \
	python \
	python-paho-mqtt

# install unofficial ones
yay -Sy --needed \
	python-pyftdi \
	python-setuptools-scm-git-archive \
	linux-gpib-svn \
	python-gpib-ctypes
