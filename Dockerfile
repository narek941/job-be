FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code including applypilot
COPY . .

# Install the local applypilot package
RUN pip install -e ./applypilot-src

# Playwright binaries are already included in the base image, 
# so we don't need to run `playwright install` here!

# For the web service
EXPOSE 8000
CMD ["uvicorn", "armapply.main:app", "--host", "0.0.0.0", "--port", "8000"]
