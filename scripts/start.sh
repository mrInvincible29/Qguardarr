#!/bin/bash

# Qguardarr startup script

set -e

echo "🚀 Starting Qguardarr..."

# Create required directories
mkdir -p data logs

# Check if config exists
if [ ! -f "config/qguardarr.yaml" ]; then
    echo "❌ Configuration file not found: config/qguardarr.yaml"
    echo "Please copy config/qguardarr.yaml.example and configure it"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/upgrade dependencies
echo "📦 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Run the application
echo "▶️ Starting Qguardarr service..."
python -m src.main