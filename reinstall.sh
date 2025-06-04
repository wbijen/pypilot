#!/bin/bash

# Perform git fetch and check for changes
git fetch

if [ -z "$(git diff origin/master)" ]; then
    echo "Nothing to update"
else
    # Stop pypilot service
    sudo systemctl stop pypilot.service

    # If changes are found, pull them
    git pull
    
    # Install the project
    sudo python3 setup.py install

    # Start pypilot service
    sudo systemctl start pypilot.service

    # Watch the log
    journalctl -u pypilot.service -f
fi
