from datetime import timezone

from scripts.gta_shadow import load_policy


def test_shadow_loader_preserves_disabled_safe_defaults(tmp_path):
    config = tmp_path / "gta.yaml"
    config.write_text(
        """enabled: false
live_enabled: false
start_utc: '2026-11-18T00:00:00Z'
end_utc: '2026-11-22T00:00:00Z'
max_total_usd: 9
max_per_token_usd: 3
max_open_positions: 0
keywords: [gta, gta6]
""",
        encoding="utf-8",
    )
    policy = load_policy(config)
    assert policy.enabled is False
    assert policy.max_total_usd == 9
    assert policy.max_per_token_usd == 3
    assert policy.start_utc.tzinfo == timezone.utc
