import pytest
from VoiceClonerAppServer.core.settings_manager import ModelWorkerConfig, settings_manager
from VoiceClonerAppServer.services.model_dispatcher import ModelDispatcher


def test_capability_routing_matches_supported_voice():
    dispatcher = ModelDispatcher()

    # Worker 1 supports voice_a and voice_b
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-1:8001",
        supported_voices={"voice_a", "voice_b"},
        supports_cloning=True,
        worker_id="worker-1",
    )
    # Worker 2 supports voice_b and voice_c
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-2:8002",
        supported_voices={"voice_b", "voice_c"},
        supports_cloning=False,
        worker_id="worker-2",
    )

    # Configure active workers in settings_manager
    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({
        "model_workers": [
            {"id": "worker-1", "url": "http://worker-1:8001", "api_key": "k1", "max_concurrent": 2, "is_active": True},
            {"id": "worker-2", "url": "http://worker-2:8002", "api_key": "k2", "max_concurrent": 2, "is_active": True},
        ]
    })

    try:
        # Requesting voice_a should ONLY pick Worker 1
        w_a, url_a = dispatcher._select_worker(voice_id="voice_a")
        assert w_a.id == "worker-1"
        assert url_a == "http://worker-1:8001"
        dispatcher._release_worker(url_a)

        # Requesting voice_c should ONLY pick Worker 2
        w_c, url_c = dispatcher._select_worker(voice_id="voice_c")
        assert w_c.id == "worker-2"
        assert url_c == "http://worker-2:8002"
        dispatcher._release_worker(url_c)

        # Requesting an unsupported voice should return None
        res_none = dispatcher._select_worker(voice_id="unknown_voice_xyz")
        assert res_none is None
    finally:
        # Restore settings
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})


def test_capability_routing_least_active_among_shared_voice():
    dispatcher = ModelDispatcher()

    # Both Worker 1 and Worker 2 support voice_shared
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-1:8001",
        supported_voices={"voice_shared"},
        worker_id="worker-1",
    )
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-2:8002",
        supported_voices={"voice_shared"},
        worker_id="worker-2",
    )

    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({
        "model_workers": [
            {"id": "worker-1", "url": "http://worker-1:8001", "api_key": "k1", "max_concurrent": 2, "is_active": True},
            {"id": "worker-2", "url": "http://worker-2:8002", "api_key": "k2", "max_concurrent": 2, "is_active": True},
        ]
    })

    try:
        # Simulate Worker 1 is busy with 2 active jobs
        stat_1 = dispatcher._get_worker_stat("http://worker-1:8001")
        stat_1["active_jobs"] = 2

        stat_2 = dispatcher._get_worker_stat("http://worker-2:8002")
        stat_2["active_jobs"] = 0

        # Should pick Worker 2 because it is least active
        chosen_w, chosen_url = dispatcher._select_worker(voice_id="voice_shared")
        assert chosen_w.id == "worker-2"
        assert chosen_url == "http://worker-2:8002"
        dispatcher._release_worker(chosen_url)
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})


def test_capability_routing_for_cloning():
    dispatcher = ModelDispatcher()

    # Worker 1 supports cloning; Worker 2 does NOT
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-1:8001",
        supported_voices={"v1"},
        supports_cloning=True,
        worker_id="worker-1",
    )
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-2:8002",
        supported_voices={"v1"},
        supports_cloning=False,
        worker_id="worker-2",
    )

    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({
        "model_workers": [
            {"id": "worker-1", "url": "http://worker-1:8001", "api_key": "k1", "max_concurrent": 2, "is_active": True},
            {"id": "worker-2", "url": "http://worker-2:8002", "api_key": "k2", "max_concurrent": 2, "is_active": True},
        ]
    })

    try:
        # Requesting clone synthesis or embedding should route to Worker 1
        chosen_w, chosen_url = dispatcher._select_worker(is_clone=True)
        assert chosen_w.id == "worker-1"
        assert chosen_url == "http://worker-1:8001"
        dispatcher._release_worker(chosen_url)
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})


def test_select_worker_skips_full_workers():
    dispatcher = ModelDispatcher()
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-1:8001",
        supported_voices={"v1"},
        worker_id="worker-1",
    )
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-2:8002",
        supported_voices={"v1"},
        worker_id="worker-2",
    )

    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({
        "model_workers": [
            {"id": "worker-1", "url": "http://worker-1:8001", "api_key": "k1", "max_concurrent": 2, "is_active": True},
            {"id": "worker-2", "url": "http://worker-2:8002", "api_key": "k2", "max_concurrent": 2, "is_active": True},
        ]
    })

    try:
        # Worker 1 is at full capacity (2 active jobs / 2 max_concurrent)
        dispatcher._get_worker_stat("http://worker-1:8001")["active_jobs"] = 2
        dispatcher._get_worker_stat("http://worker-2:8002")["active_jobs"] = 0

        # Must pick Worker 2
        chosen_w, chosen_url = dispatcher._select_worker(voice_id="v1")
        assert chosen_w.id == "worker-2"
        dispatcher._release_worker(chosen_url)

        # If both are full, must return None
        dispatcher._get_worker_stat("http://worker-2:8002")["active_jobs"] = 2
        res = dispatcher._select_worker(voice_id="v1")
        assert res is None
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})


def test_select_worker_by_utilization_ratio():
    dispatcher = ModelDispatcher()
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-1:8001",
        supported_voices={"v1"},
        worker_id="worker-1",
    )
    dispatcher.register_worker_capabilities(
        worker_url="http://worker-2:8002",
        supported_voices={"v1"},
        worker_id="worker-2",
    )

    original_workers = list(settings_manager.get_settings().model_workers)
    settings_manager.update_settings({
        "model_workers": [
            # Worker 1 has capacity 8, active 3 -> 37.5% utilization
            {"id": "worker-1", "url": "http://worker-1:8001", "api_key": "k1", "max_concurrent": 8, "is_active": True},
            # Worker 2 has capacity 4, active 2 -> 50.0% utilization
            {"id": "worker-2", "url": "http://worker-2:8002", "api_key": "k2", "max_concurrent": 4, "is_active": True},
        ]
    })

    try:
        dispatcher._get_worker_stat("http://worker-1:8001")["active_jobs"] = 3
        dispatcher._get_worker_stat("http://worker-2:8002")["active_jobs"] = 2

        # Worker 1 has lower utilization ratio (3/8 = 0.375 < 2/4 = 0.5)
        # Even though worker 1 has MORE active jobs (3 > 2), it should be chosen!
        chosen_w, chosen_url = dispatcher._select_worker(voice_id="v1")
        assert chosen_w.id == "worker-1"
        assert chosen_url == "http://worker-1:8001"
        dispatcher._release_worker(chosen_url)
    finally:
        settings_manager.update_settings({"model_workers": [w.model_dump() for w in original_workers]})

