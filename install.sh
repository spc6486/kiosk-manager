#!/bin/bash
set -e

# ── Kiosk Manager Installer ─────────────────────────────────────────
# Installs to /opt/kiosk-manager with Preferences menu entry.
# No system tray — settings-only app.
# Usage: ./install.sh              — install
#        sudo ./install.sh --uninstall  — remove

INSTALL_DIR="/opt/kiosk-manager"
LAUNCHER="/usr/local/bin/kiosk-manager"
DESKTOP_SYS="/usr/share/applications/kiosk-manager.desktop"
AUTOSTART_SYS="/etc/xdg/autostart/kiosk-manager.desktop"
SUDOERS="/etc/sudoers.d/kiosk-manager"
ICON_DIR="/usr/share/icons/hicolor/scalable/apps"

APP_FILES=(
    kiosk-manager.py
    kiosk-manager.svg
)

USER_REAL="${SUDO_USER:-$USER}"
HOME_REAL=$(eval echo "~$USER_REAL")

info()  { echo -e "  \033[1;34m→\033[0m $*"; }
ok()    { echo -e "  \033[1;32m✓\033[0m $*"; }
err()   { echo -e "  \033[1;31m✗\033[0m $*"; }

# ── Uninstall ────────────────────────────────────────────────────────

if [ "${1:-}" = "--uninstall" ]; then
    echo ""
    echo "╔══════════════════════════════════════════════╗"
    echo "║  Uninstalling Kiosk Manager                  ║"
    echo "╚══════════════════════════════════════════════╝"
    echo ""

    sudo rm -f "$LAUNCHER"
    sudo rm -f "$DESKTOP_SYS"
    sudo rm -f "$AUTOSTART_SYS"
    sudo rm -f "$SUDOERS"
    sudo rm -f "$ICON_DIR/kiosk-manager.svg"
    sudo rm -rf "$INSTALL_DIR"

    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        sudo gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
    fi

    ok "Kiosk Manager uninstalled."
    echo ""
    echo "  Note: Kiosk configuration is preserved."
    echo "  To restore normal desktop boot:"
    echo "    sudo sed -i 's/^# *\(.*lwrespawn.*\)/\1/' /etc/xdg/labwc/autostart"
    echo "    sed -i '/kiosk-launch/d' ~/.config/labwc/autostart"
    echo "    sudo raspi-config nonint do_boot_behaviour B3"
    echo ""
    exit 0
fi

# ── Install ──────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  Installing Kiosk Manager                    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$EUID" -eq 0 ] && [ -z "$SUDO_USER" ]; then
    err "Don't run as root. Run as your normal user."
    exit 1
fi

if ! sudo -n true 2>/dev/null; then
    info "This installer needs sudo. You may be prompted for your password."
    echo ""
fi

# ── 1. System dependencies ──────────────────────────────────────────

info "Checking system packages..."
NEED_PKGS=""
for pkg in python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 libayatana-appindicator3-1 python3-pil sox plymouth plymouth-themes unclutter; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        NEED_PKGS="$NEED_PKGS $pkg"
    fi
done

if [ -n "$NEED_PKGS" ]; then
    info "Installing:$NEED_PKGS"
    sudo apt-get update -qq
    sudo apt-get install -y -qq $NEED_PKGS > /dev/null 2>&1
fi
ok "Dependencies satisfied"

# ── 2. Install application files ────────────────────────────────────

info "Installing application files..."
sudo mkdir -p "$INSTALL_DIR"

for f in "${APP_FILES[@]}"; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        sudo cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
    else
        err "Missing file: $f"
        exit 1
    fi
done

sudo cp "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/install.sh"
sudo chmod 755 "$INSTALL_DIR/kiosk-manager.py"
sudo chmod 755 "$INSTALL_DIR/install.sh"
ok "Files installed to $INSTALL_DIR"

# Create user data directories
info "Creating data directories..."
sudo -u "$USER_REAL" mkdir -p "$HOME_REAL/.local/share/kiosk-manager/backups"
sudo -u "$USER_REAL" mkdir -p "$HOME_REAL/.local/share/kiosk-manager/sources"
ok "Data directories created"

# ── 3. Icon ─────────────────────────────────────────────────────────

info "Installing icon..."
sudo mkdir -p "$ICON_DIR"
sudo cp "$INSTALL_DIR/kiosk-manager.svg" "$ICON_DIR/kiosk-manager.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    sudo gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
fi
ok "Icon installed"

# ── 4. Sudoers rules ────────────────────────────────────────────────

info "Creating sudoers rules..."
sudo tee "$SUDOERS" > /dev/null <<EOF
# Kiosk Manager — passwordless commands
# Kiosk mode
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/raspi-config nonint do_boot_behaviour *
$USER_REAL ALL=(ALL) NOPASSWD: /usr/sbin/reboot
$USER_REAL ALL=(ALL) NOPASSWD: /sbin/reboot
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/xdg/labwc/autostart
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/tee $INSTALL_DIR/kiosk-launch.sh
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/chmod 755 $INSTALL_DIR/kiosk-launch.sh
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/rm -f $INSTALL_DIR/kiosk-launch.sh
# Boot image (Plymouth)
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/install -d /usr/share/plymouth/themes/custom-boot
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/cp -f * /usr/share/plymouth/themes/custom-boot/Boot.png
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/cp -f * /boot/firmware/splash.png
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/tee /usr/share/plymouth/themes/custom-boot/custom-boot.script
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/tee /usr/share/plymouth/themes/custom-boot/custom-boot.plymouth
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/plymouth-set-default-theme -R custom-boot
# Boot sound
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable startup-sound.service
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable startup-sound.service
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/systemctl daemon-reload
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/systemd/system/startup-sound.service
# Shutdown screen
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/systemctl mask plymouth-poweroff.service plymouth-reboot.service
$USER_REAL ALL=(ALL) NOPASSWD: /usr/bin/systemctl unmask plymouth-poweroff.service plymouth-reboot.service
EOF
sudo chmod 440 "$SUDOERS"
if ! sudo visudo -c -f "$SUDOERS" >/dev/null 2>&1; then
    err "Invalid sudoers file — removing"
    sudo rm -f "$SUDOERS"
else
    ok "Sudoers rules created"
fi

# ── 5. Launcher and menu entry ──────────────────────────────────────

info "Creating launcher..."
sudo tee "$LAUNCHER" > /dev/null <<EOF
#!/bin/sh
cd "$INSTALL_DIR"
exec python3 kiosk-manager.py "\$@"
EOF
sudo chmod 755 "$LAUNCHER"
ok "Launcher: $LAUNCHER"

info "Creating desktop entry..."
sudo tee "$DESKTOP_SYS" > /dev/null <<'EOF'
[Desktop Entry]
Type=Application
Name=Kiosk Manager
Comment=Configure kiosk mode, auto-login, and app launch
Exec=kiosk-manager
Icon=kiosk-manager
Categories=Settings;System;
Keywords=kiosk;autostart;boot;login;
EOF
ok "Menu entry: Preferences → Kiosk Manager"

info "Setting up tray autostart..."
sudo tee "$AUTOSTART_SYS" > /dev/null <<EOF
[Desktop Entry]
Type=Application
Name=Kiosk Manager Tray
Comment=System tray for kiosk mode
Exec=sh -c 'sleep 3 && $LAUNCHER --tray'
Icon=kiosk-manager
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
ok "Tray autostart: $AUTOSTART_SYS"

# ── Done ─────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  Installation Complete!                      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Launch:         kiosk-manager"
echo "  CLI status:     kiosk-manager --cli"
echo "  Menu:           Preferences → Kiosk Manager"
echo "  Tray:           Starts automatically on login"
echo ""
echo "  Features:"
echo "    • Auto-login toggle"
echo "    • Kiosk app launch with desktop suppression"
echo "    • Auto-restart on crash (with escape mechanism)"
echo "    • Hide mouse cursor option"
echo "    • Boot image (with rotation/margin processing)"
echo "    • Boot sound (with format conversion)"
echo "    • Shutdown screen control"
echo "    • System tray icon for quick access"
echo ""
echo "  Uninstall:      sudo $INSTALL_DIR/install.sh --uninstall"
echo ""
