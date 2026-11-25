# Headless Chromium for `make screenshots` (capture.py beside this file).
# The base image carries the browsers one Playwright release drives, not the
# Python package, so the FROM tag and the pip pin name the same release and
# move together. Bump both by hand.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble
RUN pip install --no-cache-dir playwright==1.65.2
