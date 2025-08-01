#!/usr/bin/env bash
# setup_sqm_gps_logger.sh
# Prepare a Raspberry Pi to run the SQM-GPS logger and discipline
# the system clock from a BU353-N5 USB GPS.
# Re-runnable: you can use it to “reset” the environment.

set -euo pipefail

### ---------- USER-ADJUSTABLE VARIABLES ---------------------------------
PROJECT_DIR="$HOME/SQM_GPS_Logger"            # repo already cloned here
VENV_DIR="$PROJECT_DIR/venv"                  # virtual-env location
MAIN_PY="$PROJECT_DIR/logging/main.py"        # entry point
GPS_DEVICE="/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller-if00-port0"
SCREEN_NAME="sqm_gps_logger"                  # screen session name
SERVICE_NAME="sqm_gps_logger.service"         # systemd unit file name
# ------------------------------------------------------------------------

echo "=== 1.  Packages ==="
sudo apt-get update
sudo apt-get -y install \
     python3 python3-venv python3-pip \
     gpsd gpsd-clients chrony \
     screen git

echo "=== 2.  Python virtual-env ==="
if [[ -d "$VENV_DIR" ]]; then
  echo "Venv already exists → skipping creation."
else
  python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
if [[ -f "$PROJECT_DIR/requirements.txt" ]]; then
  pip install --upgrade pip
  pip install --requirement "$PROJECT_DIR/requirements.txt"
fi
deactivate

echo "=== 3.  gpsd configuration ==="
sudo tee /etc/default/gpsd >/dev/null <<EOF
START_DAEMON="true"
DEVICES="$GPS_DEVICE"
GPSD_OPTIONS="-n"
USBAUTO="false"
EOF
sudo systemctl enable --now gpsd

echo "=== 4.  chrony drop-in ==="
sudo tee /etc/chrony/conf.d/20-gpsd.conf >/dev/null <<'EOF'
# NMEA time from gpsd (shared-memory segment 0)
refclock SHM 0 offset 0.0 precision 1e-3 poll 3 refid GPS
EOF
sudo systemctl restart chrony

echo "=== 5.  systemd unit (oneshot + screen) ==="
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
sudo tee "$SERVICE_PATH" >/dev/null <<EOF
[Unit]
Description=SQM GPS Logger Service
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'screen -S $SCREEN_NAME -dm bash -c "$VENV_DIR/bin/python $MAIN_PY"'
WorkingDirectory=$PROJECT_DIR/logging
RemainAfterExit=true
User=$USER

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo "=== 6.  Quick checks ==="
gpspipe -r -n 3 | head -n 2
chronyc sources -v | head -n 10
systemctl --no-pager status "$SERVICE_NAME"

cat <<EOS

Setup complete.

• Attach to the logger console:   screen -r $SCREEN_NAME
  (Detach again with ⌃A D.)

• Stop / start the service:       sudo systemctl stop $SERVICE_NAME
                                   sudo systemctl start $SERVICE_NAME

A reboot is recommended to verify that everything comes up cleanly.
EOS

