FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY config/ ./config/

# Create directories for data and logs
RUN mkdir -p data logs

# Create non-root user for security
RUN groupadd -r qguardarr && useradd -r -g qguardarr qguardarr
RUN chown -R qguardarr:qguardarr /app
USER qguardarr

# Expose port
EXPOSE 8089

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8089/health')" || exit 1

# Run the application
CMD ["python", "-m", "src.main"]