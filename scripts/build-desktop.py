"""Build a native app from code and public mapping definitions only."""
from pathlib import Path
import platform
import os
import shutil
import subprocess
import sys

if sys.version_info[:3] != (3, 13, 15):
    raise SystemExit('Release packaging requires Python 3.13.15, matching the validated Windows/macOS workflow.')

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from catalyst_desktop import __version__
backend = 'keyring.backends.Windows' if sys.platform == 'win32' else 'keyring.backends.macOS' if sys.platform == 'darwin' else 'keyring.backends.SecretService'
package_name = 'CATALYST-Windows' if sys.platform == 'win32' else 'CATALYST'
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--onefile' if sys.platform == 'win32' else '--onedir', '--windowed',
    '--name', package_name, '--distpath', str(root / 'desktop-dist'),
    '--workpath', str(root / '.desktop-build' / 'work'), '--specpath', str(root / '.desktop-build'),
    '--paths', str(root), '--hidden-import', backend,
    '--add-data', str(root / 'mappings') + ':mappings',
    str(root / 'scripts' / 'launch-desktop.py')]
if sys.platform == 'darwin':
    command += ['--osx-bundle-identifier', 'edu.rochester.porosoff.catalyst']
build_env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(root / '.desktop-build' / 'cache'))
subprocess.run(command, cwd=root, check=True, env=build_env)
executable = root / 'desktop-dist' / ('CATALYST.app/Contents/MacOS/CATALYST' if sys.platform == 'darwin'
    else 'CATALYST-Windows.exe' if sys.platform == 'win32' else 'CATALYST/CATALYST')
subprocess.run([str(executable), '--self-test'], cwd=root, check=True, timeout=90)
if sys.platform == 'darwin':
    app = root / 'desktop-dist' / 'CATALYST.app'
    staging = root / '.desktop-build' / ('dmg-' + __version__ + '-' + platform.machine())
    staging.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ditto', str(app), str(staging / 'CATALYST.app')], check=True)
    if not (staging / 'Applications').exists():
        (staging / 'Applications').symlink_to('/Applications')
    shutil.copy2(root / 'docs' / 'desktop-quickstart.md', staging / 'READ-ME.md')
    shutil.copy2(root / 'docs' / 'scisure-configuration.md', staging / 'SciSure-configuration.md')
    output = root / 'desktop-dist' / ('CATALYST-Mac-AppleSilicon.dmg' if platform.machine() == 'arm64' else 'CATALYST-Mac-Intel.dmg')
    subprocess.run(['hdiutil', 'create', '-volname', 'CATALYST ' + __version__, '-srcfolder', str(staging),
        '-ov', '-format', 'UDZO', str(output)], check=True)
    subprocess.run(['hdiutil', 'verify', str(output)], check=True)
elif sys.platform == 'win32':
    # Test the distributed one-file executable from a folder with no adjacent Python runtime.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='catalyst-portable-check-') as isolated:
        portable = Path(isolated) / executable.name
        shutil.copy2(executable, portable)
        subprocess.run([str(portable), '--self-test'], cwd=isolated, check=True, timeout=90)
    output = executable
else:
    app = root / 'desktop-dist' / 'CATALYST'
    shutil.copy2(root / 'docs' / 'desktop-quickstart.md', app / 'READ-ME.md')
    shutil.copy2(root / 'docs' / 'consortium-workflow.md', app / 'consortium-workflow.md')
    shutil.copy2(root / 'docs' / 'scisure-integration-review.md', app / 'scisure-integration-review.md')
    output = shutil.make_archive(str(root / 'desktop-dist' / f'CATALYST-{__version__}-{platform.system()}-{platform.machine()}'), 'zip', root_dir=app.parent, base_dir=app.name)
print(f'Built {output}')
