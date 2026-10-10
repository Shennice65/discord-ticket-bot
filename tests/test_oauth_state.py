from utils.oauth_state import sign_state, verify_state

SECRET = "s3cret"


def test_round_trip():
    state = sign_state(442188857014747136, SECRET, now=1000)
    assert verify_state(state, SECRET, now=1100) == 442188857014747136


def test_expired_state_is_rejected():
    state = sign_state(1, SECRET, now=1000, ttl=60)
    assert verify_state(state, SECRET, now=1061) is None


def test_tampered_discord_id_is_rejected():
    state = sign_state(111, SECRET, now=1000)
    forged = state.replace("111.", "222.", 1)
    assert verify_state(forged, SECRET, now=1001) is None


def test_wrong_secret_and_garbage_rejected():
    state = sign_state(1, SECRET, now=1000)
    assert verify_state(state, "other", now=1001) is None
    for bad in ("", "123", "1.2", "a.b.c", "1.9999999999.", "x" * 200):
        assert verify_state(bad, SECRET, now=1) is None


def test_legacy_unsigned_discord_id_is_rejected():
    assert verify_state("442188857014747136", SECRET) is None


def test_resolve_prefers_dedicated_secret_over_webhook_secret():
    from utils.oauth_state import resolve_state_secret

    env = {"ROBLOX_WEBHOOK_SECRET": "hook"}
    doc = {"ROBLOX_OAUTH_STATE_SECRET": "state"}
    assert resolve_state_secret(env, doc) == "state"  # Mongo dedicated beats env webhook fallback
    assert resolve_state_secret({"ROBLOX_OAUTH_STATE_SECRET": "e", **env}, doc) == "e"


def test_resolve_falls_back_and_fails_closed():
    from utils.oauth_state import resolve_state_secret

    assert resolve_state_secret({"ROBLOX_WEBHOOK_SECRET": "hook"}, None) == "hook"
    assert resolve_state_secret({}, {"ROBLOX_WEBHOOK_SECRET": "mongo-hook"}) == "mongo-hook"
    assert resolve_state_secret({}, {}) == ""
