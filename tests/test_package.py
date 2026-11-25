import unittest

import signalbox


class Package(unittest.TestCase):
    def test_states_its_version(self):
        self.assertRegex(signalbox.__version__, r"^\d+\.\d+\.\d+")
        self.assertEqual(signalbox.__version__, "9.9.9", "a failure to rehearse the lane")


if __name__ == "__main__":
    unittest.main()
