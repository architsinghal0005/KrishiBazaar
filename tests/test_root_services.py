"""
Unit tests for root services proxy imports.
Ensures that `services.mandi_service`, `services.weather_service`, and `services.optimizer`
are directly importable as requested in Phase 4 specification.
"""

def test_root_services_proxies_importable():
    import services.mandi_service as root_mandi
    import services.weather_service as root_weather
    import services.optimizer as root_opt

    assert hasattr(root_mandi, "get_mandi_prices")
    assert hasattr(root_mandi, "get_mandi_prices_async")
    assert hasattr(root_mandi, "SAMPLE_BENCHMARK_MANDI_DATA")

    assert hasattr(root_weather, "get_transit_weather")
    assert hasattr(root_weather, "get_transit_weather_async")
    assert hasattr(root_weather, "evaluate_transit_hazard")
    assert hasattr(root_weather, "resolve_coordinates")

    assert hasattr(root_opt, "calculate_channel_net_return")
    assert hasattr(root_opt, "optimize_sales_channels")
