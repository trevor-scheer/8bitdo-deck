from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    role: str
    catalog_type: int
    firmware_pid: int
    instructions: str
    support: str = "Firmware browsing; native installation awaits hardware validation."

    def public(self) -> dict:
        return asdict(self)


MODELS = (
    Model("ultimate2-controller", "Ultimate 2 Wireless controller", "controller", 94, 0x6012,
          "Connect the controller directly with a USB data cable. The receiver has separate firmware."),
    Model("ultimate2-receiver", "Ultimate 2 Wireless receiver", "receiver", 95, 0x6013,
          "Connect the USB receiver to the Deck, directly or through its dock. Turn the paired controller off to inspect the receiver."),
    Model("ultimate-bluetooth-controller", "Ultimate Bluetooth controller (80NA)", "controller", 41, 0x6007,
          "Connect the controller with a USB data cable so the app can identify its firmware target. The receiver has separate firmware."),
    Model("ultimate-bluetooth-receiver", "Ultimate Bluetooth receiver (80NA)", "receiver", 42, 0x6008,
          "Connect the receiver directly or through its dock. The app reads its model tag to distinguish it from the 2.4G receiver."),
    Model("ultimate-24g-controller", "Ultimate 2.4G controller (81HA)", "controller", 43, 0x3012,
          "Check the 81HA model label and connect the controller directly by USB. Do not select the Bluetooth model's firmware."),
    Model("ultimate-24g-receiver", "Ultimate 2.4G receiver (81HA)", "receiver", 44, 0x3013,
          "Connect the receiver directly or through its dock. Receiver firmware is separate from controller firmware."),
)

BY_ID = {model.id: model for model in MODELS}
BY_FIRMWARE_PID = {model.firmware_pid: model for model in MODELS}
# Shared USB personalities require a model-tag reply before naming a candidate.
BY_USB_PID = {pid: model for pid, model in BY_FIRMWARE_PID.items() if pid != 0x6007}
QUERY_PIDS = frozenset({0x3109, 0x310B, 0x6007, 0x6012, 0x6013})


def model_for_identity(pid: int) -> Model | None:
    return BY_FIRMWARE_PID.get(pid)


def get_model(model_id: str) -> Model:
    try:
        return BY_ID[model_id]
    except (KeyError, TypeError):
        raise ValueError("Select a known model from the device list.") from None
