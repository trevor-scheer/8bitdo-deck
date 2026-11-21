"""Reviewed native write qualifications, independent of user-selected models.

Add records only with the hardware evidence described in docs/protocol.md.
Neither downloaded metadata nor a UI request may populate this collection.
"""

from .protocol import DeviceIdentity, FirmwareImage, FlashQualification


QUALIFICATIONS: tuple[FlashQualification, ...] = ()


def qualification_for(image: FirmwareImage, identity: DeviceIdentity) -> FlashQualification | None:
    if not identity.target_verified:
        return None
    return next((entry for entry in QUALIFICATIONS if (
        entry.product_id == identity.product_id == image.product_id
        and entry.hardware_revision == identity.hardware_revision
        and entry.image_revision == image.revision
        and entry.image_sha256 == image.sha256
        and bool(entry.evidence.strip())
        and identity.version_raw in entry.source_versions
        and identity.beta == 0
    )), None)
