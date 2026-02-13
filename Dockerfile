# Use an official Python runtime as a parent image
FROM python:3.12-slim-bullseye

# Set build arguments for UID and GID of the user to run the script
# This is useful to avoid running the script as root
ARG UID=1000
ARG GID=1000

# Set a working directory
WORKDIR /app

# Set environment variables to avoid prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install browsers in a shared location so non-root users can access them
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system dependencies required by Playwright (browsers and related libraries)
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libnspr4 \
        libnss3 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        xdg-utils \
        libgbm1 \
        # Clean-up
        && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache if they haven't changed.
COPY requirements.txt /app/requirements.txt

# Upgrade pip
RUN pip install --upgrade pip

# Install requirements
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Browsers (Chromium, Firefox, WebKit)
RUN playwright install --with-deps chromium

# Fix permissions for the shared browsers directory (change 1000:1000 accordingly)
RUN chown -R ${UID}:${GID} /ms-playwright

# Copy all files
COPY . /app/

# Set the command to run the script
# CMD ["python", "src/web-capture.py"]
