#!/bin/bash

# qBittorrent initialization script for Docker testing
# This script configures the qBittorrent container with proper credentials

set -e

QBIT_HOST="${QBIT_HOST:-localhost}"
QBIT_PORT="${QBIT_PORT:-8080}"
# Known test password; no log parsing or password changes
KNOWN_PASSWORD="${QBIT_PASSWORD:-adminadmin}"
MAX_ATTEMPTS=30
ATTEMPT=0

echo "🔧 Initializing qBittorrent container..."

# Function to wait for qBittorrent to be accessible
wait_for_qbittorrent() {
    echo "⏳ Waiting for qBittorrent to be accessible at http://$QBIT_HOST:$QBIT_PORT"
    
    while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
        if curl -s --connect-timeout 5 "http://$QBIT_HOST:$QBIT_PORT" > /dev/null 2>&1; then
            echo "✅ qBittorrent is accessible!"
            return 0
        fi
        
        ATTEMPT=$((ATTEMPT + 1))
        echo "Attempt $ATTEMPT/$MAX_ATTEMPTS - waiting 5 seconds..."
        sleep 5
    done
    
    echo "❌ Failed to connect to qBittorrent after $MAX_ATTEMPTS attempts"
    return 1
}

# Function to try authentication with retry and backoff
try_auth() {
    local username=$1
    local password=$2
    local cookie_jar=$3
    local max_retries=${4:-3}
    
    for attempt in $(seq 1 $max_retries); do
        echo "  Attempt $attempt/$max_retries for user '$username'"
        
        RESPONSE=$(curl -s -c "$cookie_jar" \
            -d "username=$username" \
            -d "password=$password" \
            "http://$QBIT_HOST:$QBIT_PORT/api/v2/auth/login" 2>/dev/null || true)
        
        if echo "$RESPONSE" | grep -q "Ok"; then
            echo "✅ Successfully authenticated as '$username'"
            return 0
        elif echo "$RESPONSE" | grep -q -i "banned\|forbidden"; then
            echo "⚠️  IP banned, waiting before retry..."
            sleep $((attempt * 5))  # Exponential backoff: 5s, 10s, 15s
        else
            echo "  Authentication failed: $RESPONSE"
            sleep 2  # Short delay between attempts
        fi
    done
    
    return 1
}

# Function to authenticate with default credentials (no log parsing)
authenticate_default() {
    echo "🔐 Attempting authentication with known credentials..."
    
    COOKIE_JAR=$(mktemp)
    
    # Try known credentials first
    echo "🔑 Trying admin / $KNOWN_PASSWORD"
    if try_auth "admin" "$KNOWN_PASSWORD" "$COOKIE_JAR"; then
        echo "$COOKIE_JAR"
        return 0
    fi
    
    # Fallbacks
    if [ "$KNOWN_PASSWORD" != "adminadmin" ]; then
        echo "🔑 Trying admin / adminadmin"
        if try_auth "admin" "adminadmin" "$COOKIE_JAR"; then
            echo "$COOKIE_JAR"
            return 0
        fi
    fi
    
    echo "🔑 Trying admin / admin"
    if try_auth "admin" "admin" "$COOKIE_JAR"; then
        echo "$COOKIE_JAR"
        return 0
    fi
    
    echo "🔑 Trying empty credentials"
    if try_auth "" "" "$COOKIE_JAR"; then
        echo "$COOKIE_JAR"
        return 0
    fi
    
    rm -f "$COOKIE_JAR"
    echo "❌ Failed to authenticate with known credentials"
    return 1
}

# No password changes or log parsing are performed in this script.

# Main execution
main() {
    echo "=== qBittorrent Container Initialization ==="
    echo "Host: $QBIT_HOST:$QBIT_PORT"
    echo "Known Password: $KNOWN_PASSWORD"
    echo ""
    
    # Step 1: Wait for qBittorrent to be accessible
    if ! wait_for_qbittorrent; then
        exit 1
    fi
    
    # Step 2: Authenticate with default credentials (no password changes)
    COOKIE_JAR=$(authenticate_default)
    if [ $? -ne 0 ]; then
        echo "❌ Could not authenticate with qBittorrent"
        exit 1
    fi
    
    # Cleanup
    rm -f "$COOKIE_JAR"
    
    echo ""
    echo "✅ qBittorrent initialization verified (no password change needed)"
    echo "You can use credentials: admin / $KNOWN_PASSWORD"
}

# Run main function
main "$@"
