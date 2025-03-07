# Use an official Python runtime as a parent image
FROM python:3.12-slim-bullseye

# Set a working directory
WORKDIR /app

# Copy all files
COPY . /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Browsers (Chromium, Firefox, WebKit)
RUN playwright install --with-deps chromium

# Set the command to run the script
CMD ["python", "src/web-capture.py"]
