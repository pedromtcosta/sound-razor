import unittest

from master_track.pipeline import DEFAULT_MODEL, MODEL_STEMS, VOCAL_MODEL, select_model


class StemSelectionTests(unittest.TestCase):
    def test_default_keeps_all_six_sources(self):
        self.assertEqual(select_model(None, None), (DEFAULT_MODEL, MODEL_STEMS[DEFAULT_MODEL]))

    def test_model_selection_and_output_order(self):
        model, stems = select_model(None, ["guitar", "bass", "vocals", "drums", "guitar"])
        self.assertEqual(model, DEFAULT_MODEL)
        self.assertEqual(stems, ("guitar", "bass", "vocals", "drums"))
        self.assertEqual(select_model(None, ["vocals"]), (VOCAL_MODEL, ("vocals",)))
        self.assertEqual(select_model(None, ["drums"])[0], "htdemucs.yaml")

    def test_incompatible_requests_fail_before_inference(self):
        for model, stems in [(VOCAL_MODEL, ["guitar"]), (None, ["instrumental", "guitar"]),
                             (None, ["lead-guitar"]), (None, []), ("custom.onnx", ["vocals"])]:
            with self.subTest(model=model, stems=stems), self.assertRaises(ValueError):
                select_model(model, stems)

    def test_custom_model_without_filter_is_preserved(self):
        self.assertEqual(select_model("custom.onnx", None), ("custom.onnx", None))

    def test_guitar_roles_require_guitar_capable_first_stage(self):
        for roles in (["lead"], ["rhythm"], ["lead", "rhythm", "bass", "vocals", "drums"]):
            self.assertEqual(select_model(None, roles), (DEFAULT_MODEL, tuple(roles)))
            with self.assertRaises(ValueError):
                select_model("htdemucs.yaml", roles)


if __name__ == "__main__":
    unittest.main()
