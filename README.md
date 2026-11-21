# 8BitDo Deck

A proposed unified firmware updater for 8BitDo controllers and accessories on Steam Deck.

The intended experience is one app: detect a device or select it manually, identify its controller and receiver firmware separately, show available updates, and guide the user through updating over USB. Broad 8BitDo coverage is the long-term goal; support must be validated per model and hardware revision.

**Status: project definition and feasibility research. No updater code or installable plugin exists yet. No device firmware was changed.**

The proposed first release targets Trevor's original Ultimate Wireless and Ultimate 2 Wireless controllers and their receivers. The exact first-generation controller variant still needs identification. A dock is not assumed to have separately updateable firmware.

- [Implementation proposal](docs/plan.md): Decky first, reusable update engine, milestones, and release criteria. Awaiting approval.
- [Research and device evidence](docs/research.md): official web updater, Linux implementations, observed USB IDs, and unresolved questions.

This is an independent project. The working name does not imply affiliation with 8BitDo.
