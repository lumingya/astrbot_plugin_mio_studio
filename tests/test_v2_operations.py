import pytest

from core.v2_operations import OPS, operation_path


@pytest.mark.parametrize(
    ("operation", "method", "path"),
    [
        ("fetch_models", "POST", "/settings/models"),
        ("order_queue", "PUT", "/workshop/queue/order"),
        ("adopt_take", "POST", "/episodes/{episode_id}/takes/{take_id}/adopt"),
        ("qa_episode", "POST", "/episodes/{episode_id}/qa"),
        ("add_panel", "POST", "/episodes/{episode_id}/panels"),
        ("download_export", "GET", "/export/jobs/{job_id}/file"),
        ("preview_creation_kit", "POST", "/creation-kits/{series_id}/preview"),
    ],
)
def test_routes_match_published_protocol(operation, method, path):
    assert OPS[operation] == (method, path)


@pytest.mark.parametrize("value", ["", "..", "a/b", "a\\b", "%2e%2e", "\x00"])
def test_unsafe_identifiers_are_rejected(value):
    with pytest.raises(ValueError):
        operation_path("get_job", {"job_id": value})


def test_identifiers_are_encoded_as_one_segment():
    assert operation_path("get_job", {"job_id": "分镜 #1?"}) == (
        "/jobs/%E5%88%86%E9%95%9C%20%231%3F"
    )
