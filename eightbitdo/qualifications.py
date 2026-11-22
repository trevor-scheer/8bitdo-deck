"""Developer-reviewed write authorizations, independent of user-selected models.

Add records only with the hardware evidence described in docs/protocol.md.
Neither downloaded metadata nor a UI request may populate this collection.
"""

from .protocol import DeviceIdentity, FirmwareImage, FlashAuthorization, FlashQualification, SourceRegion, SourceReviewedAuthorization
from .legacy_protocol import LegacySourceReviewedAuthorization, ReceiverInitialization
from .wireless import WirelessAuthorization, WirelessTarget


QUALIFICATIONS: tuple[FlashQualification, ...] = ()
WIRELESS_AUTHORIZATIONS: tuple[WirelessAuthorization, ...] = (
    WirelessAuthorization(
        controller=SourceReviewedAuthorization(
            product_id=0x6012,
            hardware_revision=None,
            image_revision=0,
            image_sha256="968edee528c23d56e2059288f90c109fa5135f060de5b403e72d0b6c50f5eb87",
            evidence=(
                "Experimental Ultimate 2 Wireless controller 1.09 to official 1.10 through receiver 1.06. "
                "The exact controller and receiver bootloaders and installed applications must match reviewed fingerprints. "
                "Controller edition 1 is required; hardware revision is unreported. "
                "A controller running 1.10 completed USB boot inspection and return with unchanged firmware on 2026-11-22. "
                "The 1.09 radio transaction and USB rescue protocol were source-reviewed and tested with simulated failures. "
                "A complete wireless transfer, USB rescue reinstall, and power-loss recovery remain untested on hardware. "
                "Keep a USB data cable available for recovery. See docs/devices/ultimate2-wireless.md "
                "and docs/devices/ultimate2-controller-recovery.md."
            ),
            source_versions=(109,),
            source_regions=(
                SourceRegion(0, 0x5000, "faab5fff75e88556749d15980f84926592fc04329da2866e9af36919af529fa5"),
                SourceRegion(0x5000, 145408, "6a2ff4de75982f398a2d19a57f1c43f1bda98e4154f1e3b9686ee41678c7b3a1"),
            ),
        ),
        receiver_version_raw=106,
        receiver_source_regions=(
            SourceRegion(0, 0x5000, "20b9904986708ffae77491630faa24a5fb98ef073cdb557488a82be05e9ce5e7"),
            SourceRegion(0x5000, 40448, "e5bc101ea9b9f1c970a7fbffe4a92261400ad49cbdb58d4190d08a6fd404b3fe"),
        ),
        controller_edition=1,
        minimum_battery_percent=30,
    ),
)
SOURCE_REVIEWED_AUTHORIZATIONS: tuple[SourceReviewedAuthorization, ...] = (
    SourceReviewedAuthorization(
        product_id=0x6013,
        hardware_revision=None,
        image_revision=0,
        image_sha256="1018a299f3662098fcd7d2a71789d1290bb7603dd1998fe03759c5643cd05401",
        evidence=(
            "Ultimate 2 Wireless receiver 1.01 to official 1.06 only. "
            "Installed bootloader and application fingerprints, full-block checksum/readback, "
            "and an unchanged-firmware USB recovery round trip verified on 2026-11-21. "
            "Hardware revision is unreported; compatibility is constrained by product identity "
            "and exact installed code. First firmware installation remains experimental: "
            "activation, pending-image copy, and power-loss recovery are not hardware-validated. "
            "See docs/protocol.md and docs/receiver-recovery.md."
        ),
        source_versions=(101,),
        source_regions=(
            SourceRegion(0, 0x5000, "20b9904986708ffae77491630faa24a5fb98ef073cdb557488a82be05e9ce5e7"),
            SourceRegion(0x5000, 0x7500, "c1fe20ca4b969e9d4b11e21e91dd4767852932508493d0cb6ce3608b10e5914b"),
        ),
    ),
)
LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS: tuple[LegacySourceReviewedAuthorization, ...] = (
    LegacySourceReviewedAuthorization(
        product_id=0x6008,
        hardware_revision=None,
        image_revision=0,
        image_sha256="099101a6ca62321b76de2b7c61452342be794956a622aba542120125d91ffb97",
        evidence=(
            "Ultimate Bluetooth receiver 1.03 to official 1.04 only. "
            "Boot entry, firmware target, initialization geometry, one existing application-page "
            "checksum, and return to stable 1.03 verified on 2026-11-21. "
            "The packaged boot prefix was source-reviewed; installed bootloader bytes were not read back. "
            "Hardware revision is unreported. First installation and interrupted-update reflash "
            "remain experimental; firmware writing and power-loss recovery are not hardware-validated. "
            "Recovery in boot mode requires separately confirming a complete erase and reinstall "
            "of the same cached image. See docs/legacy-receiver-protocol.md."
        ),
        source_versions=(103,),
        initialization=ReceiverInitialization(0x4000, 0xFFFFFFFF, 0, 0x1000),
        boot_prefix_sha256="4f9f771cd34c7147a9123c35f2aac3d397396071c58c9f6571b9efccfc64dc81",
        boot_prefix_crc16=0x1ECD,
    ),
)


def has_native_installation(product_id: int) -> bool:
    """Advisory model capability; exact image/device authorization is still required."""
    records = (*QUALIFICATIONS, *SOURCE_REVIEWED_AUTHORIZATIONS, *LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS)
    return any(entry.product_id == product_id for entry in records)


def has_wireless_installation(product_id: int) -> bool:
    return any(entry.controller.product_id == product_id for entry in WIRELESS_AUTHORIZATIONS)


def wireless_qualification_for(image: FirmwareImage, target: WirelessTarget) -> WirelessAuthorization | None:
    return next((entry for entry in WIRELESS_AUTHORIZATIONS if (
        entry.controller.product_id == target.controller.product_id == image.product_id
        and target.controller.target_verified and target.receiver.target_verified
        and entry.controller.hardware_revision == target.controller.hardware_revision
        and entry.controller.image_revision == image.revision
        and entry.controller.image_sha256 == image.sha256
        and bool(entry.controller.evidence.strip())
        and target.controller.version_raw in entry.controller.source_versions
        and target.controller.beta == 0
        and target.receiver.product_id == 0x6013
        and target.receiver.version_raw == entry.receiver_version_raw and target.receiver.beta == 0
        and target.status.edition == entry.controller_edition
    )), None)


def qualification_for(image: FirmwareImage, identity: DeviceIdentity) -> FlashAuthorization | None:
    if not identity.target_verified:
        return None
    records = (*QUALIFICATIONS, *SOURCE_REVIEWED_AUTHORIZATIONS, *LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS)
    return next((entry for entry in records if (
        entry.product_id == identity.product_id == image.product_id
        and entry.hardware_revision == identity.hardware_revision
        and entry.image_revision == image.revision
        and entry.image_sha256 == image.sha256
        and bool(entry.evidence.strip())
        and identity.version_raw in entry.source_versions
        and identity.beta == 0
    )), None)
