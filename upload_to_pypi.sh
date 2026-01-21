#!/bin/bash
# Helper script to upload CondaNest to PyPI

echo "=========================================="
echo "CondaNest PyPI Upload Helper"
echo "=========================================="
echo ""
echo "Before uploading, make sure you have:"
echo "  1. Enabled 2FA on your PyPI account"
echo "  2. Created an API token at: https://pypi.org/manage/account/token/"
echo "  3. Token has 'Publish' scope (for all projects or this project)"
echo ""
echo "The token should look like: pypi-AgEIcH... (starts with pypi-)"
echo ""
read -p "Enter your PyPI API token: " -s TOKEN
echo ""
echo ""

# Set environment variables
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="$TOKEN"

echo "Uploading to PyPI..."
.build-venv/bin/twine upload dist/*

# Clear the token from environment
unset TWINE_USERNAME
unset TWINE_PASSWORD
unset TOKEN

echo ""
echo "Done! Check your package at: https://pypi.org/project/condanest/"
