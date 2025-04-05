FROM python:3.9-slim

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install packages needed for monitoring and debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Prometheus client
RUN pip install --no-cache-dir prometheus-client

# Copy application code
COPY . .

# Create a non-root user to run the app
RUN groupadd -r trader && useradd -r -g trader trader
RUN chown -R trader:trader /app
USER trader

# Expose Prometheus metrics port
EXPOSE 8000

# Create data directories
RUN mkdir -p /app/data/logs /app/data/backtest

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp \
    PYTHONPATH=/app

# Default command - displays help
ENTRYPOINT ["python", "main.py"]
CMD ["--help"] 