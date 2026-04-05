#!/bin/sh
set -eu

GPS_DEVICE="${1:-}"

if [ -z "$GPS_DEVICE" ]; then
  if [ -e /dev/ttyOP_gps ]; then
    GPS_DEVICE="/dev/ttyOP_gps"
  elif [ -e /dev/ttyUSB0 ]; then
    GPS_DEVICE="/dev/ttyUSB0"
  else
    echo "No GPS device found. Pass a device path explicitly." >&2
    exit 1
  fi
fi

if [ ! -e "$GPS_DEVICE" ]; then
  echo "GPS device does not exist: $GPS_DEVICE" >&2
  exit 1
fi

sudo tee /etc/default/gpsd >/dev/null <<EOF
START_DAEMON="true"
USBAUTO="true"
DEVICES="$GPS_DEVICE"
GPSD_OPTIONS="-n -b"
EOF

sudo systemctl daemon-reload
sudo systemctl restart gpsd.socket
sudo systemctl restart gpsd
sleep 2
sudo gpsdctl add "$GPS_DEVICE" || true
systemctl is-active gpsd
gpspipe -w -n 6 2>/dev/null || true
