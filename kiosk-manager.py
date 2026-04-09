#!/usr/bin/env python3
"""
Kiosk Manager — Configure automatic login, app launch, and desktop
suppression on Raspberry Pi OS Bookworm (labwc / Wayfire).

GTK3 settings application for kiosk mode, boot image, and boot sound
configuration on Raspberry Pi OS Bookworm with labwc.
"""

VERSION = "1.1.2"

import gi
gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3
    except (ValueError, ImportError):
        AppIndicator3 = None

from gi.repository import Gtk, Gdk, GLib
import os
import sys
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────

HOME = Path.home()
LABWC_SYS_AUTOSTART  = Path("/etc/xdg/labwc/autostart")
LABWC_USER_AUTOSTART = HOME / ".config" / "labwc" / "autostart"
LIGHTDM_CONF         = Path("/etc/lightdm/lightdm.conf")
BACKUP_DIR           = HOME / ".local" / "share" / "kiosk-manager" / "backups"
SOURCES_DIR          = HOME / ".local" / "share" / "kiosk-manager" / "sources"

APP_DIR      = Path("/opt/kiosk-manager")
LAUNCH_SCRIPT = APP_DIR / "kiosk-launch.sh"
ICON_NAME    = "kiosk-manager"
ICON_PATH    = APP_DIR / "kiosk-manager.svg"
TRAY_AUTOSTART = Path("/etc/xdg/autostart/kiosk-manager.desktop")

# Boot image paths
PLYMOUTH_THEME_DIR = Path("/usr/share/plymouth/themes/custom-boot")
PLYMOUTH_IMAGE     = PLYMOUTH_THEME_DIR / "Boot.png"
PLYMOUTH_SCRIPT    = PLYMOUTH_THEME_DIR / "custom-boot.script"
PLYMOUTH_CONF      = PLYMOUTH_THEME_DIR / "custom-boot.plymouth"
FIRMWARE_SPLASH    = Path("/boot/firmware/splash.png")
CMDLINE_TXT        = Path("/boot/firmware/cmdline.txt")

# Boot sound paths
SOUNDS_DIR       = HOME / "sounds"
BOOTCHIME_WAV    = SOUNDS_DIR / "bootchime.wav"
SOUND_SERVICE    = Path("/etc/systemd/system/startup-sound.service")

# Standard labwc system autostart components
LABWC_KANSHI   = "/usr/bin/kanshi &"
LABWC_XDG      = "/usr/bin/lxsession-xdg-autostart"
LABWC_DESKTOP  = "/usr/bin/lwrespawn /usr/bin/pcmanfm --desktop --profile LXDE-pi &"
LABWC_PANEL    = "/usr/bin/lwrespawn /usr/bin/wf-panel-pi &"

# Image types we accept
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif",
                    ".gif", ".webp"}
SOUND_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac"}


# ── Compositor Detection ─────────────────────────────────────────────

def detect_compositor():
    """Return 'labwc', 'wayfire', or 'unknown'."""
    for name in ("labwc", "wayfire"):
        try:
            out = subprocess.check_output(
                ["pgrep", "-x", name], stderr=subprocess.DEVNULL
            )
            if out.strip():
                return name
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return "unknown"


# ── Auto-login ───────────────────────────────────────────────────────

def is_autologin_enabled():
    """Check if desktop autologin is configured in lightdm."""
    if not LIGHTDM_CONF.exists():
        return False
    try:
        for line in LIGHTDM_CONF.read_text().splitlines():
            line = line.strip()
            if line.startswith("autologin-user=") and not line.startswith("#"):
                return bool(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return False


def set_autologin(enable):
    """Enable or disable desktop autologin via raspi-config."""
    opt = "B4" if enable else "B3"
    try:
        subprocess.run(
            ["sudo", "-n", "raspi-config", "nonint", "do_boot_behaviour", opt],
            check=True, capture_output=True, timeout=30
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as e:
        print(f"[kiosk] raspi-config error: {e}", file=sys.stderr, flush=True)
        return False


def is_tray_enabled():
    """Check if the tray icon autostart is active."""
    return TRAY_AUTOSTART.exists()


def set_tray_enabled(enable):
    """Enable or disable the tray icon autostart."""
    disabled = Path(str(TRAY_AUTOSTART) + ".disabled")
    try:
        if enable and disabled.exists():
            subprocess.run(
                ["sudo", "-n", "mv", str(disabled), str(TRAY_AUTOSTART)],
                check=True, capture_output=True, timeout=10)
        elif not enable and TRAY_AUTOSTART.exists():
            subprocess.run(
                ["sudo", "-n", "mv", str(TRAY_AUTOSTART), str(disabled)],
                check=True, capture_output=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


# ── Read current kiosk state from labwc user autostart ───────────────


def read_kiosk_state():
    """Read current kiosk configuration.

    Returns dict: enabled, app_command, suppress_panel, suppress_desktop,
                  auto_restart, restart_on_quit, hide_cursor.
    """
    state = {
        "enabled": False,
        "app_command": "",
        "suppress_panel": True,
        "suppress_desktop": True,
        "auto_restart": False,
        "restart_on_quit": False,
        "hide_cursor": False,
    }

    # Check if kiosk-launch.sh is in user autostart
    if LABWC_USER_AUTOSTART.exists():
        text = LABWC_USER_AUTOSTART.read_text()
        if "kiosk-launch.sh" in text:
            state["enabled"] = True

    # Read settings from launch script via marker comments
    if state["enabled"] and LAUNCH_SCRIPT.exists():
        try:
            script_text = LAUNCH_SCRIPT.read_text()
            # Extract app command from marker
            for line in script_text.splitlines():
                if line.startswith("# KIOSK_APP="):
                    state["app_command"] = line[len("# KIOSK_APP="):]
                    break
            # Detect options from markers
            state["auto_restart"] = "# KIOSK_AUTO_RESTART=1" in script_text
            state["restart_on_quit"] = "# KIOSK_RESTART_ON_QUIT=1" in script_text
            state["hide_cursor"] = "# KIOSK_HIDE_CURSOR=1" in script_text
        except OSError:
            pass

    # Check system autostart for suppressed (commented) lines
    if LABWC_SYS_AUTOSTART.exists():
        try:
            text = LABWC_SYS_AUTOSTART.read_text()
            for line in text.splitlines():
                stripped = line.strip()
                if "wf-panel-pi" in stripped:
                    state["suppress_panel"] = stripped.startswith("#")
                if "pcmanfm --desktop" in stripped:
                    state["suppress_desktop"] = stripped.startswith("#")
        except OSError:
            pass

    return state


# ── Backup ───────────────────────────────────────────────────────────

def _backup(path):
    """Create timestamped backup."""
    if not path.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    safe = str(path).replace("/", "_").lstrip("_")
    dest = BACKUP_DIR / f"{safe}.bak.{ts}"
    shutil.copy2(path, dest)
    # Keep max 10
    prefix = f"{safe}.bak."
    baks = sorted(BACKUP_DIR.glob(f"{prefix}*"))
    while len(baks) > 10:
        baks.pop(0).unlink()


def list_backups():
    """Return [(original_path, timestamp_str, backup_path), ...]."""
    if not BACKUP_DIR.exists():
        return []
    result = []
    for f in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if ".bak." not in f.name:
            continue
        parts = f.name.rsplit(".bak.", 1)
        if len(parts) == 2:
            orig = "/" + parts[0].replace("_", "/")
            result.append((orig, parts[1], f))
    return result


# ── Apply kiosk settings ────────────────────────────────────────────

def write_launch_script(app_command, suppress_panel, suppress_desktop,
                        auto_restart=False, restart_on_quit=False,
                        hide_cursor=False):
    """Write the kiosk-launch.sh wrapper."""
    restore_lines = []
    if suppress_desktop:
        restore_lines.append(
            "/usr/bin/lwrespawn /usr/bin/pcmanfm --desktop --profile LXDE-pi &")
    if suppress_panel:
        restore_lines.append(
            "/usr/bin/lwrespawn /usr/bin/wf-panel-pi &")

    if restore_lines:
        restore_block = ("# Restore suppressed desktop components\n"
                         + "\n".join(restore_lines))
    else:
        restore_block = "# Nothing to restore"

    # Marker comments for state detection on next launch
    markers = [f"# KIOSK_APP={app_command}"]
    if auto_restart:
        markers.append("# KIOSK_AUTO_RESTART=1")
    if restart_on_quit:
        markers.append("# KIOSK_RESTART_ON_QUIT=1")
    if hide_cursor:
        markers.append("# KIOSK_HIDE_CURSOR=1")
    marker_block = "\n".join(markers)

    # Cursor hiding
    cursor_start = ""
    cursor_stop = ""
    if hide_cursor:
        cursor_start = (
            'UNCLUTTER_PID=""\n'
            'if command -v unclutter >/dev/null 2>&1; then\n'
            '    unclutter -idle 0.1 -root >/dev/null 2>&1 &\n'
            '    UNCLUTTER_PID=$!\n'
            'fi\n\n')
        cursor_stop = (
            '[ -n "${UNCLUTTER_PID}" ] && kill "$UNCLUTTER_PID" 2>/dev/null || true\n')

    # App launch block
    if auto_restart or restart_on_quit:
        if restart_on_quit:
            # Restart on any exit (crash or quit)
            break_on_clean = ""
        else:
            # Only restart on crash (non-zero exit)
            break_on_clean = ('    [ "$EXIT_CODE" -eq 0 ] && break  '
                              '# clean quit = stop\n')

        app_block = f"""MAX_RESTARTS=5
COUNT=0
EXIT_FLAG="$HOME/.kiosk-exit"
rm -f "$EXIT_FLAG"

while [ ! -f "$EXIT_FLAG" ] && [ "$COUNT" -lt "$MAX_RESTARTS" ]; do
    {app_command}
    EXIT_CODE=$?
{break_on_clean}    COUNT=$((COUNT + 1))
    echo "[kiosk] App exited (code $EXIT_CODE), restart $COUNT/$MAX_RESTARTS"
    sleep 2  # delay between crash restarts to prevent rapid loops
done

rm -f "$EXIT_FLAG\""""
    else:
        app_block = app_command

    script = f"""#!/bin/bash
# Generated by Kiosk Manager — do not edit manually
{marker_block}

sleep 1  # allow compositor/kanshi to apply display config

{cursor_start}{app_block}

{cursor_stop}{restore_block}
"""
    try:
        subprocess.run(
            ["sudo", "-n", "tee", str(LAUNCH_SCRIPT)],
            input=script.encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=10
        )
        subprocess.run(
            ["sudo", "-n", "chmod", "755", str(LAUNCH_SCRIPT)],
            check=True, capture_output=True, timeout=10
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[kiosk] Failed to write launch script: {e}",
              file=sys.stderr, flush=True)
        return False


def _sudo_write(path, content):
    """Write a file via sudo tee."""
    try:
        subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=content.encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=10
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[kiosk] Failed to write {path}: {e}",
              file=sys.stderr, flush=True)
        return False


def _modify_system_autostart(suppress_panel, suppress_desktop):
    """Comment/uncomment pcmanfm and wf-panel-pi in system autostart."""
    if not LABWC_SYS_AUTOSTART.exists():
        return False

    _backup(LABWC_SYS_AUTOSTART)
    text = LABWC_SYS_AUTOSTART.read_text()
    new_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        # Handle pcmanfm line
        if "pcmanfm --desktop" in stripped:
            uncommented = stripped.lstrip("# ")
            if suppress_desktop:
                new_lines.append(f"# {uncommented}")
            else:
                new_lines.append(uncommented)
        # Handle wf-panel-pi line
        elif "wf-panel-pi" in stripped:
            uncommented = stripped.lstrip("# ")
            if suppress_panel:
                new_lines.append(f"# {uncommented}")
            else:
                new_lines.append(uncommented)
        else:
            new_lines.append(line)

    return _sudo_write(LABWC_SYS_AUTOSTART, "\n".join(new_lines) + "\n")


def _restore_system_autostart():
    """Uncomment all lines in system autostart (restore full desktop)."""
    if not LABWC_SYS_AUTOSTART.exists():
        return True

    _backup(LABWC_SYS_AUTOSTART)
    text = LABWC_SYS_AUTOSTART.read_text()
    new_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and (
            "pcmanfm --desktop" in stripped or "wf-panel-pi" in stripped
        ):
            # Uncomment
            new_lines.append(stripped.lstrip("# "))
        else:
            new_lines.append(line)

    return _sudo_write(LABWC_SYS_AUTOSTART, "\n".join(new_lines) + "\n")


def apply_kiosk(app_command, suppress_panel, suppress_desktop,
                enable, auto_restart=False,
                restart_on_quit=False, hide_cursor=False):
    """Configure kiosk mode.

    Strategy: system autostart always runs, so we comment/uncomment
    lines there for suppression. The user autostart adds kiosk-launch.sh.
    Other entries (swayidle, brightness-control, etc.) are never touched.
    """

    if not enable:
        # Restore system autostart (uncomment everything)
        _restore_system_autostart()

        # Remove only the kiosk-launch line from user autostart
        if LABWC_USER_AUTOSTART.exists():
            _backup(LABWC_USER_AUTOSTART)
            text = LABWC_USER_AUTOSTART.read_text()
            keep = [l for l in text.splitlines()
                    if "kiosk-launch" not in l]
            remaining = "\n".join(keep).strip()
            if remaining:
                LABWC_USER_AUTOSTART.write_text(remaining + "\n")
            else:
                LABWC_USER_AUTOSTART.unlink()

        # Clean up launch script
        if LAUNCH_SCRIPT.exists():
            try:
                subprocess.run(
                    ["sudo", "-n", "rm", "-f", str(LAUNCH_SCRIPT)],
                    check=True, capture_output=True, timeout=10
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
        return True

    # ── Enable kiosk ──

    # Write the launch script
    if not write_launch_script(app_command, suppress_panel, suppress_desktop,
                               auto_restart=auto_restart,
                               restart_on_quit=restart_on_quit,
                               hide_cursor=hide_cursor):
        return False

    # Modify system autostart to suppress panel/desktop
    if not _modify_system_autostart(suppress_panel, suppress_desktop):
        return False

    # Add kiosk launcher to user autostart, preserving all other lines
    _backup(LABWC_USER_AUTOSTART)

    lines = []
    if LABWC_USER_AUTOSTART.exists():
        for line in LABWC_USER_AUTOSTART.read_text().splitlines():
            if "kiosk-launch" not in line and line.strip():
                lines.append(line.strip())

    # Add kiosk launcher
    if not any("kiosk-launch" in l for l in lines):
        lines.append(f"{LAUNCH_SCRIPT} &")

    LABWC_USER_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    LABWC_USER_AUTOSTART.write_text("\n".join(lines) + "\n")
    return True


# ── Boot Image ───────────────────────────────────────────────────────

def read_margins_from_cmdline():
    """Parse kernel margins from cmdline.txt. Returns (L, R, T, B)."""
    defaults = (0, 0, 0, 0)
    if not CMDLINE_TXT.exists():
        return defaults
    text = CMDLINE_TXT.read_text()
    m = re.search(r'margin_left=(\d+)', text)
    left = int(m.group(1)) if m else 0
    m = re.search(r'margin_right=(\d+)', text)
    right = int(m.group(1)) if m else 0
    m = re.search(r'margin_top=(\d+)', text)
    top = int(m.group(1)) if m else 0
    m = re.search(r'margin_bottom=(\d+)', text)
    bottom = int(m.group(1)) if m else 0
    return (left, right, top, bottom)


def read_display_resolution():
    """Read native display resolution from DRM (hardware EDID).
    Falls back to kanshi config, then 1920x1080 as last resort."""
    # Primary: read from DRM — the native panel resolution
    drm_base = Path("/sys/class/drm")
    if drm_base.exists():
        for card_dir in sorted(drm_base.iterdir()):
            modes_file = card_dir / "modes"
            if modes_file.exists():
                try:
                    first_mode = modes_file.read_text().strip().splitlines()
                    if first_mode:
                        m = re.match(r'(\d+)x(\d+)', first_mode[0])
                        if m:
                            return (int(m.group(1)), int(m.group(2)))
                except OSError:
                    pass

    # Fallback: kanshi config
    kanshi = HOME / ".config" / "kanshi" / "config"
    if kanshi.exists():
        try:
            text = kanshi.read_text()
            m = re.search(r'mode\s+(\d+)x(\d+)', text)
            if m:
                return (int(m.group(1)), int(m.group(2)))
        except OSError:
            pass

    return (1920, 1080)


def read_display_rotation():
    """Read rotation from kanshi config. Returns degrees (0, 90, 180, 270)."""
    kanshi = HOME / ".config" / "kanshi" / "config"
    if kanshi.exists():
        text = kanshi.read_text()
        if "transform 180" in text or "transform flipped-180" in text:
            return 180
        if "transform 90" in text or "transform flipped-90" in text:
            return 90
        if "transform 270" in text or "transform flipped-270" in text:
            return 270
    return 0


def process_boot_image(source_path, fill=True):
    """Process a source image for Plymouth/firmware splash.

    Pipeline: load → scale to fill or fit native resolution → rotate
    for physical display orientation → save as full-resolution PNG.

    The kernel margins (video= in cmdline.txt) handle hiding edge pixels
    behind the display bezel — we don't offset for them here.

    fill=True:  scale to cover full resolution, crop overflow (default)
    fill=False: scale to fit inside resolution, black bars if needed

    Returns path to processed PNG or None on error.
    """
    try:
        from PIL import Image
    except ImportError:
        print("[kiosk] python3-pil not installed", file=sys.stderr, flush=True)
        return None

    try:
        img = Image.open(source_path).convert("RGB")
    except Exception as e:
        print(f"[kiosk] Failed to open image: {e}", file=sys.stderr,
              flush=True)
        return None

    native_w, native_h = read_display_resolution()
    rotation = read_display_rotation()

    # Scale image to native resolution
    img_w, img_h = img.size
    if fill:
        scale = max(native_w / img_w, native_h / img_h)
    else:
        scale = min(native_w / img_w, native_h / img_h)

    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    if fill and (new_w > native_w or new_h > native_h):
        # Crop to native resolution, centered
        crop_x = (new_w - native_w) // 2
        crop_y = (new_h - native_h) // 2
        img = img.crop((crop_x, crop_y,
                        crop_x + native_w, crop_y + native_h))
        new_w, new_h = native_w, native_h

    # Rotate for physical display orientation
    if rotation == 180:
        img = img.rotate(180)
    elif rotation == 90:
        img = img.rotate(90, expand=True)
    elif rotation == 270:
        img = img.rotate(270, expand=True)

    # Create canvas and center (only matters for fit mode)
    canvas = Image.new("RGB", (native_w, native_h), (0, 0, 0))
    offset_x = (native_w - img.size[0]) // 2
    offset_y = (native_h - img.size[1]) // 2
    canvas.paste(img, (offset_x, offset_y))

    # Save processed image
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    processed = SOURCES_DIR / "boot_processed.png"
    canvas.save(str(processed), "PNG", optimize=True)
    return processed


def apply_boot_image(source_path, fill=True):
    """Process and install a boot image for Plymouth and firmware splash."""
    # Save source for reprocessing if margins change
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    source_copy = SOURCES_DIR / ("boot_source" + Path(source_path).suffix)
    if Path(source_path).resolve() != source_copy.resolve():
        shutil.copy2(source_path, source_copy)

    # Process the image
    processed = process_boot_image(source_path, fill=fill)
    if not processed:
        return False, "Image processing failed."

    errors = []

    # Install Plymouth theme directory and files
    try:
        subprocess.run(
            ["sudo", "-n", "install", "-d", str(PLYMOUTH_THEME_DIR)],
            check=True, capture_output=True, timeout=10
        )
    except subprocess.CalledProcessError:
        errors.append("Failed to create Plymouth theme directory.")

    # Copy processed image to Plymouth theme
    try:
        subprocess.run(
            ["sudo", "-n", "cp", "-f", str(processed), str(PLYMOUTH_IMAGE)],
            check=True, capture_output=True, timeout=10
        )
    except subprocess.CalledProcessError:
        errors.append("Failed to copy image to Plymouth theme.")

    # Copy to firmware splash
    try:
        subprocess.run(
            ["sudo", "-n", "cp", "-f", str(processed), str(FIRMWARE_SPLASH)],
            check=True, capture_output=True, timeout=10
        )
    except subprocess.CalledProcessError:
        errors.append("Failed to copy firmware splash.")

    # Write Plymouth script (center, no scaling — image is pre-processed)
    script = ('wallpaper_image = Image("Boot.png");\n'
              's = Sprite(wallpaper_image);\n'
              's.SetZ(-1000);\n'
              'sw = Window.GetWidth(); sh = Window.GetHeight();\n'
              'iw = wallpaper_image.GetWidth(); '
              'ih = wallpaper_image.GetHeight();\n'
              's.SetPosition((sw - iw) / 2, (sh - ih) / 2);\n')
    try:
        subprocess.run(
            ["sudo", "-n", "tee", str(PLYMOUTH_SCRIPT)],
            input=script.encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10
        )
    except subprocess.CalledProcessError:
        errors.append("Failed to write Plymouth script.")

    # Write Plymouth theme config
    conf = ("[Plymouth Theme]\nName=Custom Boot\n"
            "Description=Custom static splash image\n"
            "ModuleName=script\n\n[script]\n"
            f"ImageDir={PLYMOUTH_THEME_DIR}\n"
            f"ScriptFile={PLYMOUTH_SCRIPT}\n")
    try:
        subprocess.run(
            ["sudo", "-n", "tee", str(PLYMOUTH_CONF)],
            input=conf.encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10
        )
    except subprocess.CalledProcessError:
        errors.append("Failed to write Plymouth config.")

    # Set Plymouth theme and rebuild initramfs
    try:
        subprocess.run(
            ["sudo", "-n", "plymouth-set-default-theme", "-R", "custom-boot"],
            check=True, capture_output=True, timeout=120
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        errors.append(f"Failed to set Plymouth theme: {e}")

    if errors:
        return False, "\n".join(errors)
    return True, None


def clear_boot_image():
    """Reset to solid black boot image."""
    try:
        from PIL import Image
    except ImportError:
        return False, "python3-pil not installed"

    native_w, native_h = read_display_resolution()
    black = Image.new("RGB", (native_w, native_h), (0, 0, 0))
    tmp = Path("/tmp/boot_black.png")
    black.save(str(tmp), "PNG", optimize=True)
    return apply_boot_image(str(tmp))


def read_boot_image_state():
    """Return dict with boot image state."""
    state = {"enabled": False, "source": None, "margins_match": True}

    source = SOURCES_DIR / "boot_source.png"
    if not source.exists():
        # Check other extensions
        for ext in IMAGE_EXTENSIONS:
            candidate = SOURCES_DIR / f"boot_source{ext}"
            if candidate.exists():
                source = candidate
                break

    if source.exists() and PLYMOUTH_IMAGE.exists():
        # Check if Boot.png is larger than a tiny black image (>10KB)
        try:
            size = PLYMOUTH_IMAGE.stat().st_size
            state["enabled"] = size > 10240
        except OSError:
            pass
        state["source"] = str(source)

    # Check if display settings have changed since last processing
    display_state_file = SOURCES_DIR / "boot_display.txt"
    if display_state_file.exists():
        try:
            saved = display_state_file.read_text().strip()
            res = read_display_resolution()
            rot = read_display_rotation()
            current = f"{res[0]}x{res[1]},{rot}"
            state["margins_match"] = (saved == current)
        except OSError:
            pass

    return state


# ── Boot Sound ───────────────────────────────────────────────────────

def process_boot_sound(source_path, delay=1.5):
    """Convert audio to 48kHz 16-bit stereo WAV with warmup silence.
    delay: seconds of silence to prepend (HDMI/amp warmup)."""
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    source_copy = SOURCES_DIR / ("boot_sound_source" +
                                  Path(source_path).suffix)
    if Path(source_path).resolve() != source_copy.resolve():
        shutil.copy2(source_path, source_copy)

    # Save delay setting for future reference
    (SOURCES_DIR / "sound_delay.txt").write_text(str(delay))

    converted = SOURCES_DIR / "boot_converted.wav"
    warmup = SOURCES_DIR / "boot_warmup.wav"
    final = SOURCES_DIR / "boot_final.wav"

    has_sox = shutil.which("sox") is not None
    has_ffmpeg = shutil.which("ffmpeg") is not None

    if not has_sox and not has_ffmpeg:
        return None, "Neither sox nor ffmpeg installed."

    delay_str = f"{delay:.1f}"

    try:
        if has_sox:
            subprocess.run(
                ["sox", str(source_path), "-r", "48000", "-c", "2",
                 "-b", "16", str(converted)],
                check=True, capture_output=True, timeout=60
            )
            subprocess.run(
                ["sox", "-n", "-r", "48000", "-c", "2", "-b", "16",
                 str(warmup), "trim", "0", delay_str],
                check=True, capture_output=True, timeout=10
            )
            subprocess.run(
                ["sox", str(warmup), str(converted), str(final)],
                check=True, capture_output=True, timeout=60
            )
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(source_path),
                 "-ar", "48000", "-ac", "2", "-sample_fmt", "s16",
                 str(converted)],
                check=True, capture_output=True, timeout=60
            )
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", "anullsrc=r=48000:cl=stereo",
                 "-t", delay_str, "-sample_fmt", "s16", str(warmup)],
                check=True, capture_output=True, timeout=10
            )
            concat_list = SOURCES_DIR / "concat.txt"
            concat_list.write_text(
                f"file '{warmup}'\nfile '{converted}'\n")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_list), "-c", "copy", str(final)],
                check=True, capture_output=True, timeout=60
            )

        return str(final), None

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else str(e)
        return None, f"Audio conversion failed: {stderr[:200]}"


def _ensure_sound_service():
    """Create startup-sound.service if it doesn't exist.
    Uses a simple aplay-based service. If a custom script like
    bootchime-gpio.sh already exists, the existing service is preserved."""
    if SOUND_SERVICE.exists():
        return True

    service = f"""[Unit]
Description=Play startup sound before greeter
After=local-fs.target sound.target plymouth-start.service
Before=display-manager.service plymouth-quit.service graphical.target
Wants=sound.target plymouth-start.service
ConditionPathExists={BOOTCHIME_WAV}

[Service]
Type=oneshot
ExecStart=/usr/bin/aplay {BOOTCHIME_WAV}

[Install]
WantedBy=graphical.target
"""
    try:
        subprocess.run(
            ["sudo", "-n", "tee", str(SOUND_SERVICE)],
            input=service.encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10
        )
        subprocess.run(
            ["sudo", "-n", "systemctl", "daemon-reload"],
            check=True, capture_output=True, timeout=10
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def apply_boot_sound(source_path, delay=1.5):
    """Process and install a boot sound."""
    processed, error = process_boot_sound(source_path, delay=delay)
    if not processed:
        return False, error

    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(processed, BOOTCHIME_WAV)
    except OSError as e:
        return False, f"Failed to copy sound: {e}"

    # Create service if it doesn't exist (fresh install)
    if not SOUND_SERVICE.exists():
        if not _ensure_sound_service():
            return False, "Failed to create startup-sound.service."

    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", "enable", "startup-sound.service"],
            check=True, capture_output=True, timeout=10
        )
    except subprocess.CalledProcessError:
        pass

    return True, None


def set_boot_sound_enabled(enable):
    """Enable or disable the boot sound service."""
    action = "enable" if enable else "disable"
    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", action, "startup-sound.service"],
            check=True, capture_output=True, timeout=10
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def read_sound_delay():
    """Read the saved sound delay setting. Default 1.5s."""
    delay_file = SOURCES_DIR / "sound_delay.txt"
    if delay_file.exists():
        try:
            return float(delay_file.read_text().strip())
        except (ValueError, OSError):
            pass
    return 1.5


def read_boot_sound_state():
    """Return dict with boot sound state."""
    state = {"enabled": False, "service_exists": False, "source": None}

    state["service_exists"] = SOUND_SERVICE.exists()

    if state["service_exists"]:
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", "startup-sound.service"],
                capture_output=True, text=True, timeout=5
            )
            state["enabled"] = result.stdout.strip() == "enabled"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    for ext in SOUND_EXTENSIONS:
        candidate = SOURCES_DIR / f"boot_sound_source{ext}"
        if candidate.exists():
            state["source"] = str(candidate)
            break

    return state


# ── Shutdown Screen ──────────────────────────────────────────────────

PLYMOUTH_SHUTDOWN_SERVICES = [
    "plymouth-poweroff.service",
    "plymouth-reboot.service",
]


def read_shutdown_black():
    """Check if shutdown splash is disabled (services masked)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", "plymouth-poweroff.service"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == "masked"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def set_shutdown_black(enable):
    """Enable or disable black screen on shutdown by masking/unmasking
    Plymouth's shutdown splash services."""
    action = "mask" if enable else "unmask"
    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", action] + PLYMOUTH_SHUTDOWN_SERVICES,
            check=True, capture_output=True, timeout=10
        )
        subprocess.run(
            ["sudo", "-n", "systemctl", "daemon-reload"],
            check=True, capture_output=True, timeout=10
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


class KioskManagerWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Kiosk Manager", default_width=420)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_resizable(False)

        self.compositor = detect_compositor()
        self.state = read_kiosk_state()

        self._build_ui()

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        self.add(outer)

        # ── Header ──
        header = Gtk.Label()
        header.set_markup(f"<big><b>Kiosk Manager</b></big>  <small>v{VERSION}</small>")
        header.set_xalign(0)
        outer.pack_start(header, False, False, 0)

        info = Gtk.Label()
        info.set_markup(
            f"<small>Compositor: <b>{self.compositor}</b>    "
            f"Auto-login: <b>{'enabled' if is_autologin_enabled() else 'disabled'}</b></small>"
        )
        info.set_xalign(0)
        outer.pack_start(info, False, False, 0)

        if self.compositor != "labwc":
            warn = Gtk.Label()
            warn.set_markup(
                f"<small><b>⚠ Kiosk features require labwc.</b> "
                f"Detected: {self.compositor}. "
                f"Boot settings still work.</small>")
            warn.set_xalign(0)
            outer.pack_start(warn, False, False, 0)

        # ── Notebook (tabs) ──
        notebook = Gtk.Notebook()
        outer.pack_start(notebook, True, True, 4)

        # ═══ TAB 1: Kiosk ═══
        kiosk_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        kiosk_page.set_margin_start(8)
        kiosk_page.set_margin_end(8)
        kiosk_page.set_margin_top(8)
        kiosk_page.set_margin_bottom(8)
        notebook.append_page(kiosk_page, Gtk.Label(label="Kiosk"))

        # Auto-login
        section_login = Gtk.Label()
        section_login.set_markup("<b>Login</b>")
        section_login.set_xalign(0)
        kiosk_page.pack_start(section_login, False, False, 0)

        self.autologin_check = Gtk.CheckButton(
            label="Automatic desktop login (no password prompt)")
        self.autologin_check.set_active(is_autologin_enabled())
        self.autologin_check.set_tooltip_text(
            "Required for unattended kiosk boot.\n"
            "Uses raspi-config to set LightDM autologin.")
        al_box = Gtk.Box(spacing=0)
        al_box.set_margin_start(12)
        al_box.pack_start(self.autologin_check, False, False, 0)
        kiosk_page.pack_start(al_box, False, False, 0)

        kiosk_page.pack_start(Gtk.Separator(), False, False, 2)

        # Kiosk mode
        section_kiosk = Gtk.Label()
        section_kiosk.set_markup("<b>Kiosk Mode</b>")
        section_kiosk.set_xalign(0)
        kiosk_page.pack_start(section_kiosk, False, False, 0)

        self.kiosk_check = Gtk.CheckButton(
            label="Enable kiosk mode (launch app on boot)")
        self.kiosk_check.set_active(self.state["enabled"])
        self.kiosk_check.connect("toggled", self._on_kiosk_toggled)
        kk_box = Gtk.Box(spacing=0)
        kk_box.set_margin_start(12)
        kk_box.pack_start(self.kiosk_check, False, False, 0)
        kiosk_page.pack_start(kk_box, False, False, 0)

        # Kiosk sub-options
        self.kiosk_frame = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.kiosk_frame.set_margin_start(24)
        self.kiosk_frame.set_margin_top(2)

        app_label = Gtk.Label(label="Application to launch:")
        app_label.set_xalign(0)
        self.kiosk_frame.pack_start(app_label, False, False, 0)

        app_hbox = Gtk.Box(spacing=4)
        self.app_entry = Gtk.Entry()
        self.app_entry.set_text(self.state["app_command"])
        self.app_entry.set_hexpand(True)
        self.app_entry.set_placeholder_text(
            "/path/to/application")
        self.app_entry.set_tooltip_text(
            "Full path to the application or wrapper script.\n"
            "Examples:\n"
            "  /usr/bin/chromium --kiosk https://example.com\n"
            "  /home/pi/my-app/start.sh\n"
            "  /usr/bin/vlc --fullscreen /path/to/video")
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._on_browse)
        paste_btn = Gtk.Button(label="Paste")
        paste_btn.set_tooltip_text("Paste from clipboard")
        paste_btn.connect("clicked", self._on_paste_app)
        app_hbox.pack_start(self.app_entry, True, True, 0)
        app_hbox.pack_start(paste_btn, False, False, 0)
        app_hbox.pack_start(browse_btn, False, False, 0)
        self.kiosk_frame.pack_start(app_hbox, False, False, 0)

        self.suppress_panel_check = Gtk.CheckButton(
            label="Hide taskbar (wf-panel-pi)")
        self.suppress_panel_check.set_active(self.state["suppress_panel"])
        self.suppress_panel_check.set_tooltip_text(
            "Taskbar hidden while kiosk app runs, restored on exit.\n"
            "Tray apps still run in the background.")
        self.kiosk_frame.pack_start(
            self.suppress_panel_check, False, False, 0)

        self.suppress_desktop_check = Gtk.CheckButton(
            label="Hide desktop icons and wallpaper (pcmanfm)")
        self.suppress_desktop_check.set_active(
            self.state["suppress_desktop"])
        self.suppress_desktop_check.set_tooltip_text(
            "Desktop background and icons hidden while kiosk app runs.")
        self.kiosk_frame.pack_start(
            self.suppress_desktop_check, False, False, 0)

        self.auto_restart_check = Gtk.CheckButton(
            label="Auto-restart on crash (max 5 times)")
        self.auto_restart_check.set_active(self.state["auto_restart"])
        self.auto_restart_check.connect("toggled", self._on_restart_toggled)
        self.auto_restart_check.set_tooltip_text(
            "Restart the app if it crashes (non-zero exit).\n"
            "Stops after 5 consecutive crashes.\n"
            "To break the loop via SSH: touch ~/.kiosk-exit")
        self.kiosk_frame.pack_start(
            self.auto_restart_check, False, False, 0)

        self.restart_on_quit_check = Gtk.CheckButton(
            label="Also restart on clean quit")
        self.restart_on_quit_check.set_active(self.state["restart_on_quit"])
        self.restart_on_quit_check.set_tooltip_text(
            "Restart even when the app exits normally (exit code 0).\n"
            "The app will keep relaunching until max restarts\n"
            "or you run: touch ~/.kiosk-exit")
        rq_box = Gtk.Box(spacing=0)
        rq_box.set_margin_start(24)
        rq_box.pack_start(self.restart_on_quit_check, False, False, 0)
        self.kiosk_frame.pack_start(rq_box, False, False, 0)

        self.hide_cursor_check = Gtk.CheckButton(
            label="Hide mouse cursor")
        self.hide_cursor_check.set_active(self.state["hide_cursor"])
        self.hide_cursor_check.set_tooltip_text(
            "Hide the mouse cursor while the kiosk app runs.\n"
            "Useful for touchscreen-only devices.")
        self.kiosk_frame.pack_start(
            self.hide_cursor_check, False, False, 0)

        kiosk_page.pack_start(self.kiosk_frame, False, False, 0)

        kiosk_page.pack_start(Gtk.Separator(), False, False, 2)

        # Tray icon
        self.tray_check = Gtk.CheckButton(
            label="Show system tray icon")
        self.tray_check.set_active(is_tray_enabled())
        self.tray_check.set_tooltip_text(
            "Show a status icon in the taskbar for\n"
            "quick access to Kiosk Manager settings.")
        tray_box = Gtk.Box(spacing=0)
        tray_box.set_margin_start(12)
        tray_box.pack_start(self.tray_check, False, False, 0)
        kiosk_page.pack_start(tray_box, False, False, 0)

        # ═══ TAB 2: Boot ═══
        boot_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        boot_page.set_margin_start(8)
        boot_page.set_margin_end(8)
        boot_page.set_margin_top(8)
        boot_page.set_margin_bottom(8)
        notebook.append_page(boot_page, Gtk.Label(label="Boot"))

        # Boot Image
        section_image = Gtk.Label()
        section_image.set_markup("<b>Boot Image</b>")
        section_image.set_xalign(0)
        boot_page.pack_start(section_image, False, False, 0)

        self.boot_image_state = read_boot_image_state()

        img_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        img_box.set_margin_start(12)

        # Display info
        res = read_display_resolution()
        rotation = read_display_rotation()
        margins = read_margins_from_cmdline()
        disp_info = Gtk.Label()
        disp_info.set_markup(
            f"<small>Display: {res[0]}×{res[1]}  "
            f"Rotation: {rotation}°\n"
            f"Borders: L={margins[0]} R={margins[1]} "
            f"T={margins[2]} B={margins[3]} "
            f"(applied by kernel)</small>")
        disp_info.set_xalign(0)
        img_box.pack_start(disp_info, False, False, 0)

        # File picker
        img_file_box = Gtk.Box(spacing=4)
        self.img_entry = Gtk.Entry()
        self.img_entry.set_hexpand(True)
        self.img_entry.set_placeholder_text("Select an image file…")
        self.img_entry.set_tooltip_text(
            "Image will be scaled and processed for your display.")
        if self.boot_image_state["source"]:
            self.img_entry.set_text(self.boot_image_state["source"])
        self._orig_img_path = self.img_entry.get_text()
        self._orig_fill_mode = True  # default is fill
        img_browse = Gtk.Button(label="Browse…")
        img_browse.connect("clicked", self._on_browse_image)
        img_clear = Gtk.Button(label="Clear")
        img_clear.set_tooltip_text("Reset to solid black boot screen.")
        img_clear.connect("clicked", self._on_clear_image)
        img_file_box.pack_start(self.img_entry, True, True, 0)
        img_file_box.pack_start(img_browse, False, False, 0)
        img_file_box.pack_start(img_clear, False, False, 0)
        img_box.pack_start(img_file_box, False, False, 0)

        # Scaling mode
        scale_box = Gtk.Box(spacing=8)
        scale_label = Gtk.Label(label="Scaling:")
        scale_label.set_xalign(0)
        scale_box.pack_start(scale_label, False, False, 0)
        self.img_fill_radio = Gtk.RadioButton.new_with_label(
            None, "Fill (crop to cover)")
        self.img_fill_radio.set_tooltip_text(
            "Scale to completely fill the visible area.\n"
            "Edges may be cropped if aspect ratios differ.")
        scale_box.pack_start(self.img_fill_radio, False, False, 0)
        self.img_fit_radio = Gtk.RadioButton.new_with_label_from_widget(
            self.img_fill_radio, "Fit (no crop)")
        self.img_fit_radio.set_tooltip_text(
            "Scale to fit entirely within the visible area.\n"
            "Black bars may appear if aspect ratios differ.")
        scale_box.pack_start(self.img_fit_radio, False, False, 0)
        self.img_fill_radio.set_active(True)  # default to fill
        img_box.pack_start(scale_box, False, False, 0)

        self.img_status = Gtk.Label()
        self.img_status.set_xalign(0)
        self._update_image_status()
        img_box.pack_start(self.img_status, False, False, 0)

        boot_page.pack_start(img_box, False, False, 0)

        boot_page.pack_start(Gtk.Separator(), False, False, 2)

        # Boot Sound
        section_sound = Gtk.Label()
        section_sound.set_markup("<b>Boot Sound</b>")
        section_sound.set_xalign(0)
        boot_page.pack_start(section_sound, False, False, 0)

        self.boot_sound_state = read_boot_sound_state()

        snd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        snd_box.set_margin_start(12)

        self.snd_enable_check = Gtk.CheckButton(label="Play sound on boot")
        self.snd_enable_check.set_active(self.boot_sound_state["enabled"])
        self.snd_enable_check.set_sensitive(
            self.boot_sound_state["service_exists"])
        if not self.boot_sound_state["service_exists"]:
            self.snd_enable_check.set_tooltip_text(
                "No boot sound service found.\n"
                "Select a sound file to create one.")
        snd_box.pack_start(self.snd_enable_check, False, False, 0)

        delay_box = Gtk.Box(spacing=4)
        delay_label = Gtk.Label(label="Startup delay:")
        delay_label.set_tooltip_text(
            "Seconds of silence before the chime plays.\n"
            "Allows HDMI audio and amplifiers to initialize.\n"
            "Reprocesses the sound file when changed.")
        delay_box.pack_start(delay_label, False, False, 0)
        self.snd_delay_spin = Gtk.SpinButton.new_with_range(0, 5, 0.5)
        self.snd_delay_spin.set_digits(1)
        self.snd_delay_spin.set_value(read_sound_delay())
        self._orig_snd_delay = read_sound_delay()
        delay_box.pack_start(self.snd_delay_spin, False, False, 0)
        delay_sec = Gtk.Label(label="sec")
        delay_box.pack_start(delay_sec, False, False, 0)
        snd_box.pack_start(delay_box, False, False, 0)

        snd_file_box = Gtk.Box(spacing=4)
        self.snd_entry = Gtk.Entry()
        self.snd_entry.set_hexpand(True)
        self.snd_entry.set_placeholder_text("Select a sound file…")
        self.snd_entry.set_tooltip_text(
            "Select an audio file to use as boot chime.\n"
            "It will be converted to 48kHz WAV with\n"
            "warmup silence when you click Apply.")
        if self.boot_sound_state["source"]:
            self.snd_entry.set_text(self.boot_sound_state["source"])
        self._orig_snd_path = self.snd_entry.get_text()
        snd_browse = Gtk.Button(label="Browse…")
        snd_browse.connect("clicked", self._on_browse_sound)
        snd_file_box.pack_start(self.snd_entry, True, True, 0)
        snd_file_box.pack_start(snd_browse, False, False, 0)
        snd_box.pack_start(snd_file_box, False, False, 0)

        self.snd_status = Gtk.Label()
        self.snd_status.set_xalign(0)
        self._update_sound_status()
        snd_box.pack_start(self.snd_status, False, False, 0)

        boot_page.pack_start(snd_box, False, False, 0)

        boot_page.pack_start(Gtk.Separator(), False, False, 2)

        # Shutdown
        section_shutdown = Gtk.Label()
        section_shutdown.set_markup("<b>Shutdown</b>")
        section_shutdown.set_xalign(0)
        boot_page.pack_start(section_shutdown, False, False, 0)

        sd_box = Gtk.Box(spacing=0)
        sd_box.set_margin_start(12)
        self.shutdown_black_check = Gtk.CheckButton(
            label="Black screen on shutdown (hide boot image)")
        self.shutdown_black_check.set_tooltip_text(
            "By default, Plymouth shows the boot image during\n"
            "shutdown too. Check this to show a black screen instead.")
        self.shutdown_black_check.set_active(read_shutdown_black())
        sd_box.pack_start(self.shutdown_black_check, False, False, 0)
        boot_page.pack_start(sd_box, False, False, 0)

        # ═══ Bottom buttons (outside tabs) ═══
        btn_box = Gtk.Box(spacing=6)
        btn_box.set_margin_top(2)

        self.tray_check = Gtk.CheckButton(label="Tray icon")
        self.tray_check.set_active(is_tray_enabled())
        self.tray_check.set_tooltip_text(
            "Show a status icon in the taskbar.\n"
            "Provides quick access to kiosk settings.")
        btn_box.pack_start(self.tray_check, False, False, 0)

        spacer = Gtk.Label()
        btn_box.pack_start(spacer, True, True, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _: self.destroy())
        btn_box.pack_start(close_btn, False, False, 0)

        self.apply_btn = Gtk.Button(label="Apply")
        self.apply_btn.get_style_context().add_class("suggested-action")
        self.apply_btn.connect("clicked", self._on_apply)
        btn_box.pack_start(self.apply_btn, False, False, 0)

        outer.pack_start(btn_box, False, False, 0)

        # Initial state
        self._on_kiosk_toggled()
        if self.compositor != "labwc":
            kiosk_page.set_sensitive(False)

    # ── UI handlers ──────────────────────────────────────────────────

    def _on_kiosk_toggled(self, _w=None):
        """Enable/disable kiosk sub-options."""
        enabled = self.kiosk_check.get_active()
        self.kiosk_frame.set_sensitive(enabled)
        self._on_restart_toggled()

    def _on_restart_toggled(self, _w=None):
        """Enable restart_on_quit only when auto_restart is checked."""
        self.restart_on_quit_check.set_sensitive(
            self.auto_restart_check.get_active())

    def _update_image_status(self):
        """Update boot image status label."""
        st = self.boot_image_state
        if st["enabled"]:
            txt = "<small>Custom boot image installed"
            if not st["margins_match"]:
                txt += " (display changed — reapply recommended)"
            txt += "</small>"
        else:
            txt = "<small>Solid black (default)</small>"
        self.img_status.set_markup(txt)

    def _update_sound_status(self):
        """Update boot sound status label."""
        st = self.boot_sound_state
        if not st["service_exists"]:
            txt = "<small>No boot sound service configured</small>"
        elif st["enabled"]:
            txt = "<small>Boot sound enabled</small>"
        else:
            txt = "<small>Boot sound disabled</small>"
        self.snd_status.set_markup(txt)

    def _on_browse(self, _btn):
        """File chooser for kiosk application selection."""
        fc = Gtk.FileChooserDialog(
            title="Select Application",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        fc.add_button("Cancel", Gtk.ResponseType.CANCEL)
        fc.add_button("Select", Gtk.ResponseType.OK)
        fc.set_current_folder(str(HOME))

        filt = Gtk.FileFilter()
        filt.set_name("All files")
        filt.add_pattern("*")
        fc.add_filter(filt)

        if fc.run() == Gtk.ResponseType.OK:
            self.app_entry.set_text(fc.get_filename())
        fc.destroy()

    def _on_paste_app(self, _btn):
        """Paste clipboard contents into the app entry."""
        clipboard = Gtk.Clipboard.get_default(self.get_display())
        text = clipboard.wait_for_text()
        if text:
            self.app_entry.set_text(text.strip())

    def _on_browse_image(self, _btn):
        """File chooser for boot image."""
        fc = Gtk.FileChooserDialog(
            title="Select Boot Image",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        fc.add_button("Cancel", Gtk.ResponseType.CANCEL)
        fc.add_button("Select", Gtk.ResponseType.OK)
        fc.set_current_folder(str(HOME))

        filt = Gtk.FileFilter()
        filt.set_name("Images")
        for ext in IMAGE_EXTENSIONS:
            filt.add_pattern(f"*{ext}")
            filt.add_pattern(f"*{ext.upper()}")
        fc.add_filter(filt)

        filt_all = Gtk.FileFilter()
        filt_all.set_name("All files")
        filt_all.add_pattern("*")
        fc.add_filter(filt_all)

        if fc.run() == Gtk.ResponseType.OK:
            self.img_entry.set_text(fc.get_filename())
        fc.destroy()

    def _on_clear_image(self, _btn):
        """Reset boot image to solid black."""
        if not self._confirm(
            "Clear boot image?",
            "Replace the boot image with a solid black screen.\n"
            "This will rebuild the initramfs (up to 30 seconds).",
            "Clear"
        ):
            return

        def _task():
            return clear_boot_image()

        def _done(result):
            ok, error = result
            if ok:
                for ext in IMAGE_EXTENSIONS:
                    src = SOURCES_DIR / f"boot_source{ext}"
                    if src.exists():
                        src.unlink()
                self.img_entry.set_text("")
                self._orig_img_path = ""
                self.boot_image_state = read_boot_image_state()
                self._update_image_status()
                self._msg(Gtk.MessageType.INFO, "Boot image cleared",
                          "Boot screen is now solid black.")
            else:
                self._msg(Gtk.MessageType.ERROR, "Failed", error)

        self._run_threaded(_task, _done, "Clearing boot image…",
                           "Rebuilding initramfs.\n"
                           "This may take up to 30 seconds.")

    def _on_browse_sound(self, _btn):
        """File chooser for boot sound."""
        fc = Gtk.FileChooserDialog(
            title="Select Boot Sound",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        fc.add_button("Cancel", Gtk.ResponseType.CANCEL)
        fc.add_button("Select", Gtk.ResponseType.OK)
        fc.set_current_folder(str(HOME))

        filt = Gtk.FileFilter()
        filt.set_name("Audio files")
        for ext in SOUND_EXTENSIONS:
            filt.add_pattern(f"*{ext}")
            filt.add_pattern(f"*{ext.upper()}")
        fc.add_filter(filt)

        filt_all = Gtk.FileFilter()
        filt_all.set_name("All files")
        filt_all.add_pattern("*")
        fc.add_filter(filt_all)

        if fc.run() == Gtk.ResponseType.OK:
            self.snd_entry.set_text(fc.get_filename())
        fc.destroy()

    # ── Apply (unified) ──────────────────────────────────────────────

    def _on_apply(self, _btn):
        """Validate, confirm, and apply all settings."""
        kiosk_enabled = self.kiosk_check.get_active()
        app_cmd = self.app_entry.get_text().strip()
        suppress_panel = self.suppress_panel_check.get_active()
        suppress_desktop = self.suppress_desktop_check.get_active()
        auto_restart = self.auto_restart_check.get_active()
        restart_on_quit = self.restart_on_quit_check.get_active()
        hide_cursor = self.hide_cursor_check.get_active()
        want_autologin = self.autologin_check.get_active()

        # Detect changes
        img_path = self.img_entry.get_text().strip()
        fill_mode = self.img_fill_radio.get_active()
        new_image = (img_path and img_path != self._orig_img_path
                     and os.path.exists(img_path))
        reprocess_image = (img_path and not new_image
                           and os.path.exists(img_path)
                           and fill_mode != self._orig_fill_mode)
        snd_path = self.snd_entry.get_text().strip()
        new_sound = (snd_path and snd_path != self._orig_snd_path
                     and os.path.exists(snd_path))
        snd_delay = self.snd_delay_spin.get_value()
        delay_changed = (snd_delay != self._orig_snd_delay)
        # Delay change on existing sound requires reprocessing
        reprocess_sound = (delay_changed and not new_sound
                           and self.boot_sound_state["source"]
                           and os.path.exists(
                               self.boot_sound_state["source"]))
        want_sound = self.snd_enable_check.get_active()
        sound_toggled = (self.boot_sound_state["service_exists"] and
                         want_sound != self.boot_sound_state["enabled"])
        want_shutdown_black = self.shutdown_black_check.get_active()
        shutdown_changed = (want_shutdown_black != read_shutdown_black())
        want_tray = self.tray_check.get_active()
        tray_changed = (want_tray != is_tray_enabled())

        # Validate kiosk
        if kiosk_enabled and not app_cmd:
            self._msg(Gtk.MessageType.WARNING, "No application specified",
                      "Enter the path to the application to launch.")
            return

        if kiosk_enabled and not os.path.exists(app_cmd.split()[0]):
            if not self._confirm(
                "Application not found",
                f"The path '{app_cmd.split()[0]}' does not exist.\n\n"
                "Apply anyway?"):
                return

        # Validate new image
        if new_image:
            ext = Path(img_path).suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                self._msg(Gtk.MessageType.WARNING, "Unsupported image",
                          f"'{ext}' is not supported.\n"
                          f"Use: {', '.join(sorted(IMAGE_EXTENSIONS))}")
                return

        # Validate new sound
        if new_sound:
            ext = Path(snd_path).suffix.lower()
            if ext not in SOUND_EXTENSIONS:
                self._msg(Gtk.MessageType.WARNING, "Unsupported audio",
                          f"'{ext}' is not supported.\n"
                          f"Use: {', '.join(sorted(SOUND_EXTENSIONS))}")
                return

        # Build change summary
        changes = []
        old = self.state

        if want_autologin != is_autologin_enabled():
            changes.append(
                f"Auto-login: {'enable' if want_autologin else 'disable'}")

        if kiosk_enabled:
            if not old["enabled"]:
                changes.append("Kiosk mode: enable")
            if app_cmd != old["app_command"]:
                changes.append(f"  App: {app_cmd}")
            if suppress_panel != old["suppress_panel"]:
                changes.append(
                    f"  Taskbar: {'hide' if suppress_panel else 'show'}")
            if suppress_desktop != old["suppress_desktop"]:
                changes.append(
                    f"  Desktop: {'hide' if suppress_desktop else 'show'}")
            if auto_restart != old["auto_restart"]:
                changes.append(
                    f"  Auto-restart: {'on' if auto_restart else 'off'}")
            if restart_on_quit != old["restart_on_quit"]:
                changes.append(
                    f"  Restart on quit: {'on' if restart_on_quit else 'off'}")
            if hide_cursor != old["hide_cursor"]:
                changes.append(
                    f"  Cursor: {'hidden' if hide_cursor else 'visible'}")
        elif old["enabled"]:
            changes.append("Kiosk mode: disable (restore normal desktop)")

        if new_image:
            changes.append(f"Boot image: {os.path.basename(img_path)}")
        elif reprocess_image:
            mode_name = "fill" if fill_mode else "fit"
            changes.append(f"Boot image: reprocess as {mode_name}")
        if new_sound:
            changes.append(f"Boot sound: {os.path.basename(snd_path)}")
        elif reprocess_sound:
            changes.append(f"Boot sound delay: {snd_delay:.1f}s")
        if sound_toggled:
            changes.append(
                f"Boot sound: {'enable' if want_sound else 'disable'}")
        if shutdown_changed:
            changes.append(
                f"Shutdown screen: "
                f"{'black' if want_shutdown_black else 'show boot image'}")
        if tray_changed:
            changes.append(
                f"Tray icon: {'show' if want_tray else 'hide'}")

        if not changes:
            self._msg(Gtk.MessageType.INFO, "No changes",
                      "Settings match the current configuration.")
            return

        needs_reboot = (kiosk_enabled != old["enabled"] or
                        app_cmd != old["app_command"] or
                        suppress_panel != old["suppress_panel"] or
                        suppress_desktop != old["suppress_desktop"] or
                        auto_restart != old["auto_restart"] or
                        restart_on_quit != old["restart_on_quit"] or
                        hide_cursor != old["hide_cursor"])

        summary = "\n".join(changes)
        if new_image or reprocess_image or new_sound or reprocess_sound:
            summary += "\n\nImage/sound processing may take up to 30 seconds."
        if needs_reboot:
            summary += "\n\nA reboot is required for kiosk changes."

        action = "Apply & Reboot" if needs_reboot else "Apply"
        if not self._confirm(f"{action}?", summary, action):
            return

        # Run everything in a background thread
        def _task():
            errors = []

            # Auto-login
            if want_autologin != is_autologin_enabled():
                if not set_autologin(want_autologin):
                    errors.append("Failed to change auto-login.")

            # Kiosk
            if not apply_kiosk(app_cmd, suppress_panel, suppress_desktop,
                               kiosk_enabled,
                               auto_restart=auto_restart,
                               restart_on_quit=restart_on_quit,
                               hide_cursor=hide_cursor):
                errors.append("Failed to write kiosk configuration.")

            # Boot sound toggle
            if sound_toggled:
                if not set_boot_sound_enabled(want_sound):
                    errors.append("Failed to toggle boot sound.")

            # New boot image or reprocess with different scaling
            if new_image or reprocess_image:
                ok, err = apply_boot_image(img_path, fill=fill_mode)
                if ok:
                    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
                    res = read_display_resolution()
                    rot = read_display_rotation()
                    (SOURCES_DIR / "boot_display.txt").write_text(
                        f"{res[0]}x{res[1]},{rot}")
                else:
                    errors.append(f"Boot image: {err}")

            # New boot sound or delay change
            if new_sound:
                ok, err = apply_boot_sound(snd_path, delay=snd_delay)
                if not ok:
                    errors.append(f"Boot sound: {err}")
            elif reprocess_sound:
                src = self.boot_sound_state["source"]
                ok, err = apply_boot_sound(src, delay=snd_delay)
                if not ok:
                    errors.append(f"Boot sound: {err}")

            # Shutdown screen
            if shutdown_changed:
                if not set_shutdown_black(want_shutdown_black):
                    errors.append("Failed to change shutdown screen.")

            # Tray icon
            if tray_changed:
                if not set_tray_enabled(want_tray):
                    errors.append("Failed to change tray icon setting.")

            return errors

        def _done(errors):
            if errors:
                self._msg(Gtk.MessageType.ERROR, "Some changes failed",
                          "\n".join(errors))
                return

            # Update state
            if new_image or reprocess_image:
                self.boot_image_state = read_boot_image_state()
                self._update_image_status()
            if new_sound or reprocess_sound:
                self.boot_sound_state = read_boot_sound_state()
                self.snd_enable_check.set_active(True)
                self.snd_enable_check.set_sensitive(True)
                self._update_sound_status()

            if needs_reboot:
                self._msg(Gtk.MessageType.INFO, "Settings applied",
                          "The system will now reboot.")
                try:
                    subprocess.Popen(
                        ["sudo", "-n", "reboot"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
                except Exception as e:
                    self._msg(Gtk.MessageType.ERROR, "Reboot failed",
                              f"Please reboot manually.\n\n{e}")
            else:
                self._msg(Gtk.MessageType.INFO, "Settings applied",
                          "Changes saved. Takes effect on next boot.")
            self.destroy()

        msg = "Applying settings…"
        detail = "Please wait."
        if new_image or reprocess_image:
            detail = ("Processing image and rebuilding initramfs.\n"
                      "This may take up to 30 seconds.")
        elif new_sound or reprocess_sound:
            detail = "Converting audio…"

        self._run_threaded(_task, _done, msg, detail)

    # ── Backup History ───────────────────────────────────────────────

    def _on_history(self, _btn):
        """Show backup history with restore option."""
        backups = list_backups()

        dlg = Gtk.Dialog(
            title="Backup History", parent=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dlg.set_default_size(420, 280)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)

        box = dlg.get_content_area()
        box.set_spacing(4)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)

        if not backups:
            lbl = Gtk.Label(
                label="No backups yet.\n\n"
                      "Backups are created whenever kiosk settings change.")
            lbl.set_justify(Gtk.Justification.CENTER)
            box.pack_start(lbl, True, True, 0)
        else:
            store = Gtk.ListStore(str, str, str)
            for orig, ts, bak in backups:
                ts_fmt = (f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} "
                          f"{ts[8:10]}:{ts[10:12]}:{ts[12:]}")
                store.append([orig, ts_fmt, str(bak)])

            tree = Gtk.TreeView(model=store)
            tree.set_headers_visible(True)
            col1 = Gtk.TreeViewColumn(
                "File", Gtk.CellRendererText(), text=0)
            col1.set_expand(True)
            tree.append_column(col1)
            col2 = Gtk.TreeViewColumn(
                "Timestamp", Gtk.CellRendererText(), text=1)
            tree.append_column(col2)

            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.add(tree)
            box.pack_start(scroll, True, True, 0)

            restore_btn = Gtk.Button(label="Restore Selected")
            box.pack_start(restore_btn, False, False, 0)

            def _on_restore(_b):
                sel = tree.get_selection()
                model, it = sel.get_selected()
                if it is None:
                    return
                orig_path = model[it][0]
                bak_path = model[it][2]
                if self._confirm(
                    f"Restore {orig_path}?",
                    "Current file will be backed up first.\n"
                    "Reboot needed for changes to take effect."
                ):
                    try:
                        dest = Path(orig_path)
                        _backup(dest)
                        shutil.copy2(Path(bak_path), dest)
                        self._msg(Gtk.MessageType.INFO, "Restored",
                                  "Reboot for changes to take effect.",
                                  parent=dlg)
                    except OSError as e:
                        self._msg(Gtk.MessageType.ERROR, "Restore failed",
                                  str(e), parent=dlg)

            restore_btn.connect("clicked", _on_restore)

        dlg.show_all()
        dlg.run()
        dlg.destroy()

    # ── Helpers ──────────────────────────────────────────────────────

    def _run_threaded(self, task_fn, callback_fn, message="Working…",
                      detail="Please wait."):
        """Run task_fn in a background thread with a visible progress
        dialog. Calls callback_fn(result) on the GTK thread when done."""
        dlg = Gtk.MessageDialog(
            parent=self,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text=message,
            secondary_text=detail,
        )
        dlg.set_deletable(False)
        dlg.show_all()

        def _worker():
            try:
                result = task_fn()
            except Exception as e:
                print(f"[kiosk] Thread error: {e}", file=sys.stderr,
                      flush=True)
                result = [f"Unexpected error: {e}"]
            GLib.idle_add(self._threaded_done, result, callback_fn, dlg)

        threading.Thread(target=_worker, daemon=True).start()

    def _threaded_done(self, result, callback_fn, dlg):
        """Called on GTK thread when background task finishes."""
        dlg.destroy()
        callback_fn(result)
        return False  # remove idle source

    def _msg(self, msg_type, title, text, parent=None):
        md = Gtk.MessageDialog(
            parent=parent or self,
            message_type=msg_type,
            buttons=Gtk.ButtonsType.OK,
            text=title,
            secondary_text=text,
        )
        md.run()
        md.destroy()

    def _confirm(self, title, text, ok_label="OK"):
        md = Gtk.MessageDialog(
            parent=self,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
            secondary_text=text,
        )
        md.add_button("Cancel", Gtk.ResponseType.CANCEL)
        md.add_button(ok_label, Gtk.ResponseType.OK)
        result = md.run() == Gtk.ResponseType.OK
        md.destroy()
        return result


# ── CLI mode ─────────────────────────────────────────────────────────

def cli_status():
    """Print current kiosk configuration."""
    comp = detect_compositor()
    state = read_kiosk_state()
    autologin = is_autologin_enabled()
    img_state = read_boot_image_state()
    snd_state = read_boot_sound_state()

    print(f"Compositor:      {comp}")
    print(f"Auto-login:      {'enabled' if autologin else 'disabled'}")
    print(f"Kiosk mode:      {'enabled' if state['enabled'] else 'disabled'}")
    if state["enabled"]:
        print(f"  App:           {state['app_command']}")
        print(f"  Taskbar:       "
              f"{'hidden' if state['suppress_panel'] else 'shown'}")
        print(f"  Desktop:       "
              f"{'hidden' if state['suppress_desktop'] else 'shown'}")
        print(f"  Auto-restart:  "
              f"{'on' if state['auto_restart'] else 'off'}")
        if state["auto_restart"]:
            print(f"  Restart quit:  "
                  f"{'yes' if state['restart_on_quit'] else 'no'}")
        print(f"  Hide cursor:   "
              f"{'yes' if state['hide_cursor'] else 'no'}")

    print()
    print(f"Boot image:      "
          f"{'custom' if img_state['enabled'] else 'solid black'}")
    if img_state["source"]:
        print(f"  Source:        {img_state['source']}")
    if not img_state["margins_match"]:
        print(f"  WARNING:       Display changed — reapply recommended")

    print(f"Boot sound:      "
          f"{'enabled' if snd_state['enabled'] else 'disabled'}")
    if snd_state["source"]:
        print(f"  Source:        {snd_state['source']}")
    print(f"Shutdown screen: "
          f"{'black' if read_shutdown_black() else 'boot image'}")

    print()
    res = read_display_resolution()
    rotation = read_display_rotation()
    margins = read_margins_from_cmdline()
    print(f"Display:         {res[0]}×{res[1]} rotation={rotation}°")
    print(f"Margins:         L={margins[0]} R={margins[1]} "
          f"T={margins[2]} B={margins[3]}")

    print()
    if LABWC_USER_AUTOSTART.exists():
        print(f"User autostart ({LABWC_USER_AUTOSTART}):")
        print(LABWC_USER_AUTOSTART.read_text())
    else:
        print("No user autostart (system defaults active)")

    if LAUNCH_SCRIPT.exists():
        print(f"\nLaunch script ({LAUNCH_SCRIPT}):")
        print(LAUNCH_SCRIPT.read_text())


# ── Tray Icon ────────────────────────────────────────────────────────

class KioskManagerTray:
    """System tray icon for quick access to Kiosk Manager settings."""

    def __init__(self):
        if AppIndicator3 is None:
            print("[kiosk] AppIndicator3 not available, tray disabled",
                  file=sys.stderr, flush=True)
            Gtk.main_quit()
            return

        icon = ICON_NAME
        if not Gtk.IconTheme.get_default().has_icon(ICON_NAME):
            if ICON_PATH.exists():
                icon = str(ICON_PATH)

        self.indicator = AppIndicator3.Indicator.new(
            "kiosk-manager",
            icon,
            AppIndicator3.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        if ICON_PATH.exists():
            self.indicator.set_icon_theme_path(str(ICON_PATH.parent))

        self._update_menu()
        # Refresh status every 30 seconds
        GLib.timeout_add_seconds(30, self._update_menu)

    def _update_menu(self):
        """Build or rebuild the tray menu."""
        menu = Gtk.Menu()

        state = read_kiosk_state()

        # Status line
        if state["enabled"]:
            app_name = os.path.basename(state["app_command"].split()[0]) \
                if state["app_command"] else "unknown"
            status_text = f"Kiosk: {app_name}"
        else:
            status_text = "Kiosk: OFF"

        status = Gtk.MenuItem(label=status_text)
        status.set_sensitive(False)
        menu.append(status)

        menu.append(Gtk.SeparatorMenuItem())

        # Settings
        settings_item = Gtk.MenuItem(label="Kiosk Settings…")
        settings_item.connect("activate", self._on_settings)
        menu.append(settings_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)
        return True  # keep timeout alive

    def _on_settings(self, _widget):
        """Open the settings window."""
        win = KioskManagerWindow()
        win.connect("destroy", lambda _: None)  # don't quit tray
        win.show_all()


# ── Entry Point ──────────────────────────────────────────────────────


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-v"):
        print(f"kiosk-manager {VERSION}")
        return

    if len(sys.argv) > 1 and sys.argv[1] in ("--cli", "--status", "-s"):
        cli_status()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--tray":
        tray = KioskManagerTray()
        Gtk.main()
        return

    win = KioskManagerWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
