import pytest

from core.governance import Actor, Governance, GovernanceError


@pytest.fixture
def actor():
    return Actor("alice", "Alice", "qq:GroupMessage:12", "aiocqhttp")


def test_roles_and_capabilities(actor):
    governance = Governance({"permissions": {"creator_ids": ["qq:alice"], "viewer_ids": ["eve"]}})
    guest = Actor("eve", session=actor.session)
    assert governance.require(actor, "create") == "creator"
    assert governance.require(guest, "read") == "viewer"
    with pytest.raises(GovernanceError) as exc:
        governance.require(guest, "create")
    assert exc.value.code == "role_denied"
    assert governance.require(Actor.panel(), "admin") == "admin"


def test_raw_account_id_cannot_impersonate_scoped_grant():
    gov = Governance({"permissions": {"creator_ids": ["qq:alice"]}})
    actor = Actor("qq:alice", session="other:GroupMessage:12")
    assert gov.role(actor) == "viewer"


@pytest.mark.parametrize(
    ("prefix", "session", "allowed"),
    [
        ("qq:GroupMessage:12", "qq:GroupMessage:12", True),
        ("qq:GroupMessage:12", "qq:GroupMessage:123", False),
        ("qq:GroupMessage:12", "qq:GroupMessage:12:thread", False),
        ("qq:GroupMessage:12:thread", "qq:GroupMessage:12:thread", True),
        ("qq:GroupMessage:", "qq:GroupMessage:123", True),
        ("qq:GroupMessage", "qq:FriendMessage:12", False),
        ("qq", "qq2:GroupMessage:12", False),
        ("qq:", "qq:GroupMessage:123", True),
        ("qq", "qq:GroupMessage:", False),
    ],
)
def test_allowlist_respects_umo_boundaries(prefix, session, allowed):
    gov = Governance({"permissions": {"allowed_sessions": [prefix]}})
    assert (gov.role(Actor("alice", session=session)) == "creator") is allowed


@pytest.mark.parametrize("capability", ["read", "control", "remove", "export"])
def test_task_ownership_protects_both_sender_and_session(actor, capability):
    gov = Governance({})
    task = {"sender_id": actor.id, "umo": actor.session, "status": "complete"}
    gov.authorize_task(actor, task, capability=capability)
    for outsider in (
        Actor("bob", session=actor.session),
        Actor("alice", session="qq:GroupMessage:13"),
        Actor("alice", session="qq2:GroupMessage:12"),
    ):
        with pytest.raises(GovernanceError) as denied:
            gov.authorize_task(outsider, task, capability=capability)
        assert denied.value.code == "not_owner"
    gov.authorize_task(Actor.panel(), task, capability=capability)


def test_legacy_tasks_cannot_touch_mio(actor):
    gov = Governance({})
    record = {"sender_id": actor.id, "umo": actor.session, "status": "legacy"}
    gov.authorize_task(actor, record, capability="read")
    gov.authorize_task(actor, record, capability="remove")
    for principal in (actor, Actor.panel()):
        with pytest.raises(GovernanceError) as exc:
            gov.authorize_task(principal, record)
        assert exc.value.code == "legacy_task"
        assert exc.value.status == 409


def test_group_owner_configuration_is_session_bound(actor):
    gov = Governance({"permissions": {"viewer_ids": [actor.id]}})
    group_owner = Actor(actor.id, session=actor.session, is_session_admin=True)
    gov.authorize_session_config(group_owner, actor.session)
    with pytest.raises(GovernanceError):
        gov.authorize_session_config(group_owner, "qq:GroupMessage:other")
    with pytest.raises(GovernanceError):
        gov.authorize_session_config(actor, actor.session)
    with pytest.raises(GovernanceError):
        gov.authorize_session_config(
            Actor("alice", session="qq:FriendMessage:alice", is_session_admin=True),
            "qq:FriendMessage:alice",
        )


def test_admin_only_override(actor):
    gov = Governance({"permissions": {"admin_only": True}})
    with pytest.raises(GovernanceError) as exc:
        gov.require(actor, "read")
    assert exc.value.code == "session_denied"
    assert gov.role(Actor.panel()) == "admin"


def test_cloud_allowlist_is_adapter_qualified(actor):
    gov = Governance({"permissions": {"cloud_allowed_ids": ["qq:alice"]}})
    assert gov.policy(actor, cloud=True).cloud is True
    assert gov.policy(actor).cloud is False
    for outsider in (
        Actor("bob", session=actor.session),
        Actor("alice", session="qq2:GroupMessage:12", platform="aiocqhttp"),
    ):
        with pytest.raises(GovernanceError) as exc:
            gov.policy(outsider, cloud=True)
        assert exc.value.code == "cloud_denied"
    assert Governance({}).policy(Actor.panel(), cloud=True).cloud


def test_keyword_filter_normalizes_unicode_and_never_echoes_prompt(actor):
    gov = Governance({"governance": {"blocked_keywords": ["secret", "禁语"]}})
    for text in ("SECREＴ 藏在这里", "这里有禁语"):
        with pytest.raises(GovernanceError) as exc:
            gov.policy(actor, text=text)
        assert exc.value.code == "blocked_keyword"
        assert text not in str(exc.value)
    assert gov.policy(actor, text="一页关于旅行的漫画")


def test_quota_policy_bounds_config_and_reads_changes(actor):
    config = {
        "governance": {
            "global_limit": -9,
            "user_active_limit": "4",
            "daily_limit": 12,
            "cloud_daily_limit": 8,
            "cooldown_seconds": 20,
        }
    }
    gov = Governance(config)
    assert gov.global_limit == 1
    policy = gov.policy(actor)
    assert policy.user_active_limit == 4
    assert policy.daily_limit == 12
    assert policy.cloud_daily_limit == 8
    assert policy.cooldown_seconds == 20
    config["governance"]["daily_limit"] = 50
    assert gov.policy(actor).daily_limit == 50
