#!/bin/bash
set -e

echo "🐳 === Qguardarr Docker Integration Testing ==="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="docker-compose.test.yml"
PROJECT_NAME="qguardarr-test"
TEST_TIMEOUT=300  # 5 minutes total timeout

# Parse command line arguments
QUICK_MODE=false
CLEANUP_ONLY=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -q|--quick)
            QUICK_MODE=true
            shift
            ;;
        -c|--cleanup)
            CLEANUP_ONLY=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -q, --quick    Run minimal quick tests only"
            echo "  -c, --cleanup  Cleanup containers and exit"
            echo "  -v, --verbose  Verbose output"
            echo "  -h, --help     Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Function to log with timestamp
log() {
    local level=$1
    shift
    local color=""
    
    case $level in
        INFO) color=$BLUE ;;
        SUCCESS) color=$GREEN ;;
        WARNING) color=$YELLOW ;;
        ERROR) color=$RED ;;
    esac
    
    echo -e "${color}[$(date '+%H:%M:%S')] $level: $*${NC}"
}

# Function to check if Docker is available
check_docker() {
    log INFO "Checking Docker availability..."
    
    if ! command -v docker &> /dev/null; then
        log ERROR "Docker is not installed or not in PATH"
        return 1
    fi
    
    if ! docker version &> /dev/null; then
        log ERROR "Docker daemon is not running"
        return 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        log ERROR "Docker Compose is not installed or not in PATH"
        return 1
    fi
    
    log SUCCESS "Docker and Docker Compose are available"
    return 0
}

# Function to cleanup containers
cleanup() {
    log INFO "Cleaning up Docker containers and volumes..."
    
    # Stop and remove containers
    docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down --volumes --remove-orphans 2>/dev/null || true
    
    # Clean up any leftover containers with our project name
    docker ps -a --filter "name=$PROJECT_NAME" --format "{{.ID}}" | xargs -r docker rm -f 2>/dev/null || true
    
    # Clean up volumes
    docker volume ls --filter "name=$PROJECT_NAME" --format "{{.Name}}" | xargs -r docker volume rm 2>/dev/null || true
    
    # Prune unused containers and networks
    docker container prune -f 2>/dev/null || true
    docker network prune -f 2>/dev/null || true
    
    log SUCCESS "Cleanup completed"
}

# Function to setup test environment
setup_test_env() {
    log INFO "Setting up test environment..."
    
    # Create test data directories
    mkdir -p test-data/{qbit-config,downloads,torrents,qguardarr-data,qguardarr-logs}
    
    # Copy test configuration
    if [ -f "config/qguardarr.test.yaml" ]; then
        cp "config/qguardarr.test.yaml" "config/qguardarr.yaml"
        log INFO "Copied test configuration"
    else
        log WARNING "Test configuration not found, using default"
    fi
    
    # Ensure public torrents data exists
    if [ ! -f "test-data/public_torrents.json" ]; then
        log WARNING "public_torrents.json not found, some tests may be skipped"
    fi
    
    log SUCCESS "Test environment setup completed"
}

# Function to start Docker containers
start_containers() {
    log INFO "Starting Docker containers..."
    
    # Pull images first (with timeout)
    timeout 180 docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" pull || {
        log WARNING "Image pull timed out or failed, continuing with existing images"
    }
    
    # Start containers
    if $VERBOSE; then
        docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d --build
    else
        docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d --build > /dev/null 2>&1
    fi
    
    if [ $? -ne 0 ]; then
        log ERROR "Failed to start containers"
        show_container_logs
        return 1
    fi
    
    log SUCCESS "Docker containers started"
    return 0
}

# Function to wait for services
wait_for_services() {
    log INFO "Waiting for services to become healthy..."
    
    local max_wait=120  # 2 minutes
    local waited=0
    local interval=5
    
    while [ $waited -lt $max_wait ]; do
        local qbit_status=$(docker inspect qbittorrent-test --format='{{.State.Health.Status}}' 2>/dev/null || echo "none")
        local qguardarr_healthy=false
        
        # Check qBittorrent health
        if [ "$qbit_status" = "healthy" ]; then
            log INFO "qBittorrent is healthy"
            
            # Check Qguardarr health via HTTP
            if curl -f http://localhost:8089/health > /dev/null 2>&1; then
                qguardarr_healthy=true
                log INFO "Qguardarr is healthy"
            fi
        fi
        
        # In quick mode, only wait for qBittorrent
        if $QUICK_MODE && [ "$qbit_status" = "healthy" ]; then
            log SUCCESS "Services ready (quick mode)"
            return 0
        fi
        
        # In full mode, wait for both services
        if [ "$qbit_status" = "healthy" ] && [ "$qguardarr_healthy" = true ]; then
            log SUCCESS "All services are healthy"
            return 0
        fi
        
        echo -n "."
        sleep $interval
        waited=$((waited + interval))
    done
    
    log ERROR "Services failed to become healthy within ${max_wait}s"
    show_container_logs
    return 1
}

# Function to initialize qBittorrent
initialize_qbittorrent() {
    log INFO "Skipping qBittorrent init; using preconfigured credentials"
    # The test image mounts a preconfigured config with admin/adminadmin.
    # No explicit initialization needed.
    return 0
}

# Function to run tests
run_tests() {
    # Build pytest args as an array to preserve proper quoting
    local -a pytest_args
    pytest_args+=("tests/integration/")

    if $QUICK_MODE; then
        log INFO "Running quick Docker tests..."
        pytest_args+=("-m" "docker and not slow" "-x")
    else
        log INFO "Running full Docker integration tests..."
        pytest_args+=("-m" "docker")
    fi

    if $VERBOSE; then
        pytest_args+=("-v" "-s")
    else
        pytest_args+=("--tb=short")
    fi

    # Activate virtual environment if it exists
    if [ -f "venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source venv/bin/activate
        log INFO "Activated Python virtual environment"
    fi

    # Log and run the tests with timeout
    log INFO "Executing pytest with args: ${pytest_args[*]}"

    timeout "$TEST_TIMEOUT" python -m pytest "${pytest_args[@]}"
    local test_exit_code=$?

    if [ $test_exit_code -eq 0 ]; then
        log SUCCESS "All tests passed!"
    elif [ $test_exit_code -eq 124 ]; then
        log ERROR "Tests timed out after ${TEST_TIMEOUT}s"
    else
        log ERROR "Tests failed with exit code $test_exit_code"
    fi

    return $test_exit_code
}

# Function to run health checks
run_health_checks() {
    log INFO "Running health checks..."
    
    local failed=0
    
    # Check qBittorrent
    if curl -f http://localhost:8080 > /dev/null 2>&1; then
        log SUCCESS "qBittorrent: Healthy"
    else
        log ERROR "qBittorrent: Not responding"
        failed=$((failed + 1))
    fi
    
    # Check Qguardarr (if not in quick mode)
    if ! $QUICK_MODE; then
        if curl -f http://localhost:8089/health > /dev/null 2>&1; then
            log SUCCESS "Qguardarr: Healthy"
        else
            log ERROR "Qguardarr: Not responding"
            failed=$((failed + 1))
        fi
    fi
    
    return $failed
}

# Function to show container logs
show_container_logs() {
    log INFO "Container status:"
    docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps
    
    echo ""
    log INFO "Recent container logs:"
    
    echo ""
    echo "=== qBittorrent logs ==="
    docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs --tail=20 qbittorrent-test || true
    
    if ! $QUICK_MODE; then
        echo ""
        echo "=== Qguardarr logs ==="
        docker-compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs --tail=20 qguardarr-test || true
    fi
}

# Function to show resource usage
show_resource_usage() {
    log INFO "Docker resource usage:"
    
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" 2>/dev/null || {
        log WARNING "Could not retrieve resource stats"
    }
}

# Trap to ensure cleanup on exit
trap cleanup EXIT

# Main execution
main() {
    local start_time=$(date +%s)
    
    echo ""
    log INFO "Starting Docker integration testing..."
    log INFO "Mode: $([ "$QUICK_MODE" = true ] && echo "Quick" || echo "Full")"
    log INFO "Verbose: $VERBOSE"
    echo ""
    
    # Check prerequisites
    if ! check_docker; then
        exit 1
    fi
    
    # Cleanup only mode
    if $CLEANUP_ONLY; then
        cleanup
        exit 0
    fi
    
    # Initial cleanup
    cleanup
    
    # Setup test environment
    if ! setup_test_env; then
        exit 1
    fi
    
    # Start containers
    if ! start_containers; then
        exit 1
    fi
    
    # Wait for services
    if ! wait_for_services; then
        exit 1
    fi
    
    # Initialize qBittorrent
    initialize_qbittorrent
    
    # Run health checks
    if ! run_health_checks; then
        log WARNING "Some services are not healthy, but continuing with tests..."
    fi
    
    # Run tests
    local test_result=0
    if ! run_tests; then
        test_result=1
    fi
    
    # Show final status
    echo ""
    log INFO "=== Test Summary ==="
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    log INFO "Total duration: ${duration}s"
    
    if ! $QUICK_MODE; then
        show_resource_usage
    fi
    
    if [ $test_result -eq 0 ]; then
        log SUCCESS "🎉 Docker integration tests completed successfully!"
    else
        log ERROR "❌ Docker integration tests failed"
        echo ""
        log INFO "Troubleshooting tips:"
        echo "  - Check container logs: docker-compose -f $COMPOSE_FILE logs"
        echo "  - Run with verbose output: $0 --verbose"
        echo "  - Try quick mode first: $0 --quick"
        echo "  - Manual cleanup: $0 --cleanup"
    fi
    
    return $test_result
}

# Run main function
main "$@"
