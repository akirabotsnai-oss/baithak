FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables to prevent Python from writing pyc files to disc
# and to prevent Python from buffering stdout and stderr
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project files
COPY . .

# Expose port (default 5000, can be overridden by platforms)
EXPOSE 5000

# Start hypercorn, binding to the PORT environment variable or 5000
CMD ["sh", "-c", "hypercorn app:app -b 0.0.0.0:${PORT:-5000}"]
