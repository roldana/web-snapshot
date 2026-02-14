# Use an official Python runtime as a parent image
FROM python:3.12-slim-bullseye

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

# Ensure the shared browsers directory is readable/executable by any user
RUN chmod -R a+rX /ms-playwright || true

# Copy all files
COPY . /app/

