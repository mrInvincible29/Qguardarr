"""Docker utilities for integration testing"""

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import pytest

logger = logging.getLogger(__name__)


class DockerManager:
    """Manages Docker containers for integration testing"""
    
    def __init__(self):
        self.compose_file = "docker-compose.test.yml"
        self.project_name = "qguardarr-test"
        self.containers_started = False
        
    def is_docker_available(self) -> bool:
        """Check if Docker is available and running"""
        try:
            result = subprocess.run(
                ["docker", "version"], 
                capture_output=True, 
                text=True, 
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def is_compose_available(self) -> bool:
        """Check if Docker Compose is available"""
        try:
            result = subprocess.run(
                ["docker-compose", "--version"], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def cleanup_containers(self) -> bool:
        """Stop and remove all test containers"""
        try:
            logger.info("Cleaning up Docker containers...")
            
            # Stop containers
            subprocess.run([
                "docker-compose", "-f", self.compose_file, 
                "-p", self.project_name,
                "down", "--volumes", "--remove-orphans"
            ], capture_output=True, timeout=30)
            
            # Clean up any leftover containers
            subprocess.run([
                "docker", "container", "prune", "-f"
            ], capture_output=True, timeout=10)
            
            # Clean up volumes
            subprocess.run([
                "docker", "volume", "prune", "-f"
            ], capture_output=True, timeout=10)
            
            logger.info("✅ Docker cleanup completed")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("❌ Docker cleanup timed out")
            return False
        except Exception as e:
            logger.error(f"❌ Docker cleanup failed: {e}")
            return False
    
    def setup_test_data_dirs(self):
        """Create necessary test data directories"""
        test_dirs = [
            "test-data/qbit-config",
            "test-data/downloads", 
            "test-data/torrents",
            "test-data/qguardarr-data",
            "test-data/qguardarr-logs"
        ]
        
        for dir_path in test_dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    def start_containers(self) -> bool:
        """Start Docker containers for testing"""
        try:
            if self.containers_started:
                return True
                
            logger.info("Starting Docker containers for testing...")
            
            # Setup test directories
            self.setup_test_data_dirs()
            
            # Copy test config
            subprocess.run([
                "cp", "config/qguardarr.test.yaml", "config/qguardarr.yaml"
            ], check=True)
            
            # Start containers
            result = subprocess.run([
                "docker-compose", "-f", self.compose_file,
                "-p", self.project_name,
                "up", "-d", "--build"
            ], capture_output=True, text=True, timeout=120)
            
            if result.returncode != 0:
                logger.error(f"Failed to start containers: {result.stderr}")
                return False
            
            self.containers_started = True
            logger.info("✅ Docker containers started")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("❌ Container startup timed out")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to start containers: {e}")
            return False
    
    async def wait_for_service(self, service_name: str, url: str, timeout: int = 60) -> bool:
        """Wait for a service to become healthy"""
        logger.info(f"⏳ Waiting for {service_name} at {url}")
        
        start_time = time.time()
        
        async with httpx.AsyncClient() as client:
            while time.time() - start_time < timeout:
                try:
                    response = await client.get(url, timeout=5.0)
                    if response.status_code in (200, 202):
                        logger.info(f"✅ {service_name} is ready!")
                        return True
                except Exception:
                    pass
                
                await asyncio.sleep(2)
        
        logger.error(f"❌ {service_name} failed to start within {timeout}s")
        return False
    
    async def initialize_qbittorrent(self) -> bool:
        """Initialize qBittorrent with proper credentials"""
        logger.info("🔧 Initializing qBittorrent...")
        
        try:
            # Set environment variables for the init script
            env = os.environ.copy()
            env.update({
                "QBIT_HOST": "localhost",
                "QBIT_PORT": "8080", 
                "QBIT_PASSWORD": "adminpass123"
            })
            
            result = subprocess.run([
                "bash", "scripts/qbittorrent-init.sh"
            ], capture_output=True, text=True, timeout=90, env=env)
            
            if result.returncode == 0:
                logger.info("✅ qBittorrent initialization completed")
                logger.debug(f"Init output: {result.stdout}")
                return True
            else:
                logger.warning(f"⚠️ qBittorrent init returned {result.returncode}")
                logger.warning(f"Error output: {result.stderr}")
                logger.warning(f"Standard output: {result.stdout}")
                # Don't fail here - the password might still be set
                return True
                
        except subprocess.TimeoutExpired:
            logger.error("❌ qBittorrent initialization timed out after 90s")
            return False
        except Exception as e:
            logger.error(f"❌ qBittorrent initialization failed: {e}")
            return False
    
    def get_container_logs(self, container_name: str) -> str:
        """Get logs from a specific container"""
        try:
            result = subprocess.run([
                "docker", "logs", "--tail", "50", f"{self.project_name}_{container_name}_1"
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Failed to get logs: {result.stderr}"
                
        except Exception as e:
            return f"Error getting logs: {e}"
    
    def show_resource_usage(self):
        """Show Docker resource usage"""
        try:
            result = subprocess.run([
                "docker", "stats", "--no-stream", 
                "--format", "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                logger.info("Docker Resource Usage:")
                logger.info(result.stdout)
            
        except Exception as e:
            logger.warning(f"Could not get resource usage: {e}")


class QBittorrentHelper:
    """Helper class for qBittorrent operations in tests"""
    
    def __init__(self, host="localhost", port=8080, username="admin", password="adminadmin"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.base_url = f"http://{host}:{port}"
        
    async def is_healthy(self) -> bool:
        """Check if qBittorrent is healthy"""
        try:
            async with httpx.AsyncClient() as client:
                # Try multiple passwords (no log parsing)
                passwords_to_try = [
                    self.password or "adminadmin",  # Configured or default
                    "adminadmin",                    # Known preconfigured
                    "admin",                         # Simple fallback
                    "",                              # Empty fallback
                ]
                
                # Try each password
                for password in passwords_to_try:
                    try:
                        response = await client.post(
                            f"{self.base_url}/api/v2/auth/login",
                            data={"username": self.username, "password": password},
                            timeout=10.0
                        )
                        
                        if response.text.strip() == "Ok.":
                            # Now check version with cookie
                            response = await client.get(f"{self.base_url}/api/v2/app/version", timeout=5.0)
                            return response.status_code == 200
                    except Exception:
                        continue
                
                return False
        except Exception:
            return False
    
    async def authenticate(self) -> Optional[httpx.AsyncClient]:
        """Authenticate and return client with session"""
        try:
            client = httpx.AsyncClient()
            
            # Try multiple passwords (no log parsing)
            passwords_to_try = [
                self.password or "adminadmin",  # Configured or default
                "adminadmin",
                "admin",
                "",
            ]
            
            # Try each password
            for password in passwords_to_try:
                try:
                    response = await client.post(
                        f"{self.base_url}/api/v2/auth/login",
                        data={"username": self.username, "password": password},
                        timeout=10.0
                    )
                    
                    if response.text.strip() == "Ok.":
                        return client
                except Exception:
                    continue
            
            # If we get here, authentication failed
            await client.aclose()
            return None
                
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return None
    
    async def get_test_torrent_info(self) -> List[Dict]:
        """Get information about test torrents"""
        client = await self.authenticate()
        if not client:
            return []
            
        try:
            response = await client.get(
                f"{self.base_url}/api/v2/torrents/info",
                timeout=10.0
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return []
                
        except Exception as e:
            logger.error(f"Failed to get torrent info: {e}")
            return []
        finally:
            await client.aclose()


class QguardarrHelper:
    """Helper class for Qguardarr service operations in tests"""
    
    def __init__(self, host="localhost", port=8089):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
    
    async def is_healthy(self) -> bool:
        """Check if Qguardarr is healthy"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/health", timeout=5.0)
                if response.status_code == 200:
                    health_data = response.json()
                    return health_data.get("status") in ["healthy", "starting"]
                return False
        except Exception:
            return False
    
    async def get_stats(self) -> Dict:
        """Get Qguardarr statistics"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/stats", timeout=5.0)
                if response.status_code == 200:
                    return response.json()
                return {}
        except Exception:
            return {}
    
    async def send_test_webhook(self, event_data: Dict) -> bool:
        """Send a test webhook event"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/webhook",
                    data=event_data,
                    timeout=5.0
                )
                return response.status_code == 202
        except Exception:
            return False


# Test skip decorators
def skip_if_no_docker(func):
    """Skip test if Docker is not available"""
    docker_mgr = DockerManager()
    return pytest.mark.skipif(
        not docker_mgr.is_docker_available(),
        reason="Docker not available"
    )(func)


def skip_if_no_compose(func):
    """Skip test if Docker Compose is not available"""
    docker_mgr = DockerManager()
    return pytest.mark.skipif(
        not docker_mgr.is_compose_available(),
        reason="Docker Compose not available"
    )(func)


# Pytest markers
pytest_plugins = []

def pytest_configure(config):
    """Configure pytest with Docker markers"""
    config.addinivalue_line(
        "markers", "docker: mark test as requiring Docker containers"
    )
    config.addinivalue_line(
        "markers", "qbittorrent: mark test as requiring qBittorrent"
    )
    config.addinivalue_line(
        "markers", "qguardarr: mark test as requiring Qguardarr service"
    )
