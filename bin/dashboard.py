#!/usr/bin/env python3
"""DragonAPI read-only overview for Kindle."""
import datetime
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from PIL import Image, ImageDraw, ImageFont

ROOT = '/mnt/us/extensions/dragonapi-dashboard'
FBINK = '/mnt/us/usbnet/bin/fbink'

def shape_arabic(word):
    """Shape and reverse Arabic letters for Pillow builds without libraqm."""
    forms = {}
    for code in range(0xFB50, 0xFF00):
        char = chr(code)
        parts = unicodedata.decomposition(char).split()
        if len(parts) == 2 and parts[0] in ('<isolated>', '<final>', '<initial>', '<medial>'):
            forms.setdefault(chr(int(parts[1], 16)), {})[parts[0][1:-1]] = char
    shaped = []
    for i, char in enumerate(word):
        current = forms.get(char, {})
        previous = forms.get(word[i - 1], {}) if i else {}
        following = forms.get(word[i + 1], {}) if i + 1 < len(word) else {}
        joins_previous = 'initial' in previous and 'final' in current
        joins_next = 'initial' in current and 'final' in following
        form = 'medial' if joins_previous and joins_next else 'final' if joins_previous else 'initial' if joins_next else 'isolated'
        shaped.append(current.get(form, char))
    return ''.join(reversed(shaped))

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
    cache_path = os.path.join(ROOT, 'agent-quota-cache.json')
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    try:
        with open(os.path.join(ROOT, 'agent-quotas.json'), encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    try:
        timestamp = data.get('timestamp')
        for key, title in [('codex', 'Codex'), ('claudecode', 'Claude Code'), ('antigravity', 'AG')]:
            item = data.get('quotas', {}).get(key, {})
            groups = item.get('groups') or []
            state = item.get('status', 'unavailable')
            if state == 'live' and groups and isinstance(timestamp, (int, float)):
                cache[key] = {'item': item, 'timestamp': timestamp}
            elif (state not in ('live', 'cached') or not groups) and key in cache:
                cached = cache[key]
                cached_time = cached.get('timestamp')
                if isinstance(cached_time, (int, float)) and 0 <= time.time() * 1000 - cached_time <= 15 * 60000:
                    item = cached.get('item', {})
                    groups = item.get('groups') or []
                    state = 'cached'
                    item = dict(item, updatedAt=cached_time)
            if state not in ('live', 'cached') or not groups:
                rows.append((title, '--', '--'))
                continue
            for group in groups:
                label = title if key != 'antigravity' else title + ' ' + str(group.get('label') or group.get('id') or 'model')
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
                rows.append(('  CREDITS', '%s' % format(balance, ',.2f'), ''))
            extra = item.get('extraUsage') or {}
            if extra.get('enabled') and isinstance(extra.get('used'), (int, float)) and isinstance(extra.get('limit'), (int, float)):
                rows.append(('  EXTRA ' + extra.get('currency', 'USD'), '%s / %s' % (format(extra['used'], ',.2f'), format(extra['limit'], ',.2f')), ''))
            elif key == 'claudecode':
                rows.append(('  EXTRA', '--', ''))
        try:
            tmp = cache_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cache, f)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    except (OSError, ValueError, KeyError, TypeError):
        rows.append(('Waiting for Mac sync', '--', '--'))
    return rows, timestamp

def dashboard(config):
    base, token = config['base_url'].rstrip('/'), config['access_token']
    failures = []
    cache_path = os.path.join(ROOT, 'dragonapi-cache.json')
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    identity = hashlib.sha256((base + '\0' + token + '\0' + config.get('scope', 'admin')).encode()).hexdigest()
    if cache.get('_identity') != identity:
        cache = {'_identity': identity}
    def get(label, path, default):
        try:
            data = fetch(base, token, path)
            cache[label] = {'timestamp': time.time(), 'data': data}
            return data if data is not None else default
        except (OSError, ValueError, KeyError):
            saved = cache.get(label, {})
            if isinstance(saved, dict) and 'data' in saved and isinstance(saved.get('timestamp'), (int, float)):
                failures.append(label + ' offline')
                return saved['data'] if saved['data'] is not None else default
            failures.append(label + ' --')
            return default
    now = int(time.time())
    query = urllib.parse.urlencode({'start_timestamp': now - 86400, 'end_timestamp': now})
    admin = config.get('scope', 'admin') == 'admin'
    user = get('account', '/api/user/self', {})
    display_config = get('display', '/api/status', {})
    usage = get('usage', ('/api/data/?' if admin else '/api/data/self?') + query, [])
    host = get('host', '/api/system/host-metrics', {}) if admin else {}
    temperature = get('temperature', '/api/system/temperature', {}) if admin else {}
    machines = get('VM', '/api/system/virtual-machines', []) if admin else []
    try:
        with open(cache_path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(cache, f)
        os.replace(cache_path + '.tmp', cache_path)
    except OSError:
        pass
    temp = temperature.get('current', {}).get('cpu')
    def quota(value):
        unit = display_config.get('quota_per_unit')
        display = display_config.get('quota_display_type')
        if display == 'USD' and isinstance(unit, (int, float)) and unit > 0 and isinstance(value, (int, float)):
            return '$' + format(value / unit, ',.2f')
        return number(value)
    width = 64
    border = '+' + '-' * (width - 2) + '+'
    def row(text):
        return '| ' + str(text)[:width - 4].ljust(width - 4) + ' |'
    def table_row(name, five, week):
        return row('%-18.18s | %-18.18s | %-18.18s' % (name, five, week))
    agent_rows, stamp = agent_table()
    age = max(0, int((time.time() * 1000 - stamp) / 60000)) if isinstance(stamp, (int, float)) else None
    status = ('Agent sync %dm ago' % age) if age is not None else 'Agent sync --'
    if age is not None and age > 10:
        status += ' / STALE'
    lines = [border, row('AGENT QUOTAS   |   Remaining %   |   ' + status),
             row('Updated ' + datetime.datetime.now().strftime('%m-%d %H:%M:%S') + '    Reset times: device local time'),
             border, table_row('ACCOUNT / MODEL', '5 HOURS / RESET', 'WEEK / RESET'),
             row('-' * 18 + '-+-' + '-' * 18 + '-+-' + '-' * 18)]
    lines.extend(table_row(*entry) for entry in agent_rows)
    lines += [border, row('DRAGONAPI  |  Account and last 24 hours'),
              row('Balance: %-17s Used: %s' % (quota(user.get('quota')), quota(user.get('used_quota')))),
              row('Requests: %-16s 24h: %s requests / %s tokens' % (number(user.get('request_count')), number(sum(x.get('count', 0) or 0 for x in usage)), number(sum(x.get('token_used', 0) or 0 for x in usage))))]
    if admin:
        def percent(part):
            value = host.get(part, {}).get('usage_percent')
            return ('%.0f%%' % value) if isinstance(value, (int, float)) else '--'
        lines.append(row('Host  CPU: %s  RAM: %s  Disk: %s  Temp: %s C' % (percent('cpu'), percent('memory'), percent('storage'), str(temp) if temp is not None else '--')))
        vm_text = ', '.join(str(vm.get('name', 'VM')) + ':' + str(vm.get('state', '--')) for vm in machines[:6]) or 'No data'
        lines.append(row('VM  ' + vm_text))
    lines += [row((', '.join(failures)) if failures else 'DragonAPI updated ' + datetime.datetime.now().strftime('%H:%M:%S')), border]
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
    sync = ('SYNC %d MIN AGO' % age) if age is not None else 'SYNC --'
    if age is not None and age > 10:
        sync += '  /  STALE'
    text(60, 34, 'AGENT QUOTAS', 46, True)
    text(1160, 49, sync.replace('SYNC ', 'AGENT '), 25, True)
    text(60, 102, 'REMAINING', 23, True)
    text(640, 102, '5 HOURS', 25, True)
    text(1115, 102, 'WEEKLY', 25, True)
    line(60, 145, 1588, 145, 3)
    row_height = min(115, int(620 / max(1, len(entries))))
    def quota(x, y, value):
        match = re.match(r'^\s*([\d.]+%)\s+(.*)$', value)
        if match:
            text(x, y + 2, match.group(1), 58, True)
            if float(match.group(1).rstrip('%')) <= 20:
                draw.rectangle((pos(x + 220), pos(y + 16), pos(x + 288), pos(y + 48)), fill=0)
                text(x + 230, y + 17, 'LOW', 19, True, fill=255)
            reset = match.group(2)
            text(x, y + 76, 'RESET ' + reset, 21)
        else:
            text(x, y + 14, value, 38, True)
    for i, (label, five, weekly, detail) in enumerate(entries):
        y = 157 + i * row_height
        text(60, y + 16, label, 31, True)
        if detail:
            text(60, y + 66, detail, 25, True)
        quota(640, y, five)
        quota(1115, y, weekly)
        line(60, y + row_height - 5, 1588, y + row_height - 5, 1)
    footer_y = 157 + len(entries) * row_height + 20
    footer = content.splitlines()
    start = next((i for i, item in enumerate(footer) if 'DRAGONAPI  |' in item), len(footer))
    footer = [item.strip('| ').strip() for item in footer[start:] if item.startswith('|')]
    for i, item in enumerate(footer):
        text(60, footer_y + i * 35, item, 23, i == 0)
    try:
        with open(os.path.join(ROOT, 'words.json'), encoding='utf-8') as f:
            words = json.load(f)
        day = datetime.date.today().toordinal()
        ai = words['ai'][day % len(words['ai'])]
        egyptian = words['egyptian'][day % len(words['egyptian'])]
        chinese_font = '/mnt/us/koreader/fonts/noto/NotoSansCJKsc-Regular.otf'
        arabic_font = '/mnt/us/koreader/fonts/noto/NotoNaskhArabic-Bold.ttf'
        def local_text(x, y, value, size, font_path):
            draw.text((pos(x), pos(y)), str(value), font=ImageFont.truetype(font_path, max(10, pos(size))), fill=0)
        def chinese_lines(x, y, value):
            face = ImageFont.truetype(chinese_font, max(10, pos(19)))
            lines = ['']
            for char in value:
                if draw.textlength(lines[-1] + char, font=face) > pos(335):
                    lines.append('')
                lines[-1] += char
            for index, item in enumerate(lines[:2]):
                draw.text((pos(x), pos(y + index * 25)), item, font=face, fill=0)
        line(800, footer_y, 800, footer_y + 195, 1)
        line(1195, footer_y + 5, 1195, footer_y + 145, 1)
        text(830, footer_y, 'AI ENGLISH', 19, True)
        text(1220, footer_y, 'EGYPTIAN ARABIC', 19, True)
        ai_size = 42
        while ai_size > 26 and draw.textlength(ai['word'], font=font(ai_size, True)) > pos(335):
            ai_size -= 1
        text(830, footer_y + 22, ai['word'], ai_size, True)
        local_text(1220, footer_y + 9, shape_arabic(egyptian['word']), 46, arabic_font)
        text(830, footer_y + 70, ai['pronunciation'], 19)
        text(1220, footer_y + 74, egyptian['pronunciation'], 19)
        chinese_lines(830, footer_y + 96, ai['meaning'])
        chinese_lines(1220, footer_y + 96, egyptian['meaning'] + '  ·  谐音：' + egyptian['chinese'])
        tips = words.get('ai_tips', [])
        if tips:
            local_text(830, footer_y + 155, 'AI知识：' + tips[day % len(tips)], 26, chinese_font)
    except (OSError, ValueError, KeyError, TypeError, IndexError, ZeroDivisionError):
        pass
    # Give the process list the full width below the DragonAPI summary.
    pm_y = footer_y + len(footer) * 35 + 12
    line(60, pm_y, 1588, pm_y, 2)
    try:
        with open(os.path.join(ROOT, 'pm2-status.json'), encoding='utf-8') as f:
            host = json.load(f)
        if not isinstance(host, dict) or not isinstance(host.get('processes'), list):
            raise ValueError('Invalid status')
    except (OSError, ValueError, TypeError):
        host = {}
    processes = host.get('processes', [])
    updated = host.get('timestamp')
    restart_path = os.path.join(ROOT, 'pm2-restart-cache.json')
    try:
        with open(restart_path, encoding='utf-8') as f:
            restart_cache = json.load(f)
    except (OSError, ValueError):
        restart_cache = {}
    if isinstance(updated, (int, float)) and updated != restart_cache.get('timestamp'):
        old_counts = restart_cache.get('counts', {})
        increases = []
        counts = {}
        for proc in processes:
            key = str(proc.get('name', '')) + ':' + str(proc.get('id', ''))
            count = proc.get('restarts')
            if isinstance(count, (int, float)):
                counts[key] = count
                if key in old_counts and count > old_counts[key]:
                    increases.append(key)
        restart_cache = {'timestamp': updated, 'counts': counts, 'increases': increases}
        try:
            with open(restart_path + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(restart_cache, f)
            os.replace(restart_path + '.tmp', restart_path)
        except OSError:
            pass
    increases = set(restart_cache.get('increases', []))
    processes = sorted(processes, key=lambda p: (p.get('status') == 'online', str(p.get('name', '')) + ':' + str(p.get('id', '')) not in increases))
    stale = not isinstance(updated, (int, float)) or time.time() - updated / 1000 > 600
    online = sum(1 for proc in processes if proc.get('status') == 'online')
    if host.get('status') == 'unavailable':
        status = '--'
    elif not host:
        status = 'NO DATA'
    elif not processes:
        status = 'NO PROCESSES'
    else:
        status = '%d/%d ONLINE' % (online, len(processes))
    if isinstance(updated, (int, float)):
        status += ' / %dm ago' % max(0, int((time.time() * 1000 - updated) / 60000))
    if stale and host:
        status += ' / STALE'
    text(60, pm_y + 8, 'MAC PM2   ' + status, 25, True)
    # Two columns use the full width while keeping all current entries visible.
    max_rows = max(1, (1236 - pm_y - 55) // 49)
    visible_rows = max(1, min(max_rows, (len(processes) + 1) // 2))
    per_page = visible_rows * 2
    page_count = max(1, (len(processes) + per_page - 1) // per_page)
    page = int(time.time() // 60) % page_count
    if page_count > 1:
        text(1515, pm_y + 10, '%d/%d' % (page + 1, page_count), 19)
    for i, proc in enumerate(processes[page * per_page:(page + 1) * per_page]):
        x = 60 + (i // visible_rows) * 770
        y = pm_y + 42 + (i % visible_rows) * 49
        cpu = proc.get('cpuPercent')
        memory = proc.get('memoryBytes')
        cpu_text = '%.1f%%' % cpu if isinstance(cpu, (int, float)) else '--'
        mem_text = '%.0fM' % (memory / 1048576) if isinstance(memory, (int, float)) else '--'
        name = str(proc.get('name', '?'))[:24]
        if str(proc.get('name', '')) + ':' + str(proc.get('id', '')) in increases:
            name += ' !'
        proc_status = str(proc.get('status', '?'))
        text(x, y, name, 22, True)
        text(x + 360, y, '%s  %s  %s  R:%s' % (proc_status, cpu_text, mem_text, proc.get('restarts', '--')), 20, proc_status != 'online')
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
