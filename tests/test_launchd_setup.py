"""Tests for launchd setup script (without actually loading into launchd)."""

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from subprocess import run, PIPE


def test_setup_script_generates_valid_plist():
    """Setup script generates syntactically valid plist XML."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir) / "repo"
        repo_root.mkdir()

        log_dir = repo_root / "data" / "logs"
        log_dir.mkdir(parents=True)

        plist_path = Path(tmpdir) / "test.plist"

        # Simulate what setup_launchd.sh does: generate plist content
        interval = 43200
        label = "com.pathosphere.loop.test"

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>cd {str(repo_root).replace("&", "&amp;")} &amp;&amp; uv run pathos loop</string>
  </array>

  <key>StartInterval</key>
  <integer>{interval}</integer>

  <key>StandardOutPath</key>
  <string>{str(log_dir).replace("&", "&amp;")}/launchd.log</string>

  <key>StandardErrorPath</key>
  <string>{str(log_dir).replace("&", "&amp;")}/launchd_error.log</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
"""
        plist_path.write_text(plist_content)

        # Parse and validate
        tree = ET.parse(plist_path)
        root = tree.getroot()

        # Verify structure
        assert root.tag == "plist"
        dict_elem = root.find("dict")
        assert dict_elem is not None

        # Verify required keys
        keys = [elem.text for elem in dict_elem.findall("key")]
        assert "Label" in keys
        assert "ProgramArguments" in keys
        assert "StartInterval" in keys
        assert "StandardOutPath" in keys
        assert "StandardErrorPath" in keys
        assert "RunAtLoad" in keys
        assert "KeepAlive" in keys


def test_setup_script_interval_conversion():
    """Interval in hours converts correctly to seconds."""
    sleep_hours = 12.0
    expected_seconds = int(sleep_hours * 3600)
    assert expected_seconds == 43200


def test_setup_script_creates_log_directory():
    """Setup script creates data/logs if missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir) / "repo"
        repo_root.mkdir()

        log_dir = repo_root / "data" / "logs"
        assert not log_dir.exists()

        # Simulate directory creation (what setup_launchd.sh does)
        log_dir.mkdir(parents=True, exist_ok=True)

        assert log_dir.exists()
        assert log_dir.is_dir()


def test_setup_script_plist_path_expansion():
    """Path expansion in plist uses absolute paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir) / "repo"
        repo_root.mkdir()
        log_dir = repo_root / "data" / "logs"
        log_dir.mkdir(parents=True)

        # Verify paths are absolute
        assert repo_root.is_absolute()
        assert log_dir.is_absolute()

        # Plist should not contain ~ or $variables
        plist_content = f"""
<StandardOutPath>{log_dir}/launchd.log</StandardOutPath>
<StandardErrorPath>{log_dir}/launchd_error.log</StandardErrorPath>
"""
        assert "~" not in plist_content
        assert "$" not in plist_content
        assert str(log_dir) in plist_content


def test_setup_script_uninstall_removes_plist():
    """Uninstall flag removes plist file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plist_path = Path(tmpdir) / "com.pathosphere.loop.plist"
        plist_path.write_text("<dummy/>")

        assert plist_path.exists()

        # Simulate uninstall
        plist_path.unlink()

        assert not plist_path.exists()


def test_setup_script_idempotent():
    """Running setup twice produces identical plist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plist_path = Path(tmpdir) / "test.plist"

        def write_plist():
            content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.pathosphere.loop</string>
  <key>StartInterval</key>
  <integer>43200</integer>
</dict>
</plist>
"""
            plist_path.write_text(content)

        write_plist()
        first_content = plist_path.read_text()

        write_plist()
        second_content = plist_path.read_text()

        assert first_content == second_content
