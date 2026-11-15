from VoiceClonerAppServer.core.settings_manager import (
    AdRewardsConfig, AppSettings, DefaultAnonymousLimitsConfig, SettingsManager, SecurityConfig,
)

def test_anonymous_defaults_allow_zero_clone_slots():
    cfg = DefaultAnonymousLimitsConfig()
    assert cfg.max_cloned_slots == 0
    assert cfg.daily_tts_char_limit == 50000

def test_ads_defaults_are_disabled():
    ads = AdRewardsConfig()
    assert ads.enabled is False
    assert ads.chars_per_ad == 1000
    assert ads.max_ads_per_user_per_day == 10

def test_update_settings_accepts_anonymous_and_ads_blocks(tmp_path):
    manager = SettingsManager(settings_path=tmp_path / "settings.json")
    updated = manager.update_settings({
        "default_anonymous_limits": {"max_cloned_slots": 0, "daily_tts_char_limit": 12345},
        "ad_rewards": {"enabled": True, "chars_per_ad": 500},
    })
    assert updated.default_anonymous_limits.daily_tts_char_limit == 12345
    assert updated.ad_rewards.enabled is True
    assert updated.ad_rewards.chars_per_ad == 500
