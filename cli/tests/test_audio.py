"""Tests for audio capture module."""

import pytest

from dalston_cli.audio import MicrophoneStream, resolve_device


def test_list_devices():
    """Test listing audio devices."""
    # This should not fail even with no audio devices
    devices = MicrophoneStream.list_devices()
    assert isinstance(devices, list)


def test_get_default_device():
    """Test getting default device."""
    # This may return None if no default device
    device = MicrophoneStream.get_default_device()
    assert device is None or isinstance(device, dict)


def test_resolve_device_numeric():
    """Test resolving device by numeric index."""
    assert resolve_device("0") == 0
    assert resolve_device("5") == 5


def test_resolve_device_invalid():
    """Test resolving invalid device name."""
    with pytest.raises(ValueError, match="Device not found"):
        resolve_device("nonexistent_device_xyz_123")
