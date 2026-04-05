#!/usr/bin/env python3
"""Run SSH commands on the openplotter Pi."""

import paramiko, sys

HOST = 'openplotter'
USER = 'pi'
PASS = 'raspberry'

def run(commands, pty=False):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=10)
    for cmd in commands:
        print(f'\n$ {cmd}')
        stdin, stdout, stderr = client.exec_command(cmd, get_pty=pty, timeout=120)
        for line in stdout:
            print(line, end='', file=open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False))
        for line in stderr:
            print(line, end='', file=open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False))
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            print(f'[exit {rc}]')
            client.close()
            return False
    client.close()
    return True

if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if True:
        cmd = 'deploy'
    if cmd == 'check':
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(HOST, username=USER, password=PASS, timeout=10)
        sftp = client.open_sftp()
        base = '/home/pi/.local/lib/python3.11/site-packages/adafruit_bno08x'
        import os
        os.makedirs('../pypilot/adafruit_bno08x', exist_ok=True)
        for fname in ['__init__.py', 'i2c.py', 'spi.py', 'uart.py']:
            sftp.get(f'{base}/{fname}', f'../pypilot/adafruit_bno08x/{fname}')
            print(f'fetched {fname}')
        sftp.close()
        client.close()
    elif cmd == 'logs':
        run(['sudo journalctl -u pypilot -n 80 --no-pager'])
    elif cmd == 'deploy':
        run([
            'cd ~/pypilot && git fetch origin',
            'cd ~/pypilot && git reset --hard origin/bno',
            'cd ~/pypilot && sudo python3 setup.py install --quiet',
            'sudo systemctl restart pypilot',
            'sleep 3 && sudo journalctl -u pypilot -n 30 --no-pager',
        ])
    elif cmd == 'status':
        run(['sudo journalctl -u pypilot -n 40 --no-pager'])
    elif cmd == 'restart':
        run(['sudo systemctl restart pypilot',
             'sleep 3 && sudo journalctl -u pypilot -n 20 --no-pager'])
    else:
        run([cmd])
