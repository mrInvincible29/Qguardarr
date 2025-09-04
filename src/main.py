"""Main FastAPI application for Qguardarr"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.allocation import AllocationEngine
from src.config import ConfigLoader
from src.qbit_client import QBittorrentClient
from src.rollback import RollbackManager
from src.tracker_matcher import TrackerMatcher
from src.utils.logging_setup import setup_logging
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
    version="0.3.2",
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
    asyncio.create_task(config_watcher_task())

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


async def _apply_new_config(new_config) -> None:
    """Apply a freshly loaded configuration to running components."""
    # Update tracker matcher
    if matcher := app_state.get("tracker_matcher"):
        try:
            matcher.update_tracker_configs(new_config.trackers)
        except Exception as e:
            logging.warning(f"Failed updating tracker matcher: {e}")

    # Update allocation engine config and rollout/dry-run knobs
    if engine := app_state.get("allocation_engine"):
        try:
            engine.config = new_config
            engine.update_rollout_percentage(
                new_config.global_settings.rollout_percentage
            )
            engine.dry_run = bool(getattr(new_config.global_settings, "dry_run", False))
            if engine.dry_run:
                from src.dry_run_store import DryRunStore

                engine.dry_run_store = DryRunStore(
                    new_config.global_settings.dry_run_store_path
                )
            else:
                engine.dry_run_store = None
        except Exception as e:
            logging.warning(f"Failed updating allocation engine: {e}")

    # Update webhook handler/cross-seed settings
    if wh := app_state.get("webhook_handler"):
        try:
            wh.config = new_config
            if wh.cross_seed_forwarder:
                wh.cross_seed_forwarder.config = new_config
        except Exception as e:
            logging.warning(f"Failed updating webhook handler: {e}")


async def config_watcher_task(poll_interval: float = 2.0):
    """Poll the config file and hot-reload on change (mtime-based)."""
    loader = app_state.get("config_loader")
    if not loader:
        return
    path = loader.config_path
    last_mtime = None
    try:
        if path.exists():
            last_mtime = path.stat().st_mtime
    except Exception:
        last_mtime = None

    logging.info(f"Config watcher started for {path}")
    while True:
        try:
            await asyncio.sleep(poll_interval)
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            if last_mtime is None:
                last_mtime = mtime
                continue
            if mtime != last_mtime:
                logging.info("Config file change detected; reloading")
                last_mtime = mtime
                try:
                    new_cfg = loader.reload_config()
                    app_state["config"] = new_cfg
                    await _apply_new_config(new_cfg)
                    logging.info("Config hot-reload applied")
                except Exception as e:
                    logging.error(f"Config reload failed: {e}")
        except Exception as e:
            logging.warning(f"Config watcher error: {e}")


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint"""
    uptime = time.time() - app_state["start_time"]
    config = app_state.get("config")

    health_data = {
        "status": app_state.get("health_status", "unknown"),
        "uptime_seconds": round(uptime, 1),
        "version": "0.3.2",
        "last_cycle_time": app_state.get("last_cycle_time"),
        "last_cycle_duration": app_state.get("last_cycle_duration"),
    }

    if config:
        health_data.update(
            {
                "rollout_percentage": config.global_settings.rollout_percentage,
                "update_interval": config.global_settings.update_interval,
                "dry_run": getattr(config.global_settings, "dry_run", False),
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
    """Rollback all changes and restore original per-torrent limits"""
    if not (rollback_manager := app_state.get("rollback_manager")):
        raise HTTPException(status_code=503, detail="Service not ready")
    if not (qbit_client := app_state.get("qbit_client")):
        raise HTTPException(status_code=503, detail="qBittorrent client not ready")

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

        # Get original limits to restore
        original_limits = await rollback_manager.get_rollback_data_for_application()

        # Apply original limits back to qBittorrent in batches
        changes_count = 0
        if original_limits:
            await qbit_client.set_torrents_upload_limits_batch(original_limits)
            changes_count = len(original_limits)

            # Mark entries restored
            await rollback_manager.mark_entries_restored(list(original_limits.keys()))

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


@app.get("/preview/next-cycle")
async def preview_next_cycle():
    """Preview proposed tracker caps and torrent-level changes without applying"""
    allocation_engine = app_state.get("allocation_engine")
    if not allocation_engine:
        raise HTTPException(status_code=503, detail="Service not ready")

    # If the engine provides a preview method, use it directly
    preview_method = getattr(allocation_engine, "preview_next_cycle", None)
    if callable(preview_method):
        try:
            return await preview_method()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Fallback: minimal placeholder if not implemented
    return {
        "status": "unimplemented",
        "message": "Engine does not implement preview_next_cycle",
    }


@app.post("/smoothing/reset")
async def reset_smoothing(request: Request):
    """Reset Phase 3 smoothing state for a tracker or all."""
    allocation_engine = app_state.get("allocation_engine")
    if not allocation_engine:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        body = await request.json()
    except Exception:
        body = {}

    tracker_id = body.get("tracker_id")
    reset_all = bool(body.get("all"))

    try:
        cfg = app_state.get("config")
        strategy = cfg.global_settings.allocation_strategy if cfg else "equal"
        if reset_all:
            cleared = allocation_engine.reset_smoothing(None)
            trackers = "all"
        else:
            cleared = allocation_engine.reset_smoothing(tracker_id)
            trackers = tracker_id or ""

        resp = {
            "status": "ok",
            "cleared_count": cleared,
            "tracker": trackers,
            "strategy": strategy,
            "timestamp": time.time(),
        }
        if strategy != "soft":
            resp["message"] = "Strategy is not 'soft'; smoothing state may be unused."
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/limits/reset")
async def reset_limits(request: Request):
    """Set upload limits to unlimited (-1) for torrents previously touched by Qguardarr.

    Works in both dry-run and real modes.
    Body:
      {"confirm": true, "scope": "unrestored"|"all", "include_restored": false}
    """
    allocation_engine = app_state.get("allocation_engine")
    rollback_manager = app_state.get("rollback_manager")
    qbit_client = app_state.get("qbit_client")
    if not allocation_engine or not rollback_manager:
        raise HTTPException(status_code=503, detail="Service not ready")

    try:
        body = await request.json()
    except Exception:
        body = {}

    confirm = bool(body.get("confirm"))
    scope = body.get("scope", "unrestored")  # or "all"
    if not confirm:
        raise HTTPException(
            status_code=400, detail="Confirmation required: {'confirm': true}"
        )

    try:
        # Determine affected hashes
        include_restored = True if scope == "all" else False
        hashes = await rollback_manager.get_distinct_hashes(
            include_restored=include_restored
        )
        if not hashes:
            return {
                "status": "ok",
                "count": 0,
                "mode": "dry-run" if allocation_engine.dry_run else "real",
            }

        # Dry-run path: persist -1 in store and update cache
        if allocation_engine.dry_run and allocation_engine.dry_run_store:
            updates = {h: -1 for h in hashes}
            allocation_engine.dry_run_store.set_many(updates)
            # Update cache too
            for h in hashes:
                idx = allocation_engine.cache.hash_to_index.get(h)
                if idx is not None:
                    allocation_engine.cache.current_limits[idx] = -1
            # Optionally mark entries restored in rollback DB when requested
            if bool(body.get("mark_restored")):
                await rollback_manager.mark_entries_restored(hashes)
            return {"status": "ok", "count": len(hashes), "mode": "dry-run"}

        # Real path: set unlimited via qBittorrent in batches
        if not qbit_client:
            raise HTTPException(status_code=503, detail="qBittorrent client not ready")

        updates = {h: -1 for h in hashes}
        await qbit_client.set_torrents_upload_limits_batch(updates)
        # Update cache
        for h in hashes:
            idx = allocation_engine.cache.hash_to_index.get(h)
            if idx is not None:
                allocation_engine.cache.current_limits[idx] = -1

        # Optionally mark entries restored in rollback DB when requested
        if bool(body.get("mark_restored")):
            await rollback_manager.mark_entries_restored(hashes)
        return {"status": "ok", "count": len(hashes), "mode": "real"}

    except HTTPException:
        raise
    except Exception as e:
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


@app.post("/config/reload")
async def reload_config():
    """Reload configuration from disk and apply to running components"""
    config_loader = app_state.get("config_loader")
    if not config_loader:
        raise HTTPException(status_code=503, detail="Config loader not ready")

    try:
        new_config = config_loader.reload_config()
        app_state["config"] = new_config
        await _apply_new_config(new_config)

        return {
            "status": "reloaded",
            "rollout_percentage": new_config.global_settings.rollout_percentage,
            "strategy": new_config.global_settings.allocation_strategy,
        }
    except Exception as e:
        logging.error(f"Config reload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Qguardarr",
        "version": "0.3.2",
        "description": "qBittorrent per-tracker upload speed limiter",
        "status": app_state.get("health_status", "unknown"),
        "endpoints": {
            "health": "/health",
            "stats": "/stats",
            "stats_trackers": "/stats/trackers",
            "webhook": "/webhook",
            "config": "/config",
            "config_reload": "/config/reload",
            "preview_next_cycle": "/preview/next-cycle",
            "smoothing_reset": "/smoothing/reset",
        },
    }


if __name__ == "__main__":
    # Ensure directories exist early
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    # Load configuration first to honor logging settings
    try:
        config_loader = ConfigLoader()
        config = config_loader.load_config()
    except Exception as e:
        # If config fails, set basic logging to console and exit
        setup_logging("INFO", None)
        logging.error(f"Failed to load configuration: {e}")
        exit(1)

    # Setup logging per config; fall back to console-only if file not writable
    # Let Uvicorn own console logging to avoid duplicate lines; we only add file handler
    setup_logging(config.logging.level, config.logging.file, add_stream=False)

    try:
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
