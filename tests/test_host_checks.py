from qhv.host_checks import parse_feature_state


def test_parse_feature_state_returns_enabled() -> None:
    output = """
Feature Name : HypervisorPlatform
State : Enabled
"""
    assert parse_feature_state(output) == "Enabled"


def test_parse_feature_state_handles_localized_estado() -> None:
    output = """
Nombre de la característica : HypervisorPlatform
Estado : Habilitado
"""
    assert parse_feature_state(output) == "Enabled"


def test_parse_feature_state_returns_none_for_missing_state() -> None:
    assert parse_feature_state("no state here") is None
