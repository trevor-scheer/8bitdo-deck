# Contribute device support

Use the repository's [add-8bitdo-device skill](.agents/skills/add-8bitdo-device/SKILL.md) to take an additional controller, receiver, or accessory from discovery through implementation, validation, and an upstream pull request. It also covers reviewing new firmware versions for an existing family.

Open a checkout of this repository in a coding agent that supports skills and ask:

```text
Use $add-8bitdo-device to add support for my 8BitDo MODEL and prepare an upstream PR.
I have HARDWARE connected to HOST. Its current firmware is VERSION.
Start with discovery and offline validation; ask before state-changing hardware tests.
```

Replace `MODEL`, `HARDWARE`, `HOST`, and `VERSION` with what you know; unknown values are fine. If your agent does not discover repository skills, tell it to read `.agents/skills/add-8bitdo-device/SKILL.md` and follow that workflow. The skill is an agent-guided procedure, not a standalone firmware updater.

The workflow includes component identification, official firmware/protocol research, implementation boundaries, failure tests, recovery, bounded hardware checks, evidence documentation, and PR submission. You can contribute discovery/catalog support without flashing a device. If you lack hardware, the contribution must state that limitation and leave unvalidated operations blocked.

Keep proprietary updater binaries, firmware dumps, raw captures, and unique device identifiers out of the contribution. Use the skill's evidence template to document reproducible findings and the exact support claim. Firmware installation remains explicitly confirmed; invoking the skill does not authorize it.

See [Development](README.md#development) for the checks to run and [protocol requirements](docs/protocol.md#authorization-and-hardware-qualification) for experimental versus hardware-validated support. The contribution should pass CI and disclose untested cases before review. Submitting a PR does not merge it or publish a release.
