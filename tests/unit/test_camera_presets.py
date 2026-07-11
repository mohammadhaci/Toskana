"""Unit tests for the wizard's vendor RTSP URL builder."""

from __future__ import annotations

import pytest

from toskana.camera_presets import (
    PRESETS,
    build_rtsp_url,
    get_preset,
    mask_url_password,
)


class TestBuildRtspUrl:
    def test_hikvision_main_channel_is_one_based(self) -> None:
        url = build_rtsp_url("hikvision", "192.168.1.20", "admin", "pw")
        assert url == "rtsp://admin:pw@192.168.1.20:554/Streaming/Channels/101"

    def test_hikvision_nvr_channel_three(self) -> None:
        url = build_rtsp_url("hikvision", "192.168.1.20", "admin", "pw", channel=3)
        assert url.endswith("/Streaming/Channels/301")

    def test_hikvision_substream(self) -> None:
        url = build_rtsp_url("hikvision_sub", "10.0.0.5", "admin", "pw", channel=2)
        assert url.endswith("/Streaming/Channels/202")

    def test_dahua_main_and_sub(self) -> None:
        main = build_rtsp_url("dahua", "192.168.1.30", "admin", "pw", channel=4)
        assert main.endswith("/cam/realmonitor?channel=4&subtype=0")
        sub = build_rtsp_url("dahua_sub", "192.168.1.30", "admin", "pw", channel=4)
        assert sub.endswith("/cam/realmonitor?channel=4&subtype=1")

    def test_reolink_zero_pads_the_channel(self) -> None:
        url = build_rtsp_url("reolink", "cam.local", "admin", "pw", channel=2)
        assert url.endswith("/h264Preview_02_main")
        assert build_rtsp_url("reolink", "cam.local", "a", "b", channel=12).endswith(
            "/h264Preview_12_main"
        )

    @pytest.mark.parametrize(
        ("key", "path"),
        [
            ("tplink", "/stream1"),
            ("uniview", "/media/video1"),
            ("axis", "/axis-media/media.amp"),
        ],
    )
    def test_channelless_vendors(self, key: str, path: str) -> None:
        url = build_rtsp_url(key, "192.168.0.9", "user", "pw")
        assert url == f"rtsp://user:pw@192.168.0.9:554{path}"

    def test_custom_port(self) -> None:
        url = build_rtsp_url("tplink", "192.168.0.9", "u", "p", port=8554)
        assert "@192.168.0.9:8554/" in url

    def test_password_special_chars_are_encoded(self) -> None:
        url = build_rtsp_url("hikvision", "192.168.1.20", "admin", "p@ss:w/rd?#")
        assert "p%40ss%3Aw%2Frd%3F%23" in url
        assert "p@ss" not in url

    def test_username_special_chars_are_encoded(self) -> None:
        url = build_rtsp_url("dahua", "192.168.1.30", "user@site", "pw")
        assert url.startswith("rtsp://user%40site:pw@")

    def test_empty_credentials_drop_the_at_part(self) -> None:
        url = build_rtsp_url("tplink", "192.168.0.9")
        assert url == "rtsp://192.168.0.9:554/stream1"

    def test_password_without_username_rejected(self) -> None:
        with pytest.raises(ValueError, match="username"):
            build_rtsp_url("tplink", "192.168.0.9", "", "secret")

    def test_ip_is_stripped(self) -> None:
        url = build_rtsp_url("axis", "  192.168.0.9  ", "u", "p")
        assert "@192.168.0.9:" in url

    @pytest.mark.parametrize("bad_ip", ["", "   ", "host/path", "a@b", "1.2.3.4:554", "a b"])
    def test_invalid_host_rejected(self, bad_ip: str) -> None:
        with pytest.raises(ValueError):
            build_rtsp_url("hikvision", bad_ip, "u", "p")

    @pytest.mark.parametrize("bad_port", [0, -1, 65536])
    def test_invalid_port_rejected(self, bad_port: int) -> None:
        with pytest.raises(ValueError, match="port"):
            build_rtsp_url("hikvision", "192.168.1.1", "u", "p", port=bad_port)

    @pytest.mark.parametrize("bad_channel", [0, -3])
    def test_invalid_channel_rejected(self, bad_channel: int) -> None:
        with pytest.raises(ValueError, match="channel"):
            build_rtsp_url("hikvision", "192.168.1.1", "u", "p", channel=bad_channel)

    def test_unknown_preset_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown camera preset"):
            build_rtsp_url("nosuchvendor", "192.168.1.1", "u", "p")

    def test_generic_preset_takes_no_fields(self) -> None:
        with pytest.raises(ValueError, match="manual URL"):
            build_rtsp_url("generic", "192.168.1.1", "u", "p")


class TestPresets:
    def test_needs_channel_flags(self) -> None:
        by_key = {preset.key: preset for preset in PRESETS}
        for key in ("hikvision", "hikvision_sub", "dahua", "dahua_sub", "reolink"):
            assert by_key[key].needs_channel, key
        for key in ("tplink", "uniview", "axis", "generic"):
            assert not by_key[key].needs_channel, key

    def test_default_port_is_rtsp(self) -> None:
        assert all(preset.default_port == 554 for preset in PRESETS)

    def test_get_preset_unknown(self) -> None:
        with pytest.raises(ValueError):
            get_preset("bogus")


class TestMaskUrlPassword:
    def test_masks_the_password_only(self) -> None:
        masked = mask_url_password("rtsp://admin:s3cret@192.168.1.20:554/x")
        assert masked == "rtsp://admin:•••@192.168.1.20:554/x"

    def test_masks_encoded_passwords(self) -> None:
        masked = mask_url_password("rtsp://admin:p%40ss@10.0.0.1:554/x")
        assert "p%40ss" not in masked
        assert "admin:•••@" in masked

    def test_url_without_credentials_unchanged(self) -> None:
        url = "rtsp://192.168.1.20:554/stream1"
        assert mask_url_password(url) == url

    def test_non_url_unchanged(self) -> None:
        assert mask_url_password("./video.mp4") == "./video.mp4"
