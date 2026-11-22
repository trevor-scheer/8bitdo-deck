from dataclasses import replace
import unittest

from eightbitdo.protocol import DeviceIdentity, FirmwareImage, PairedControllerInfo
from eightbitdo.qualifications import WIRELESS_AUTHORIZATIONS, qualification_for, wireless_qualification_for
from eightbitdo.wireless import WirelessTarget


class WirelessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.authorization = WIRELESS_AUTHORIZATIONS[0]
        self.image = FirmwareImage(110, 0x5000, 0x6012, 0, 0, 0, b'',
                                   self.authorization.controller.image_sha256)
        self.target = WirelessTarget(DeviceIdentity(0x6013, 106), DeviceIdentity(0x6012, 109),
                                     PairedControllerInfo(109, 0, 100, 1))

    def test_reviewed_pair_has_only_wireless_authorization(self):
        self.assertIs(wireless_qualification_for(self.image, self.target), self.authorization)
        self.assertIsNone(qualification_for(self.image, self.target.controller))

    def test_sibling_receiver_versions_beta_editions_and_changed_image_are_rejected(self):
        targets = (
            replace(self.target, receiver=DeviceIdentity(0x6013, 101)),
            replace(self.target, receiver=DeviceIdentity(0x6008, 106)),
            replace(self.target, receiver=DeviceIdentity(0x6013, 106, beta=1)),
            replace(self.target, controller=DeviceIdentity(0x6012, 110)),
            replace(self.target, controller=DeviceIdentity(0x6012, 109, beta=1)),
            replace(self.target, controller=DeviceIdentity(0x6012, 109, target_verified=False)),
            replace(self.target, status=replace(self.target.status, edition=2)),
        )
        for target in targets:
            with self.subTest(target=target):
                self.assertIsNone(wireless_qualification_for(self.image, target))
        self.assertIsNone(wireless_qualification_for(replace(self.image, sha256='0' * 64), self.target))


if __name__ == '__main__':
    unittest.main()
