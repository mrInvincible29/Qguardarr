"""Branch tests for allocation engine cycle and active torrents."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig


@pytest.mark.asyncio
async def test_get_active_torrents_exception_path(test_config: QguardarrConfig):
    qbit = AsyncMock()
    qbit.get_torrents.side_effect = RuntimeError("network")
    matcher = Mock()
    rollback = AsyncMock()

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    torrents = await engine._get_active_torrents()
    assert torrents == []


@pytest.mark.asyncio
async def test_run_allocation_cycle_cleanup_branch(test_config: QguardarrConfig):
    qbit = AsyncMock()
    qbit.get_torrents.return_value = []
    matcher = Mock()
    rollback = AsyncMock()

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    # Force cleanup to report >0 so the debug path executes
    engine.cache.cleanup_old_torrents = Mock(return_value=3)

    await engine.run_allocation_cycle()
    engine.cache.cleanup_old_torrents.assert_called()
