import unittest

from master_track.cli import parser
from master_track.guitars import GuitarOptions, chunk_ranges, local_anchors, overlap_weights


class GuitarStageTests(unittest.TestCase):
    def test_chunk_assembly_preserves_signal_without_gaps(self):
        import numpy as np

        for length in (1, 19, 20, 21, 38, 39, 100):
            source = np.linspace(-0.8, 0.8, length, dtype=np.float32)
            summed, weights = np.zeros(length), np.zeros(length)
            for start, end in chunk_ranges(length, 20, 2):
                fade = overlap_weights(end - start, 2, start == 0, end == length)
                summed[start:end] += source[start:end] * fade
                weights[start:end] += fade
            self.assertTrue(np.all(weights > 0))
            np.testing.assert_allclose(summed / weights, source, atol=1e-7)

    def test_span_is_clipped_and_shifted_to_chunk(self):
        self.assertEqual(local_anchors((18, 23), 19, 39), [[["+", 0, 4]]])
        self.assertEqual(local_anchors((18, 23), 0, 20), [[["+", 18, 20]]])
        self.assertIsNone(local_anchors((18, 23), 23, 43))
        self.assertIsNone(local_anchors(None, 0, 20))

    def test_invalid_options_and_out_of_bounds_span(self):
        for options in (GuitarOptions(prompt=" "), GuitarOptions(chunk_seconds=1),
                        GuitarOptions(chunk_seconds=float("nan")), GuitarOptions(span=(2, 1)),
                        GuitarOptions(span=(0, float("inf"))), GuitarOptions(device="invalid")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                options.validate()
        with self.assertRaises(ValueError):
            GuitarOptions(span=(0, 11)).validate(duration=10)

    def test_cli_accepts_roles_and_guidance(self):
        args = parser().parse_args(["separate", "--file", "song.wav", "--stems", "lead", "rhythm",
                                   "--lead-span", "12", "18", "--sam-device", "cpu"])
        self.assertEqual(args.stems, ["lead", "rhythm"])
        self.assertEqual(args.lead_span, [12, 18])
        self.assertEqual(args.sam_device, "cpu")


if __name__ == "__main__":
    unittest.main()
