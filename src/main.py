"""Main FastAPI application for Qguardarr"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.allocation import AllocationEngine
from src.config import ConfigLoader
from src.qbit_client import QBittorrentClient
from src.rollback import RollbackManager
from src.tracker_matcher import TrackerMatcher
from src.webhook_handler import WebhookHandler

# Global state
app_state: Dict[str, Any] = {
    "config": None,
    "qbit_client": None,
    "webhook_handler": None,
    "tracker_matcher": None,
    "allocation_engine": None,
    "rollback_manager": None,
    "start_time": time.time(),
    "last_cycle_time": None,
    "last_cycle_duration": None,
    "health_status": "starting",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # Startup
    try:
        await startup_event()
        app_state["health_status"] = "healthy"
        yield
    except Exception as e:
        logging.error(f"Startup failed: {e}")
        app_state["health_status"] = "unhealthy"
        raise
    finally:
        # Shutdown
        await shutdown_event()


app = FastAPI(
    title="Qguardarr",
    description="qBittorrent per-tracker upload speed limiter",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware for potential web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def startup_event():
    """Initialize application components"""
    logging.info("Starting Qguardarr...")

    # Load configuration
    config_loader = ConfigLoader()
    config = config_loader.load_config()
    app_state["config"] = config
    app_state["config_loader"] = config_loader

    # Initialize components
    app_state["qbit_client"] = QBittorrentClient(config.qbittorrent)
    app_state["tracker_matcher"] = TrackerMatcher(config.trackers)
    app_state["rollback_manager"] = RollbackManager(config.rollback)
    app_state["allocation_engine"] = AllocationEngine(
        config=config,
        qbit_client=app_state["qbit_client"],
        tracker_matcher=app_state["tracker_matcher"],
        rollback_manager=app_state["rollback_manager"],
    )
    app_state["webhook_handler"] = WebhookHandler(
        config=config, allocation_engine=app_state["allocation_engine"]
    )

    # Connect to qBittorrent
    await app_state["qbit_client"].connect()

    # Initialize rollback system
    await app_state["rollback_manager"].initialize()

    # Start background tasks
    asyncio.create_task(allocation_cycle_task())
    asyncio.create_task(app_state["webhook_handler"].start_event_processor())

    logging.info("Qguardarr started successfully")


async def shutdown_event():
    """Cleanup on shutdown"""
    logging.info("Shutting down Qguardarr...")

    if app_state.get("webhook_handler"):
        await app_state["webhook_handler"].stop()

    if app_state.get("qbit_client"):
        await app_state["qbit_client"].disconnect()

    logging.info("Qguardarr shutdown complete")


async def allocation_cycle_task():
    """Background task for periodic allocation cycles"""
    config = app_state["config"]
    allocation_engine = app_state["allocation_engine"]

    while True:
        try:
            start_time = time.time()
            app_state["last_cycle_time"] = start_time

            # Run allocation cycle
            await allocation_engine.run_allocation_cycle()

            duration = time.time() - start_time
            app_state["last_cycle_duration"] = duration

            logging.debug(f"Allocation cycle completed in {duration:.2f}s")

        except Exception as e:
            logging.error(f"Allocation cycle failed: {e}")
            app_state["health_status"] = "degraded"

        # Wait for next cycle
        await asyncio.sleep(config.global_settings.update_interval)


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint"""
    uptime = time.time() - app_state["start_time"]
    config = app_state.get("config")

    health_data = {
        "status": app_state.get("health_status", "unknown"),
        "uptime_seconds": round(uptime, 1),
        "version": "0.1.0",
        "last_cycle_time": app_state.get("last_cycle_time"),
        "last_cycle_duration": app_state.get("last_cycle_duration"),
    }

    if config:
        health_data.update(
            {
                "rollout_percentage": config.global_settings.rollout_percentage,
                "update_interval": config.global_settings.update_interval,
            }
        )

    # Add allocation engine stats if available
    if allocation_engine := app_state.get("allocation_engine"):
        stats = allocation_engine.get_stats()
        health_data.update(
            {
                "active_torrents": stats.get("active_torrents", 0),
                "managed_torrents": stats.get("managed_torrents", 0),
                "api_calls_last_cycle": stats.get("api_calls_last_cycle", 0),
            }
        )

    return health_data


@app.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """Get detailed statistics"""
    if not (allocation_engine := app_state.get("allocation_engine")):
        raise HTTPException(status_code=503, detail="Service not ready")

    return allocation_engine.get_detailed_stats()


@app.get("/stats/trackers")
async def get_tracker_stats() -> Dict[str, Any]:
    """Get per-tracker statistics"""
    if not (allocation_engine := app_state.get("allocation_engine")):
        raise HTTPException(status_code=503, detail="Service not ready")

    return allocation_engine.get_tracker_stats()


@app.post("/webhook")
async def webhook_endpoint(request: Request, background_tasks: BackgroundTasks):
    """Webhook endpoint for qBittorrent events"""
    if not (webhook_handler := app_state.get("webhook_handler")):
        return JSONResponse(
            {"status": "error", "message": "Service not ready"}, status_code=503
        )

    return await webhook_handler.handle_webhook(request)


@app.post("/cycle/force")
async def force_cycle():
    """Force immediate allocation cycle"""
    if not (allocation_engine := app_state.get("allocation_engine")):
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        start_time = time.time()
        await allocation_engine.run_allocation_cycle()
        duration = time.time() - start_time

        return {
            "status": "completed",
            "duration_seconds": round(duration, 2),
            "timestamp": time.time(),
        }
    except Exception as e:
        logging.error(f"Force cycle failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rollback")
async def rollback_changes(request: Request):
    """Rollback all changes"""
    if not (rollback_manager := app_state.get("rollback_manager")):
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        # Parse request body
        body = await request.json()
        confirm = body.get("confirm", False)
        reason = body.get("reason", "Manual rollback")

        if not confirm:
            raise HTTPException(
                status_code=400,
                detail="Rollback requires confirmation. Set 'confirm': true in request body",
            )

        start_time = time.time()
        changes_count = await rollback_manager.rollback_all_changes(reason)
        duration = time.time() - start_time

        logging.warning(
            f"Rollback completed: {changes_count} changes reversed, reason: {reason}"
        )

        return {
            "status": "completed",
            "changes_reversed": changes_count,
            "duration_seconds": round(duration, 2),
            "reason": reason,
            "timestamp": time.time(),
        }

    except HTTPException:
        # Preserve explicit HTTP errors
        raise
    except Exception as e:
        logging.error(f"Rollback failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rollout")
async def update_rollout_percentage(request: Request):
    """Update rollout percentage"""
    try:
        body = await request.json()
        percentage = body.get("percentage")

        if (
            percentage is None
            or not isinstance(percentage, int)
            or not 1 <= percentage <= 100
        ):
            raise HTTPException(
                status_code=400,
                detail="Percentage must be an integer between 1 and 100",
            )

        # Update config
        config = app_state.get("config")
        if config:
            config.global_settings.rollout_percentage = percentage
            logging.info(f"Rollout percentage updated to {percentage}%")

        return {
            "status": "updated",
            "rollout_percentage": percentage,
            "timestamp": time.time(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Rollout update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config")
async def get_config():
    """Get current configuration (sanitized)"""
    config = app_state.get("config")
    if not config:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Return sanitized config (without passwords)
    # Use Pydantic v2 API to avoid deprecation warnings
    config_dict = config.model_dump()
    if "qbittorrent" in config_dict:
        config_dict["qbittorrent"]["password"] = "***"
    if "cross_seed" in config_dict and config_dict["cross_seed"].get("api_key"):
        config_dict["cross_seed"]["api_key"] = "***"

    return config_dict


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Qguardarr",
        "version": "0.1.0",
        "description": "qBittorrent per-tracker upload speed limiter",
        "status": app_state.get("health_status", "unknown"),
        "endpoints": {
            "health": "/health",
            "stats": "/stats",
            "webhook": "/webhook",
            "config": "/config",
        },
    }


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/qguardarr.log")],
    )

    # Ensure directories exist
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    # Get configuration
    try:
        config_loader = ConfigLoader()
        config = config_loader.load_config()

        # Run the application
        uvicorn.run(
            "src.main:app",
            host=config.global_settings.host,
            port=config.global_settings.port,
            reload=False,  # Disable in production
            log_level="info",
        )
    except Exception as e:
        logging.error(f"Failed to start application: {e}")
        exit(1)
