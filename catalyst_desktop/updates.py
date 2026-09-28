"""Check GitHub for a newer CATALYST release. Sends no token, name or research data: an anonymous public read."""
from __future__ import annotations

import json
import re
from urllib.request import Request, urlopen

REPOSITORY = 'mporosoff/CATALYST'
DOWNLOAD_PAGE = f'https://github.com/{REPOSITORY}/releases/latest'
LATEST_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
WINDOWS_LINK = f'https://github.com/{REPOSITORY}/releases/latest/download/CATALYST-Windows.exe'


def version_tuple(text):
    numbers = re.findall(r'\d+', str(text or ''))
    return tuple(int(n) for n in numbers[:3]) + (0,) * (3 - len(numbers[:3]))


def is_newer(latest, current):
    return bool(latest) and version_tuple(latest) > version_tuple(current)


def latest_release(timeout=6, opener=urlopen):
    """Return {'version', 'url', 'notes'} for the newest published release, or None if it cannot be read."""
    try:
        request = Request(LATEST_API, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'CATALYST-desktop'})
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read(512 * 1024).decode('utf-8'))
        tag = str(data.get('tag_name') or '')
        if not re.fullmatch(r'v?\d+(\.\d+){0,2}', tag):
            return None
        return dict(version=tag.lstrip('v'), url=str(data.get('html_url') or DOWNLOAD_PAGE), notes=str(data.get('body') or ''))
    except Exception:
        return None
