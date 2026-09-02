#!/bin/sh
set -eu
cd /entropy
export DRY_RUN=true
exec /usr/local/lib/hermes-agent/venv/bin/python3 -u main.py
# live execution intentionally unavailable in this scaffold; explicit approval required.
