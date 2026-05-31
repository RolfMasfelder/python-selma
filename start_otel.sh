#!/bin/bash

uv run phoenix serve &

sleep 2

open -a "Google Chrome" http://localhost:6006
