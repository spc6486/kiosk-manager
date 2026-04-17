# Kiosk Manager

GTK3 settings application and system tray icon for configuring kiosk mode,
boot image, and boot sound on Raspberry Pi OS Bookworm with labwc.

Works on any Raspberry Pi running Pi OS Bookworm with the labwc compositor.

## Requirements

- **Raspberry Pi OS Bookworm** (Desktop image, not Lite)
- **labwc compositor** (default since Pi OS Bookworm, Oct 2024)
- **LightDM** display manager (default on Pi OS Desktop)

The kiosk features (app launch, desktop suppression, auto-restart) are
specific to the `LXDE-pi-labwc` session and labwc's autostart system.
They will not work with Wayfire, sway, or X11 window managers.

Boot image, boot sound, shutdown screen, and auto-login work regardless
of compositor since they use Plymouth, systemd, and raspi-config.

The installer will check for and install these dependencies automatically:
`python3-gi`, `python3-pil`, `gir1.2-ayatanaappindicator3-0.1`, `sox`,
`plymouth`, `plymouth-themes`, `unclutter`.

### Wayfire Note

Pi OS shipped Wayfire as the default compositor before October 2024. If
your system still uses Wayfire (check with `pgrep wayfire`), the kiosk
tab will not function. You can switch to labwc via:

```bash
sudo raspi-config
# Advanced Options → Wayland → labwc
sudo reboot
```

## Features

### Kiosk Tab
- **Auto-login** — enable/disable LightDM desktop autologin
- **Kiosk app launch** — select an application to run automatically on boot
- **Desktop suppression** — hide taskbar (wf-panel-pi) and/or desktop
  icons (pcmanfm) while the kiosk app runs; both restore on app exit
- **Auto-restart on crash** — restart the app up to 5 times on non-zero
  exit; clean quit (exit 0) restores the desktop normally
- **Hide mouse cursor** — hide the cursor via `unclutter` while the kiosk
  app runs

### Boot Tab
- **Boot image** — custom Plymouth splash screen, automatically scaled
  (fill or fit) to native display resolution and rotated for physically
  inverted displays
- **Boot sound** — convert any audio file to 48kHz 16-bit WAV with 1.5s
  warmup silence for HDMI audio; enable/disable toggle
- **Shutdown screen** — optionally mask Plymouth on shutdown for a clean
  black screen instead of showing the boot image

### System Tray
- AppIndicator tray icon shows kiosk status and provides quick access
  to the settings window

## Install

```bash
git clone https://github.com/spc6486/kiosk-manager.git
cd kiosk-manager
./install.sh
```

Or from a tarball:

```bash
tar xzf kiosk-manager.tar.gz
cd kiosk-manager
./install.sh
```

The installer handles all dependencies (`python3-gi`, `python3-pil`, `sox`,
`plymouth`, `unclutter`, etc.), creates sudoers rules, menu entry, and
autostart for the tray icon.

## Usage

```bash
kiosk-manager          # Open settings GUI
kiosk-manager --tray   # Run as tray icon (starts automatically on login)
kiosk-manager --cli    # Print current configuration to stdout
```

Or open from **Preferences → Kiosk Manager** in the desktop menu.

## How It Works

### Kiosk Mode

The `LXDE-pi-labwc` session always runs `/etc/xdg/labwc/autostart`. A user
autostart at `~/.config/labwc/autostart` is additive, not a replacement.

To suppress desktop components, the kiosk manager comments out the
`pcmanfm` and `wf-panel-pi` lines in the system autostart. A generated
launch script (`/opt/kiosk-manager/kiosk-launch.sh`) runs the selected
application and restores the suppressed components when it exits.

Background services (battery monitor, display calibrator, brightness
control, etc.) always run via `lxsession-xdg-autostart`.

### Boot Image

The source image is processed through a pipeline:
1. Scale to fill (or fit) the full native display resolution
2. Rotate 180° if the display is physically inverted (detected from kanshi)
3. Save as a full-resolution PNG

The kernel's `video=` margins in `cmdline.txt` handle hiding edge pixels
behind the display bezel — the image renders to the full framebuffer just
like the desktop does.

Installed as both the Plymouth theme (`custom-boot`) and firmware splash.
Rebuilds initramfs on apply (~30 seconds).

### Boot Sound

Source audio is converted to 48kHz 16-bit stereo WAV with 1.5s of silence
prepended. The silence serves as an HDMI audio warmup — the audio link
establishes while the silent portion streams, so the audible chime plays
without being clipped. This eliminates the need for sleep delays in
playback scripts. If no `startup-sound.service` exists, a basic one is
created on first sound apply.

## Recovery

If your kiosk app crashes and you see a bare screen:

```bash
# Via SSH — break the restart loop:
touch ~/.kiosk-exit

# Restore full desktop:
sudo sed -i 's/^# *\(.*lwrespawn.*\)/\1/' /etc/xdg/labwc/autostart
sed -i '/kiosk-launch/d' ~/.config/labwc/autostart
sudo reboot
```

Or press **Ctrl+Alt+F2** for a text console, log in, and run the same
commands.

## File Locations

### Application
| Path | Contents |
|------|----------|
| `/opt/kiosk-manager/` | Application files |
| `/opt/kiosk-manager/kiosk-launch.sh` | Generated launch wrapper |
| `/usr/local/bin/kiosk-manager` | Launcher script |
| `/usr/share/applications/kiosk-manager.desktop` | Menu entry |
| `/etc/xdg/autostart/kiosk-manager.desktop` | Tray autostart |
| `/etc/sudoers.d/kiosk-manager` | Sudo rules |

### User Data
| Path | Contents |
|------|----------|
| `~/.local/share/kiosk-manager/backups/` | Config backups |
| `~/.local/share/kiosk-manager/sources/` | Source images/sounds |
| `~/.config/labwc/autostart` | User session autostart |

### System Files Modified
| Path | Purpose |
|------|---------|
| `/etc/xdg/labwc/autostart` | Comment/uncomment desktop components |
| `/etc/lightdm/lightdm.conf` | Auto-login (via raspi-config) |
| `/usr/share/plymouth/themes/custom-boot/` | Boot image theme |
| `/boot/firmware/splash.png` | Firmware splash |
| `~/sounds/bootchime.wav` | Processed boot sound |

## Uninstall

```bash
sudo /opt/kiosk-manager/install.sh --uninstall
```

Removes the application, launcher, menu entry, tray autostart, and sudoers
rules. Kiosk configuration and boot settings are preserved.

## Known Limitations

- **System autostart can be overwritten.** The kiosk manager suppresses
  desktop components by commenting out lines in `/etc/xdg/labwc/autostart`.
  A Pi OS update or `raspi-config` change may rewrite this file, restoring
  the desktop during kiosk mode. If you see the desktop alongside your kiosk
  app after an update, open Kiosk Manager and click Apply again.

- **Plymouth theme can be overwritten.** Apt upgrades to Plymouth or changes
  via `raspi-config` may replace the custom boot theme. The source image is
  preserved in `~/.local/share/kiosk-manager/sources/` — reapply from the
  Boot tab to restore it.

- **Boot sound service is not created by the installer.** On a fresh Pi OS
  install, the "Play sound on boot" checkbox is greyed out until you select
  a sound file and click Apply, which creates the service automatically.

## License

MIT
