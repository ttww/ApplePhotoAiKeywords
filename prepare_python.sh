#!/bin/bash -e
#
# This script sets up a Python environment and installs all dependencies from requirements.txt.
#
# Created by ttww@gmx.de / 2024/12/31


echo "Create penv..."
python -m venv .venv
source .venv/bin/activate

echo "Install requirements..."
pip install -r requirements.txt

echo "All done"



