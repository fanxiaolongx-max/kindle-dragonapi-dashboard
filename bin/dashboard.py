#!/usr/bin/env python3
"""DragonAPI read-only overview for Kindle."""
import datetime
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from PIL import Image, ImageDraw, ImageFont

ROOT = '/mnt/us/extensions/dragonapi-dashboard'
FBINK = '/mnt/us/usbnet/bin/fbink'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def fetch(base, token, path):
    request = urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    ca_file = os.path.join(ROOT, 'ca-certificates.crt')
    context = ssl.create_default_context(cafile=ca_file if os.path.isfile(ca_file) else None)
    with urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=context)).open(request, timeout=10) as response:
        obj = json.load(response)
    if isinstance(obj, dict) and obj.get('success') is False:
        raise ValueError('API request failed')
    return obj.get('data', obj) if isinstance(obj, dict) else obj

def bar(value):
    try:
        n = max(0, min(16, round(float(value) / 6.25)))
        return '[' + '#' * n + '.' * (16 - n) + '] %4.0f%%' % float(value)
    except (TypeError, ValueError):
        return '[................]   --'

def number(value):
    try:
        return format(int(value), ',')
    except (ValueError, TypeError):
        return '--'

def agent_table():
    rows = []
    timestamp = None
    try:
        with open(os.path.join(ROOT, 'agent-quotas.json'), encoding='utf-8') as f:
            data = json.load(f)
        timestamp = data.get('timestamp')
        for key, title in [('codex', 'Codex'), ('claudecode', 'Claude Code'), ('antigravity', 'AG')]:
            item = data.get('quotas', {}).get(key, {})
            groups = item.get('groups') or []
            if item.get('status') not in ('live', 'cached') or not groups:
                rows.append((title, item.get('status', 'unavailable'), '--'))
                continue
            for group in groups:
                label = title if key != 'antigravity' else 'AG ' + str(group.get('label') or group.get('id') or 'model')
                windows = {w.get('id'): w for w in group.get('windows', [])}
                def cell(name):
                    w = windows.get(name, {})
                    pct = w.get('remainingPercent')
                    if not isinstance(pct, (int, float)):
                        return '--'
                    try:
                        reset = datetime.datetime.fromisoformat(str(w.get('resetsAt')).replace('Z', '+00:00')).astimezone().strftime('%m/%d %H:%M')
                    except (ValueError, TypeError):
                        reset = '--'
                    return '%5.1f%% %s' % (pct, reset)
                rows.append((label, cell('5h'), cell('weekly')))
            balance = item.get('creditsBalance')
            if isinstance(balance, (int, float)):
                rows.append(('  Credits', '%.2f' % balance, ''))
            extra = item.get('extraUsage') or {}
            if extra.get('enabled') and isinstance(extra.get('used'), (int, float)) and isinstance(extra.get('limit'), (int, float)):
                rows.append(('  Spend ' + extra.get('currency', 'USD'), '%.2f / %.2f' % (extra['used'], extra['limit']), ''))
    except (OSError, ValueError, KeyError, TypeError):
        rows.append(('Waiting for Mac sync', '--', '--'))
    return rows, timestamp

def dashboard(config):
    base, token = config['base_url'].rstrip('/'), config['access_token']
    failures = []
    def get(label, path, default):
        try:
            return fetch(base, token, path) or default
        except (OSError, ValueError, KeyError):
            failures.append(label)
            return default
    now = int(time.time())
    query = urllib.parse.urlencode({'start_timestamp': now - 86400, 'end_timestamp': now})
    admin = config.get('scope', 'admin') == 'admin'
    user = get('account', '/api/user/self', {})
    usage = get('usage', ('/api/data/?' if admin else '/api/data/self?') + query, [])
    host = get('host', '/api/system/host-metrics', {}) if admin else {}
    temperature = get('temperature', '/api/system/temperature', {}) if admin else {}
    machines = get('VM', '/api/system/virtual-machines', []) if admin else []
    temp = temperature.get('current', {}).get('cpu')
    width = 64
    border = '+' + '-' * (width - 2) + '+'
    def row(text):
        return '| ' + str(text)[:width - 4].ljust(width - 4) + ' |'
    def table_row(name, five, week):
        return row('%-18.18s | %-18.18s | %-18.18s' % (name, five, week))
    agent_rows, stamp = agent_table()
    age = max(0, int((time.time() * 1000 - stamp) / 60000)) if isinstance(stamp, (int, float)) else None
    status = ('Agent sync %dm ago' % age) if age is not None else 'Agent sync unavailable'
    if age is not None and age > 10:
        status += ' / STALE'
    lines = [border, row('AGENT QUOTAS   |   Remaining %   |   ' + status),
             row('Updated ' + datetime.datetime.now().strftime('%m-%d %H:%M:%S') + '    Reset times: device local time'),
             border, table_row('ACCOUNT / MODEL', '5 HOURS / RESET', 'WEEK / RESET'),
             row('-' * 18 + '-+-' + '-' * 18 + '-+-' + '-' * 18)]
    lines.extend(table_row(*entry) for entry in agent_rows)
    lines += [border, row('DRAGONAPI  |  Account and last 24 hours'),
              row('Balance: %-17s Used: %s' % (number(user.get('quota')), number(user.get('used_quota')))),
              row('Requests: %-16s 24h: %s requests / %s tokens' % (number(user.get('request_count')), number(sum(x.get('count', 0) or 0 for x in usage)), number(sum(x.get('token_used', 0) or 0 for x in usage))))]
    if admin:
        def percent(part):
            value = host.get(part, {}).get('usage_percent')
            return ('%.0f%%' % value) if isinstance(value, (int, float)) else '--'
        lines.append(row('Host  CPU: %s  RAM: %s  Disk: %s  Temp: %s C' % (percent('cpu'), percent('memory'), percent('storage'), str(temp) if temp is not None else '--')))
        vm_text = ', '.join(str(vm.get('name', 'VM')) + ':' + str(vm.get('state', '--')) for vm in machines[:6]) or 'No data'
        lines.append(row('VM  ' + vm_text))
    lines += [row(('Unavailable: ' + ', '.join(failures)) if failures else 'Connected / Auto refresh'), border]
    return '\n'.join(lines)

def render_dashboard(content, width, height):
    """Render a landscape page with prominent percentages and smaller reset times."""
    image = Image.new('L', (width, height), 255)
    draw = ImageDraw.Draw(image)
    scale = min(width / 1648, height / 1236)
    regular = '/mnt/us/koreader/fonts/noto/NotoSans-Regular.ttf'
    bold = '/mnt/us/koreader/fonts/noto/NotoSans-Bold.ttf'
    def font(size, strong=False):
        return ImageFont.truetype(bold if strong else regular, max(10, int(size * scale)))
    def pos(value):
        return int(value * scale)
    def text(x, y, value, size, strong=False, fill=0):
        draw.text((pos(x), pos(y)), str(value), font=font(size, strong), fill=fill)
    def line(x1, y1, x2, y2, thickness=2):
        draw.line((pos(x1), pos(y1), pos(x2), pos(y2)), fill=0, width=max(1, pos(thickness)))

    rows, stamp = agent_table()
    entries = []
    for label, five, weekly in rows:
        if label.startswith('  ') and entries:
            entries[-1][3] = label.strip() + ': ' + five
        else:
            entries.append([label, five, weekly, ''])
    # Keep the footer visible if more model groups are added in future.
    if len(entries) > 6:
        entries = entries[:5] + [['More groups', '--', '--', '']]
    age = max(0, int((time.time() * 1000 - stamp) / 60000)) if isinstance(stamp, (int, float)) else None
    sync = ('SYNC %d MIN AGO' % age) if age is not None else 'SYNC UNAVAILABLE'
    if age is not None and age > 10:
        sync += '  /  STALE'
    text(60, 34, 'AGENT QUOTAS', 46, True)
    text(1160, 49, sync, 25, True)
    text(60, 102, 'REMAINING', 23, True)
    text(640, 102, '5 HOURS', 25, True)
    text(1115, 102, 'WEEKLY', 25, True)
    line(60, 145, 1588, 145, 3)
    row_height = min(145, int(840 / max(1, len(entries))))
    def quota(x, y, value):
        match = re.match(r'^\s*([\d.]+%)\s+(.*)$', value)
        if match:
            text(x, y + 2, match.group(1), 70, True)
            text(x, y + 91, 'RESET  ' + match.group(2), 23)
        else:
            text(x, y + 14, value, 38, True)
    for i, (label, five, weekly, detail) in enumerate(entries):
        y = 157 + i * row_height
        text(60, y + 16, label, 31, True)
        if detail:
            text(60, y + 66, detail, 23)
        quota(640, y, five)
        quota(1115, y, weekly)
        line(60, y + row_height - 5, 1588, y + row_height - 5, 1)
    footer_y = 157 + len(entries) * row_height + 20
    footer = content.splitlines()
    start = next((i for i, item in enumerate(footer) if 'DRAGONAPI  |' in item), len(footer))
    footer = [item.strip('| ').strip() for item in footer[start:] if item.startswith('|')]
    for i, item in enumerate(footer):
        text(60, footer_y + i * 35, item, 23, i == 0)
    path = '/tmp/dragonapi-dashboard.png'
    image.save(path)
    return path

def main():
    with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as f:
        config = json.load(f)
    url = urllib.parse.urlsplit(config['base_url'])
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError('base_url must be HTTPS')
    if not config.get('access_token') or config['access_token'].startswith('PASTE_'):
        raise ValueError('Set access token in config.json')
    while True:
        started = time.monotonic()
        content = dashboard(config)
        info = subprocess.run([FBINK, '-h'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        match = re.search(r'Variable fb info: (\d+)x(\d+)', info)
        width, height = (int(match.group(1)), int(match.group(2))) if match else (1648, 1236)
        path = render_dashboard(content, width, height)
        subprocess.call([FBINK, '-c', '-i', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if '--once' in sys.argv:
            return
        time.sleep(max(1, max(30, int(config.get('refresh_seconds', 60))) - (time.monotonic() - started)))

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print('Dashboard error:', exc, flush=True)
        sys.exit(1)
