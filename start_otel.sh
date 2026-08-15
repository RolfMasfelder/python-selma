#!/bin/bash

cd "$(dirname "$0")"
source venv/bin/activate

phoenix serve &

sleep 2

open -a "Google Chrome" http://localhost:6006
