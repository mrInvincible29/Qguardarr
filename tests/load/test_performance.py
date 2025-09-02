"""Load and performance tests for Qguardarr"""

import asyncio
import json
import psutil
import time
from pathlib import Path
from typing import List, Dict, Any

import httpx
import pytest

from src.qbit_client import QBittorrentClient, TorrentInfo
from src.config import QBittorrentSettings


class TorrentGenerator:
    """Generate synthetic torrents for load testing"""
    
    def __init__(self):
        self.tracker_patterns = [
            "http://tracker1.example.com/announce",
            "http://tracker2.example.com/announce", 
            "http://opentracker.org/announce",
            "udp://tracker.openbittorrent.com:80/announce",
            "http://tracker.archive.org:6969/announce"
        ]
        
    def generate_torrent(self, index: int) -> TorrentInfo:
        """Generate a synthetic torrent for testing"""
        return TorrentInfo(
            hash=f"test{index:08x}{'a' * 32}",
            name=f"Test Torrent {index}",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=1024 * (index % 10 + 1),  # 1-10 KB/s
            priority=1,
            num_seeds=index % 20 + 5,  # 5-24 seeds
            num_leechs=index % 10 + 2,  # 2-11 leeches
            ratio=1.5 + (index % 10) * 0.1,  # 1.5-2.4 ratio
            size=1024 * 1024 * (index % 100 + 10),  # 10-110 MB
            completed=1024 * 1024 * (index % 100 + 10),
            tracker=self.tracker_patterns[index % len(self.tracker_patterns)],
            category=f"category{index % 5}",
            tags=f"tag{index % 3},load-test",
            added_on=int(time.time()) - (index % 86400),  # Added within last day
            last_activity=int(time.time()) - (index % 3600)  # Activity within last hour
        )
        
    def generate_torrents(self, count: int) -> List[TorrentInfo]:
        """Generate multiple synthetic torrents"""
        return [self.generate_torrent(i) for i in range(count)]


class PerformanceMonitor:
    """Monitor system performance during tests"""
    
    def __init__(self):
        self.start_time = None
        self.measurements = []
        self.process = None
        
    def start_monitoring(self):
        """Start performance monitoring"""
        self.start_time = time.time()
        self.measurements = []
        
        # Try to find qguardarr process
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.info['name'] and 'src.main' in str(proc.info['cmdline']):
                    self.process = psutil.Process(proc.info['pid'])
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    
    def take_measurement(self, label: str) -> Dict[str, Any]:
        """Take a performance measurement"""
        if not self.start_time:
            self.start_monitoring()
            
        measurement = {
            "timestamp": time.time(),
            "elapsed": time.time() - self.start_time,
            "label": label,
            "system_memory_percent": psutil.virtual_memory().percent,
            "system_cpu_percent": psutil.cpu_percent(interval=0.1)
        }
        
        if self.process:
            try:
                measurement.update({
                    "process_memory_mb": self.process.memory_info().rss / 1024 / 1024,
                    "process_cpu_percent": self.process.cpu_percent(),
                    "process_threads": self.process.num_threads()
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self.process = None
                
        self.measurements.append(measurement)
        return measurement
        
    def get_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        if not self.measurements:
            return {}
            
        process_memory = [m.get("process_memory_mb", 0) for m in self.measurements if "process_memory_mb" in m]
        process_cpu = [m.get("process_cpu_percent", 0) for m in self.measurements if "process_cpu_percent" in m]
        
        return {
            "total_duration": time.time() - self.start_time if self.start_time else 0,
            "measurement_count": len(self.measurements),
            "process_memory_mb": {
                "min": min(process_memory) if process_memory else 0,
                "max": max(process_memory) if process_memory else 0,
                "avg": sum(process_memory) / len(process_memory) if process_memory else 0
            },
            "process_cpu_percent": {
                "min": min(process_cpu) if process_cpu else 0,
                "max": max(process_cpu) if process_cpu else 0,
                "avg": sum(process_cpu) / len(process_cpu) if process_cpu else 0
            },
            "measurements": self.measurements
        }


@pytest.mark.load
class TestMemoryUsage:
    """Test memory usage under different loads"""
    
    @pytest.fixture
    def monitor(self):
        """Performance monitor"""
        return PerformanceMonitor()
        
    @pytest.fixture
    def generator(self):
        """Torrent generator"""
        return TorrentGenerator()
    
    @pytest.mark.asyncio
    async def test_memory_usage_500_torrents(self, monitor, generator):
        """Test memory usage with 500 active torrents"""
        monitor.start_monitoring()
        
        # Generate 500 torrents
        torrents = generator.generate_torrents(500)
        monitor.take_measurement("Generated 500 torrents")
        
        # Simulate allocation engine processing
        from src.allocation import TorrentCache, AllocationEngine
        from src.config import QguardarrConfig, GlobalSettings
        from unittest.mock import Mock
        
        config = Mock(spec=QguardarrConfig)
        config.global_settings = Mock(spec=GlobalSettings)
        config.global_settings.rollout_percentage = 100
        config.global_settings.differential_threshold = 0.2
        
        cache = TorrentCache(capacity=1000)
        
        # Add torrents to cache
        for i, torrent in enumerate(torrents):
            cache.add_torrent(
                torrent.hash,
                f"tracker{i % 5}",
                torrent.upspeed,
                1024000 + (i * 1000)
            )
            
            if i % 100 == 0:
                monitor.take_measurement(f"Added {i} torrents to cache")
        
        final_measurement = monitor.take_measurement("Cache fully loaded")
        
        # Verify memory usage is within acceptable limits
        if "process_memory_mb" in final_measurement:
            memory_mb = final_measurement["process_memory_mb"]
            print(f"Memory usage with 500 torrents: {memory_mb:.2f} MB")
            
            # Should be under 60MB target
            assert memory_mb < 60, f"Memory usage {memory_mb:.2f} MB exceeds 60MB limit"
        
        # Get performance summary
        summary = monitor.get_summary()
        print(f"Performance summary: {json.dumps(summary, indent=2, default=str)}")
    
    @pytest.mark.asyncio
    async def test_memory_usage_1000_torrents(self, monitor, generator):
        """Test memory usage with 1000 active torrents"""
        monitor.start_monitoring()
        
        # Generate 1000 torrents
        torrents = generator.generate_torrents(1000)
        monitor.take_measurement("Generated 1000 torrents")
        
        from src.allocation import TorrentCache
        cache = TorrentCache(capacity=1500)
        
        # Add torrents to cache in batches
        for i, torrent in enumerate(torrents):
            cache.add_torrent(
                torrent.hash,
                f"tracker{i % 5}",
                torrent.upspeed,
                1024000 + (i * 1000)
            )
            
            if i % 200 == 0:
                monitor.take_measurement(f"Added {i} torrents")
        
        final_measurement = monitor.take_measurement("1000 torrents loaded")
        
        # Memory should still be reasonable (may be higher than 500 torrent test)
        if "process_memory_mb" in final_measurement:
            memory_mb = final_measurement["process_memory_mb"]
            print(f"Memory usage with 1000 torrents: {memory_mb:.2f} MB")
            
            # Should be under 80MB (allowing some increase for 1000 torrents)
            assert memory_mb < 80, f"Memory usage {memory_mb:.2f} MB exceeds 80MB limit"
        
        summary = monitor.get_summary()
        print(f"Performance summary: {json.dumps(summary, indent=2, default=str)}")

    @pytest.mark.asyncio
    async def test_memory_usage_3000_torrents(self, monitor, generator):
        """Test memory usage with 3000 active torrents (stress test)"""
        monitor.start_monitoring()
        
        # Generate 3000 torrents
        torrents = generator.generate_torrents(3000)
        monitor.take_measurement("Generated 3000 torrents")
        
        from src.allocation import TorrentCache
        cache = TorrentCache(capacity=5000)
        
        # Add torrents to cache in batches
        for i, torrent in enumerate(torrents):
            success = cache.add_torrent(
                torrent.hash,
                f"tracker{i % 10}",
                torrent.upspeed,
                1024000 + (i * 1000)
            )
            
            if not success:
                print(f"Cache full at {i} torrents")
                break
                
            if i % 500 == 0:
                monitor.take_measurement(f"Added {i} torrents")
        
        final_measurement = monitor.take_measurement("3000 torrents loaded")
        
        # This is stress test - memory may be higher
        if "process_memory_mb" in final_measurement:
            memory_mb = final_measurement["process_memory_mb"]
            print(f"Memory usage with 3000 torrents: {memory_mb:.2f} MB")
            
            # Should be under 100MB even for stress test
            assert memory_mb < 100, f"Memory usage {memory_mb:.2f} MB exceeds 100MB stress limit"
        
        summary = monitor.get_summary()
        print(f"Performance summary: {json.dumps(summary, indent=2, default=str)}")


@pytest.mark.load
class TestWebhookLoad:
    """Test webhook performance under load"""
    
    @pytest.mark.asyncio
    async def test_webhook_response_time(self):
        """Test webhook response time under normal load"""
        webhook_url = "http://localhost:8089/webhook"
        
        response_times = []
        
        async with httpx.AsyncClient() as client:
            for i in range(100):
                start_time = time.time()
                
                try:
                    response = await client.post(
                        webhook_url,
                        data={
                            "event": "complete",
                            "hash": f"test{i:08x}{'a' * 32}",
                            "name": f"Test Torrent {i}",
                            "tracker": "http://tracker.example.com/announce"
                        },
                        timeout=1.0
                    )
                    
                    response_time = (time.time() - start_time) * 1000
                    response_times.append(response_time)
                    
                    assert response.status_code == 202
                    
                except httpx.ConnectError:
                    pytest.skip("Qguardarr service not available")
                except httpx.TimeoutException:
                    response_times.append(1000)  # 1 second timeout
        
        # Calculate statistics
        avg_time = sum(response_times) / len(response_times)
        max_time = max(response_times)
        p95_time = sorted(response_times)[int(len(response_times) * 0.95)]
        
        print(f"Webhook response times: avg={avg_time:.1f}ms, max={max_time:.1f}ms, p95={p95_time:.1f}ms")
        
        # Should be very fast
        assert avg_time < 50, f"Average response time {avg_time:.1f}ms exceeds 50ms"
        assert p95_time < 100, f"P95 response time {p95_time:.1f}ms exceeds 100ms"
    
    @pytest.mark.asyncio 
    async def test_webhook_burst_load(self):
        """Test webhook handling burst load"""
        webhook_url = "http://localhost:8089/webhook"
        
        # First, check if service is available
        try:
            async with httpx.AsyncClient() as client:
                health_response = await client.get("http://localhost:8089/health", timeout=5.0)
                if health_response.status_code != 200:
                    pytest.skip("Qguardarr service not healthy")
        except Exception:
            pytest.skip("Qguardarr service not available")
        
        async def send_webhook(session, i):
            try:
                response = await session.post(
                    webhook_url,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "event": "add",
                        "hash": f"burst{i:08x}{'b' * 32}",
                        "name": f"Burst Torrent {i}",
                        "tracker": "http://tracker.example.com/announce"
                    },
                    timeout=5.0  # Increased timeout for load testing
                )
                return response.status_code, time.time()
            except Exception as e:
                return 500, time.time()
        
        start_time = time.time()
        
        # Use connection limits to prevent overwhelming the service
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
        async with httpx.AsyncClient(limits=limits) as client:
            # Send webhooks in smaller batches
            batch_size = 100
            results = []
            for batch_start in range(0, 1000, batch_size):
                batch_end = min(batch_start + batch_size, 1000)
                tasks = [send_webhook(client, i) for i in range(batch_start, batch_end)]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend(batch_results)
                # Small delay between batches to prevent overwhelming
                await asyncio.sleep(0.1)
        
        end_time = time.time()
        total_time = end_time - start_time
        
        # Count successful responses
        successful = sum(1 for result in results if isinstance(result, tuple) and result[0] == 202)
        
        print(f"Burst test: {successful}/1000 successful in {total_time:.2f}s")
        print(f"Rate: {successful / total_time:.1f} webhooks/second")
        
        # Should handle at least 80% successfully
        success_rate = successful / 1000
        assert success_rate > 0.8, f"Success rate {success_rate:.1%} too low"
        
        # Should complete within reasonable time
        assert total_time < 30, f"Burst test took {total_time:.1f}s, too slow"


@pytest.mark.load
class TestAllocationPerformance:
    """Test allocation cycle performance"""
    
    @pytest.fixture
    def generator(self):
        return TorrentGenerator()
    
    @pytest.mark.asyncio
    async def test_allocation_cycle_time(self, generator):
        """Test allocation cycle completion time"""
        # This would need to integrate with the actual allocation engine
        # For now, test the core algorithms in isolation
        
        from src.allocation import TorrentCache, AllocationEngine
        from src.tracker_matcher import TrackerMatcher
        from src.config import TrackerConfig
        from unittest.mock import Mock, AsyncMock
        
        # Setup mocks
        config = Mock()
        config.global_settings.rollout_percentage = 100
        config.global_settings.differential_threshold = 0.2
        
        qbit_client = AsyncMock()
        
        tracker_configs = [
            TrackerConfig(id="tracker1", name="T1", pattern=".*tracker1.*", max_upload_speed=5*1024*1024, priority=10),
            TrackerConfig(id="tracker2", name="T2", pattern=".*tracker2.*", max_upload_speed=3*1024*1024, priority=5),
            TrackerConfig(id="default", name="Default", pattern=".*", max_upload_speed=1*1024*1024, priority=1)
        ]
        
        tracker_matcher = TrackerMatcher(tracker_configs)
        rollback_manager = AsyncMock()
        
        allocation_engine = AllocationEngine(config, qbit_client, tracker_matcher, rollback_manager)
        
        # Generate test torrents
        torrents = generator.generate_torrents(500)
        
        # Time the limit calculation
        start_time = time.time()
        limits = allocation_engine._calculate_limits_phase1(torrents)
        calc_time = time.time() - start_time
        
        print(f"Limit calculation for 500 torrents: {calc_time:.3f}s")
        print(f"Rate: {len(torrents) / calc_time:.1f} torrents/second")
        
        # Should be very fast
        assert calc_time < 1.0, f"Limit calculation took {calc_time:.3f}s, too slow"
        assert len(limits) == len(torrents), "Not all torrents got limits"
        
        # Test with 1000 torrents
        torrents_1k = generator.generate_torrents(1000)
        
        start_time = time.time()
        limits_1k = allocation_engine._calculate_limits_phase1(torrents_1k)
        calc_time_1k = time.time() - start_time
        
        print(f"Limit calculation for 1000 torrents: {calc_time_1k:.3f}s")
        
        # Should still be fast and scale reasonably
        assert calc_time_1k < 2.0, f"1000 torrent calculation took {calc_time_1k:.3f}s, too slow"
        assert len(limits_1k) == len(torrents_1k)


@pytest.mark.load 
class TestEndToEndLoad:
    """End-to-end load testing"""
    
    @pytest.mark.asyncio
    async def test_full_system_load(self):
        """Test full system under realistic load"""
        
        # Check if services are available
        try:
            async with httpx.AsyncClient() as client:
                health_response = await client.get("http://localhost:8089/health", timeout=5.0)
                if health_response.status_code != 200:
                    pytest.skip("Qguardarr service not healthy")
        except Exception:
            pytest.skip("Qguardarr service not available")
        
        monitor = PerformanceMonitor()
        monitor.start_monitoring()
        
        # Send steady stream of webhooks for 30 seconds
        webhook_url = "http://localhost:8089/webhook"
        events_sent = 0
        successful_responses = 0
        
        async def send_continuous_webhooks():
            nonlocal events_sent, successful_responses
            
            async with httpx.AsyncClient() as client:
                end_time = time.time() + 30  # 30 seconds
                
                while time.time() < end_time:
                    try:
                        response = await client.post(
                            webhook_url,
                            data={
                                "event": "complete" if events_sent % 3 == 0 else "add",
                                "hash": f"load{events_sent:08x}{'c' * 32}",
                                "name": f"Load Test Torrent {events_sent}",
                                "tracker": f"http://tracker{events_sent % 5}.example.com/announce"
                            },
                            timeout=1.0
                        )
                        
                        events_sent += 1
                        if response.status_code == 202:
                            successful_responses += 1
                            
                    except Exception:
                        pass
                    
                    await asyncio.sleep(0.1)  # 10 events per second
        
        # Run load test
        start_time = time.time()
        await send_continuous_webhooks()
        total_time = time.time() - start_time
        
        final_measurement = monitor.take_measurement("Load test completed")
        
        print(f"Load test results:")
        print(f"  Duration: {total_time:.1f}s")
        print(f"  Events sent: {events_sent}")
        print(f"  Successful responses: {successful_responses}")
        print(f"  Success rate: {successful_responses / events_sent:.1%}")
        print(f"  Average rate: {events_sent / total_time:.1f} events/sec")
        
        if "process_memory_mb" in final_measurement:
            print(f"  Final memory usage: {final_measurement['process_memory_mb']:.2f} MB")
            
        # Verify system remained stable
        assert successful_responses / events_sent > 0.95, "Too many failed requests"
        
        if "process_memory_mb" in final_measurement:
            assert final_measurement["process_memory_mb"] < 80, "Memory usage too high"
        
        # Get detailed stats
        async with httpx.AsyncClient() as client:
            try:
                stats_response = await client.get("http://localhost:8089/stats", timeout=5.0)
                if stats_response.status_code == 200:
                    stats = stats_response.json()
                    print(f"  System stats: {json.dumps(stats, indent=2)}")
            except Exception:
                pass