# Bundled updater

The `eightbitdo` executable bundles CPython and a PyInstaller bootloader. The
`licenses` directory contains their license texts and notices for dependencies
collected by the build. The executable also embeds these runtime notices; run
`eightbitdo --licenses` to read the notices shipped with that exact version,
including after an executable-only application update. These notices apply to those components;
they do not select a license for this project's own source code.

Linux executables require glibc 2.35 or newer on the named architecture and use
the host's USB permissions and systemd user session. Windows and macOS packages
include their native HID dependencies. Python does not need to be installed.
Firmware images are downloaded
from the vendor when requested and are not included in this distribution.

Source, build inputs, API documentation, and firmware-validation limits:
https://github.com/trevor-scheer/8bitdo-deck
