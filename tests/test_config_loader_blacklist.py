from src.config.config_loader import ConfigLoader


def test_get_symbol_blacklist_merges_trading_and_fund_flow_scopes() -> None:
    runtime_cfg = {
        "trading": {
            "symbols": ["BTCUSDT", "QNTUSDT", "ETHUSDT", "KASUSDT"],
            "symbol_blacklist": ["kasusdt", "uniusdt"],
        },
        "fund_flow": {"symbol_blacklist": ["qntusdt", "XMRUSDT"]},
    }

    assert ConfigLoader.get_symbol_blacklist(runtime_cfg) == ["QNTUSDT", "XMRUSDT", "KASUSDT", "UNIUSDT"]
    assert ConfigLoader.get_trading_symbols(runtime_cfg) == ["BTCUSDT", "ETHUSDT"]
