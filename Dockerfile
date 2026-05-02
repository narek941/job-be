# Lightweight Python image — no Playwright/Chromium needed
FROM python:3.11-slim-bullseye

WORKDIR /app

# Install system dependencies for OCR
RUN apt-get update && apt-get install -y tesseract-ocr poppler-utils && rm -rf /var/lib/apt/lists/*

# Install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose port and start the app
# Render provides the PORT environment variable
CMD uvicorn armapply.main:app --host 0.0.0.0 --port ${PORT:-8000}
