#!/usr/bin/env python3
"""Collect one host's PM2 status and send it to Kindle over SSH."""
import argparse
import json
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--host', required=True, choices=['mac', 'debian'])
parser.add_argument('--kindle', required=True, help='SSH destination, for example root@kindle')
parser.add_argument('--pm2', default='pm2', help='Path to pm2 executable')
args = parser.parse_args()
try:
    result = subprocess.run([args.pm2, 'jlist'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
    processes = json.loads(result.stdout)
    if not isinstance(processes, list):
        raise ValueError('PM2 returned an invalid process list')
    snapshot = {'timestamp': int(time.time() * 1000), 'processes': []}
    for proc in processes:
        monitor = proc.get('monit') or {}
        environment = proc.get('pm2_env') or {}
        snapshot['processes'].append({'name': proc.get('name', '?'), 'status': environment.get('status', 'unknown'), 'cpuPercent': monitor.get('cpu'), 'memoryBytes': monitor.get('memory'), 'restarts': environment.get('restart_time')})
    # Send only the selected status fields, never PM2 environment variables.
    filename = 'pm2-status.json' if args.host == 'mac' else 'debian-pm2-status.json'
    remote = "import os,sys; p='/mnt/us/extensions/dragonapi-dashboard/%s'; t=p+'.tmp'; open(t,'wb').write(sys.stdin.buffer.read()); os.replace(t,p)" % filename
    subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.kindle, 'python3', '-c', "'" + remote + "'"], input=json.dumps(snapshot).encode(), check=True, timeout=25)
except (OSError, ValueError, subprocess.SubprocessError) as exc:
    print('PM2 collection failed:', exc, file=sys.stderr)
    sys.exit(1)
