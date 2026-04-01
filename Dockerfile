FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for exports
RUN mkdir -p docs/data output

# Default: run the web server
# Override with pipeline command for cron service
CMD ["python", "server.py"]
