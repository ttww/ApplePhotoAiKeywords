#!/bin/bash -e
#
# This script activates a Python environment and runs the keyword generator.
# (run after prepare_python.sh)
#
# Created by ttww@gmx.de / 2024/12/31

echo "Activate penv..."
source .venv/bin/activate

echo "Run python image keywords..."
python generate_keywords.py

echo "All done"



