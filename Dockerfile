# Use Python 3.11 as the base
FROM python:3.11-slim-bullseye

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    asound2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and the local ApplyPilot package source first
COPY requirements.txt .
COPY applypilot-src/ ./applypilot-src/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install the chromium browser binary
RUN playwright install chromium

# Copy the rest of the backend source code
COPY . .

# Expose port and start the app
EXPOSE 8000
CMD ["uvicorn", "armapply.main:app", "--host", "0.0.0.0", "--port", "8000"]
