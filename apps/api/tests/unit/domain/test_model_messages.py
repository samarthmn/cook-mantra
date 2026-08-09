from domain import model_runtime


def test_canonical_model_message_hides_image_bytes_from_diagnostics() -> None:
    message_type = getattr(model_runtime, "ModelMessage", None)

    assert callable(message_type)
    message = message_type(
        role="user",
        content="inspect the supplied image",
        image=b"private-image-canary",
        media_type="image/png",
    )
    assert "private-image-canary" not in repr(message)


def test_discovered_model_contract_is_available() -> None:
    discovered_type = getattr(model_runtime, "DiscoveredModel", None)

    assert callable(discovered_type)
