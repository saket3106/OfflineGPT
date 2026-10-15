import os
import sys
import time
import unittest
from pathlib import Path
from PIL import Image
import numpy as np

# Ensure source is on path
SOURCE_DIR = Path(__file__).resolve().parent.parent
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from image_pipeline import select_image_operation, ImageOperation, recolor_intent
from workspace_services import basic_image_edit
from app import find_available_conversation_image


class TestFastRecolorRouting(unittest.TestCase):

    def test_mustang_user_prompt_routing(self):
        """Verify the user's exact Mustang prompt routes to FAST_RECOLOR with zero model calls."""
        prompt = (
            "Change only the black exterior body paint of this Mustang to glossy metallic red. "
            "Do not regenerate the scene. Preserve headlights, windows, grille, tires, wheels, "
            "wet road, buildings, lighting and camera perspective. Use Fast Recolor when reliable. "
            "If automatic masking is inaccurate, request mask correction rather than silently using a full generative edit."
        )
        t0 = time.perf_counter()
        op, opts = select_image_operation(prompt, has_image=True)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        self.assertEqual(op, ImageOperation.FAST_RECOLOR)
        self.assertEqual(opts.get("color"), "#ca151c")
        self.assertEqual(opts.get("finish"), "metallic")
        self.assertEqual(opts.get("from_color"), "black")
        self.assertLess(elapsed_ms, 50.0, f"Route classification took too long: {elapsed_ms} ms")

    def test_all_six_structured_operations(self):
        """Verify selector accurately classifies all 6 operations."""
        cases = [
            ("Generate a stunning photograph of a red Ferrari in Paris", False, ImageOperation.TEXT_TO_IMAGE),
            ("Add a carbon fiber spoiler to the rear using generative edit", True, ImageOperation.GENERATIVE_IMAGE_EDIT),
            ("Change the black paint of this car to blue", True, ImageOperation.FAST_RECOLOR),
            ("Upscale this image to 4k with super-resolution", True, ImageOperation.UPSCALE),
            ("Resize this photo to 1024x768", True, ImageOperation.RESIZE),
            ("Crop the borders of this image", True, ImageOperation.CROP),
        ]
        for prompt, has_image, expected in cases:
            op, _ = select_image_operation(prompt, has_image=has_image)
            self.assertEqual(op, expected, f"Prompt '{prompt}' classified as {op}, expected {expected}")




if __name__ == "__main__":
    unittest.main()
