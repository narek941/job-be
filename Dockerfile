FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set working directory
WORKDIR /app

# Copy requirements and the local ApplyPilot package source first
# This allows pip to find the local requirement during the install phase
COPY requirements.txt .
COPY applypilot-src/ ./applypilot-src/

# Install dependencies (this will now find ./applypilot-src correctly)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend source code
COPY . .

# Expose port and start the app
EXPOSE 8000
CMD ["uvicorn", "armapply.main:app", "--host", "0.0.0.0", "--port", "8000"]
