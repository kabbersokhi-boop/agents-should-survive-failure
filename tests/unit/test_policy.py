from agents_should_survive_failure.policy import deterministic_embedding


def test_deterministic_embedding_is_stable_and_normalized() -> None:
    first = deterministic_embedding("vendor approval policy")
    second = deterministic_embedding("vendor approval policy")

    assert first == second
    assert len(first) == 8
    assert round(sum(value * value for value in first), 8) == 1.0
