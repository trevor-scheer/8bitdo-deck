"""Use existing SteamOS recovery access, with scoped setup for other systems."""

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading


RULE_PATH = Path('/etc/udev/rules.d/70-8bitdo-deck.rules')
RECEIPT_PATH = RULE_PATH.with_suffix('.installed')
RULE_TEXT = '''# 8BitDo Deck: recovery interfaces only; access belongs to the active local user.
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", ATTRS{idProduct}=="3208", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", ATTRS{idProduct}=="3002", MODE="0660", TAG+="uaccess"
'''
UDEV_RULE_DIRS = tuple(Path(p) / 'udev/rules.d' for p in ('/etc', '/run', '/usr/local/lib', '/usr/lib'))
STEAMOS_RULE_NAME = '70-steam-jupiter-input.rules'
STEAMOS_RULE_LINES = (
    'SUBSYSTEMS=="usb", TAG+="uaccess"',
    'KERNEL=="hidraw*", TAG+="uaccess"',
    'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"',
)


def _trusted_text(path: Path) -> str | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return None
        return path.read_text()
    except (OSError, UnicodeError):
        return None


def steamos_recovery_access_available() -> bool:
    """Recognize the reviewed distro policy without interpreting arbitrary udev code.

    A same-name local override or /dev/null mask takes precedence over vendor
    rules. Actual device access is still checked when opening each HID node;
    the policy does not guarantee that enumeration/ACL assignment has finished.
    """
    for directory in UDEV_RULE_DIRS:
        path = directory / STEAMOS_RULE_NAME
        if not os.path.lexists(path):
            continue
        text = _trusted_text(path)
        if text is None:
            return False
        lines = tuple(line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#'))
        return lines == STEAMOS_RULE_LINES
    return False


def recovery_access_configured() -> bool:
    if steamos_recovery_access_available():
        return True
    return (
        _trusted_text(RULE_PATH) == RULE_TEXT
        and _trusted_text(RECEIPT_PATH) == hashlib.sha256(RULE_TEXT.encode()).hexdigest() + '\n'
    )


class RecoveryAccess:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self._process = None
        self._lock = threading.Lock()
        self._error = ''

    def status(self) -> dict:
        configured = recovery_access_configured()
        pending = self._process is not None and self._process.poll() is None
        if self._process is not None and not pending and self._process.returncode:
            self._error = 'System authorization did not complete. Retry in Desktop Mode and approve the administrator prompt.'
        message = ('Recovery USB access is available. No additional setup is needed.' if configured else
                   'Receiver recovery needs access to two 8BitDo USB interfaces. Setup asks SteamOS for administrator authorization.')
        if pending:
            message = 'Approve the SteamOS administrator prompt to configure receiver recovery access.'
        elif not configured and self._error:
            message = self._error
        return {'configured': configured and not pending, 'pending': pending,
                'can_setup': shutil.which('pkexec') is not None, 'message': message}

    def setup(self) -> dict:
        with self._lock:
            status = self.status()
            if status['configured'] or status['pending']:
                return status
            executable = shutil.which('pkexec')
            script = Path(__file__).resolve().parent.parent / 'scripts/install-udev-rules.sh'
            if not executable or not script.is_file():
                self._error = 'The system permission installer is unavailable. See the installation guide.'
                return self.status()
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            environment = dict(os.environ)
            environment.setdefault('DISPLAY', ':0')
            environment.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
            environment.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path=/run/user/{os.getuid()}/bus')
            self._error = ''
            try:
                with (self.state_dir / 'recovery-access.log').open('ab') as log:
                    self._process = subprocess.Popen(
                        [executable, '--disable-internal-agent', '/bin/bash', str(script)],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                        env=environment, start_new_session=True, close_fds=True,
                    )
            except OSError:
                self._error = 'Could not open the SteamOS authorization prompt.'
            return self.status()
