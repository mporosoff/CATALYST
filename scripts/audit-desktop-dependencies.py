"""Build-time check of public dependency versions against PyPI advisory records.

Never imported by the desktop application. Sends only package names and versions.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import sys
from urllib.parse import quote
from urllib.request import urlopen


def inspect(package):
    name, version = package
    try:
        with urlopen(f'https://pypi.org/pypi/{quote(name, safe="")}/{quote(version, safe="")}/json', timeout=30) as response:
            content = response.read(5_000_001)
        if len(content) > 5_000_000:
            raise ValueError('Oversized advisory response')
        data = json.loads(content)
        vulnerabilities = data['vulnerabilities']
        if not isinstance(vulnerabilities, list):
            raise ValueError('Unsupported advisory response')
        return dict(name=name, version=version, status='checked', vulnerabilities=[
            dict(id=v['id'], link=v.get('link'), fixed_in=v.get('fixed_in')) for v in vulnerabilities])
    except Exception as error:
        return dict(name=name, version=version, status='not checked', error_type=type(error).__name__)


if __name__ == '__main__':
    packages = sorted((d.metadata['Name'], d.version) for d in distributions())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(inspect, packages))
    passed = all(r['status'] == 'checked' and not r['vulnerabilities'] for r in results)
    report = dict(format='catalyst-dependency-audit/1', checked_at=datetime.now(timezone.utc).isoformat(),
        system=platform.system(), architecture=platform.machine(), python=platform.python_version(),
        source='PyPI version-specific vulnerability records', passed=passed, packages=results,
        scope='Known published advisories for installed Python distributions; not a security certification or an audit of the bundled interpreter and OS.')
    output = Path(__file__).resolve().parents[1] / 'desktop-dist' / f'dependency-audit-{platform.system()}-{platform.machine()}.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Checked {len(results)} installed distributions: {"passed" if passed else "failed or incomplete"}.')
    for result in results:
        if result.get('vulnerabilities') or result['status'] != 'checked':
            print(json.dumps(result))
    sys.exit(0 if passed else 1)
