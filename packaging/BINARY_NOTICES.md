# Standalone Linux binary

The `eightbitdo` executable bundles CPython and a PyInstaller bootloader. The
`licenses` directory contains their license texts and notices for the Linux
libraries collected by the build. These notices apply to those components;
they do not select a license for this project's own source code.

The executable requires Linux and glibc 2.35 or newer on the named architecture.
It uses the host's trusted CA certificates, USB permissions, and systemd user
session. Python does not need to be installed. Firmware images are downloaded
from the vendor when requested and are not included in this distribution.

Source, build inputs, API documentation, and firmware-validation limits:
https://github.com/trevor-scheer/8bitdo-deck
