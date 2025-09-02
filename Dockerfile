FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Speed up pip and prefer binary wheels
ARG PIP_ONLY_BINARY
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_NO_COMPILE=1 \
    PIP_ONLY_BINARY=${PIP_ONLY_BINARY}

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install -r requirements.txt

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
