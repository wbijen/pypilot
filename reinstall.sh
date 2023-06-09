#!/bin/bash

# Stop pypilot service
sudo systemctl stop pypilot.service

# Perform git fetch and pull
git fetch
git pull

# Install the project
sudo python3 setup.py install

# Start pypilot service
sudo systemctl start pypilot.service

# Watch the log
journalctl -u pypilot.service -f