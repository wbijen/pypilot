#!/bin/bash
# Deploy current branch (default: bno) to the running pypilot service.
# Usage:  ./reinstall.sh [branch]

BRANCH="${1:-master}"

git fetch

if [ -z "$(git diff origin/$BRANCH)" ] && [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/$BRANCH)" ]; then
    echo "Nothing to update (already at latest $BRANCH)"
else
    sudo systemctl stop pypilot.service 2>/dev/null || true

    git checkout "$BRANCH"
    git pull origin "$BRANCH"

    sudo python3 setup.py install

    sudo systemctl start pypilot.service 2>/dev/null || true

    journalctl -u pypilot.service -f 2>/dev/null || echo "Service not managed by systemd; run manually."
fi
