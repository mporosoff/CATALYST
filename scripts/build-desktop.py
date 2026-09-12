"""Build a native app from code and public mapping definitions only."""
from pathlib import Path
import platform
import os
import shutil
import subprocess
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from catalyst_desktop import __version__
backend = 'keyring.backends.Windows' if sys.platform == 'win32' else 'keyring.backends.macOS' if sys.platform == 'darwin' else 'keyring.backends.SecretService'
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
    '--name', 'CATALYST', '--distpath', str(root / 'desktop-dist'),
    '--workpath', str(root / '.desktop-build' / 'work'), '--specpath', str(root / '.desktop-build'),
    '--paths', str(root), '--hidden-import', backend,
    '--add-data', str(root / 'mappings') + ':mappings',
    str(root / 'scripts' / 'launch-desktop.py')]
if sys.platform == 'darwin':
    command += ['--osx-bundle-identifier', 'edu.rochester.porosoff.catalyst']
build_env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(root / '.desktop-build' / 'cache'))
subprocess.run(command, cwd=root, check=True, env=build_env)
executable = root / 'desktop-dist' / ('CATALYST.app/Contents/MacOS/CATALYST' if sys.platform == 'darwin'
    else 'CATALYST/CATALYST.exe' if sys.platform == 'win32' else 'CATALYST/CATALYST')
subprocess.run([str(executable), '--self-test'], cwd=root, check=True, timeout=45)
if sys.platform == 'darwin':
    app = root / 'desktop-dist' / 'CATALYST.app'
    output = root / 'desktop-dist' / f'CATALYST-{__version__}-macOS-{platform.machine()}.zip'
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(output)], check=True)
    with zipfile.ZipFile(output, 'a') as archive:
        archive.write(root / 'docs' / 'desktop-quickstart.md', 'READ-ME.md')
        archive.write(root / 'docs' / 'consortium-workflow.md', 'consortium-workflow.md')
else:
    app = root / 'desktop-dist' / 'CATALYST'
    shutil.copy2(root / 'docs' / 'desktop-quickstart.md', app / 'READ-ME.md')
    shutil.copy2(root / 'docs' / 'consortium-workflow.md', app / 'consortium-workflow.md')
    output = shutil.make_archive(str(root / 'desktop-dist' / f'CATALYST-{__version__}-{platform.system()}-{platform.machine()}'), 'zip', root_dir=app.parent, base_dir=app.name)
print(f'Built {output}')
