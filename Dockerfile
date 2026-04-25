# Lightweight Python image — no Playwright/Chromium needed
FROM python:3.11-slim-bullseye

WORKDIR /app

# Install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose port and start the app
EXPOSE 8000
CMD ["uvicorn", "armapply.main:app", "--host", "0.0.0.0", "--port", "8000"]
