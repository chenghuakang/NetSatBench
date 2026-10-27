#!/bin/bash

set -e

echo “Initialise Git“
git submodule update --init --recursive

echo “Creating virtual environment”
python3 -m venv env
source env/bin/activate

echo “Initialise dependency”
pip install --upgrade pip  
pip install -r requirements.txt

echo “Initialise docker
python3 ./nsb.py system-init-docker --config ./examples/StarPerf/Iridium/worker-config.json

echo “Set up succeed”
