"""Developer-reviewed write authorizations, independent of user-selected models.

Add records only with the hardware evidence described in docs/protocol.md.
Neither downloaded metadata nor a UI request may populate this collection.
"""

from .protocol import DeviceIdentity, FirmwareImage, FlashAuthorization, FlashQualification, SourceRegion, SourceReviewedAuthorization
from .legacy_protocol import LegacySourceReviewedAuthorization


QUALIFICATIONS: tuple[FlashQualification, ...] = ()
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
LEGACY_SOURCE_REVIEWED_AUTHORIZATIONS: tuple[LegacySourceReviewedAuthorization, ...] = ()


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
