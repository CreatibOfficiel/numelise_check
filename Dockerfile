FROM mcr.microsoft.com/playwright/python:v1.34.0-jammy

WORKDIR /app

# Copy requirements first to leverage cache
COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers
RUN playwright install chromium

# Copy the rest of the application
COPY . .

# Expose the port
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "consentcrawl.server:app", "--host", "0.0.0.0", "--port", "8000"]
