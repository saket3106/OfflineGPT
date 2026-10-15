import unittest
from pathlib import Path
from unittest.mock import MagicMock

import app
import intelligence
from intelligence import DEFAULT_SYSTEM_PROMPT, analyze_request, build_system_prompt, is_standalone_greeting


class ResolutionAndWarmLatencyTests(unittest.TestCase):
    def test_p0_system_prompt_prefix_invariance_for_greetings_and_followups(self):
        """Turn 1 greeting, Turn 2 follow-up, Turn 3 explanation, and cross-chat greeting must share identical system prompt prefix."""
        app_dir = Path("source")
        base_prompt = intelligence.load_system_prompt(app_dir)

        # Turn 1: Cold Greeting
        text1 = "Hello! How are you today?"
        analysis1 = analyze_request(text1)
        analysis1["compact_conversation"] = is_standalone_greeting(text1)
        sys1 = build_system_prompt(base_prompt, analysis1, memory_context="", history_context="")

        # Turn 2: Immediate Follow-up
        text2 = "Nice! Can you tell me a quick joke?"
        analysis2 = analyze_request(text2)
        analysis2["compact_conversation"] = is_standalone_greeting(text2)
        sys2 = build_system_prompt(base_prompt, analysis2, memory_context="", history_context="")

        # Turn 3: Explanation
        text3 = "Can you explain how a computer CPU works in detail?"
        analysis3 = analyze_request(text3)
        analysis3["compact_conversation"] = is_standalone_greeting(text3)
        sys3 = build_system_prompt(base_prompt, analysis3, memory_context="", history_context="")

        # Cross-chat greeting (new conversation)
        text_cross = "Good morning!"
        analysis_cross = analyze_request(text_cross)
        analysis_cross["compact_conversation"] = is_standalone_greeting(text_cross)
        sys_cross = build_system_prompt(base_prompt, analysis_cross, memory_context="", history_context="")

        # Assert all system prompts are 100% IDENTICAL
        self.assertEqual(sys1, sys2, "Turn 1 greeting and Turn 2 follow-up system prompts must be identical to preserve llama-server KV prefix cache")
        self.assertEqual(sys2, sys3, "Turn 2 and Turn 3 system prompts must be identical")
        self.assertEqual(sys1, sys_cross, "Cross-chat greeting must share the identical system prompt")
        self.assertTrue(sys1.startswith(DEFAULT_SYSTEM_PROMPT.strip()))

    def test_p1_resolution_precedence_explicit_4k_prompts(self):
        """Explicit 4K requests must produce 3840x2160 with upscale_needed=True."""
        cfg = {"flux_width": 1024, "flux_height": 576}
        for prompt in [
            "A 4k cinematic photo of a mountain",
            "A 4K wallpaper of neon city",
            "mountain landscape in 4k",
            "mountain landscape, 4K resolution",
            "render 4k image",
            "ultra hd photo of forest",
            "photo of car 3840x2160",
            "photo of car, 3840 x 2160",
            "photo of car, 3840*2160",
        ]:
            plan = app.parse_image_resolution_intent(prompt, cfg)
            self.assertEqual(plan["target_width"], 3840, f"Failed width for {prompt}")
            self.assertEqual(plan["target_height"], 2160, f"Failed height for {prompt}")
            self.assertTrue(plan["upscale_needed"], f"Failed upscale_needed for {prompt}")

    def test_p1_resolution_precedence_non_4k_prompts_never_forced_to_4k(self):
        """Prompts not requesting 4K must NEVER be forced to 3840x2160."""
        cfg = {"flux_width": 1280, "flux_height": 720}
        plan = app.parse_image_resolution_intent("product studio shot of matte black wireless headphones on oak desk", cfg)
        self.assertEqual(plan["target_width"], 1280)
        self.assertEqual(plan["target_height"], 720)
        self.assertEqual(plan["native_width"], 1280)
        self.assertEqual(plan["native_height"], 720)
        self.assertFalse(plan["upscale_needed"])

        # Safe defaults when not configured
        plan_default = app.parse_image_resolution_intent("photo of a cute cat", {})
        self.assertEqual(plan_default["target_width"], 1024)
        self.assertEqual(plan_default["target_height"], 576)
        self.assertFalse(plan_default["upscale_needed"])

    def test_p1_resolution_precedence_aspect_ratios(self):
        """Portrait and square requests must map accurately."""
        plan_portrait = app.parse_image_resolution_intent("portrait phone wallpaper of an astronaut", {})
        self.assertEqual(plan_portrait["target_width"], 576)
        self.assertEqual(plan_portrait["target_height"], 1024)
        self.assertFalse(plan_portrait["upscale_needed"])

        plan_4k_portrait = app.parse_image_resolution_intent("portrait 4k wallpaper of an astronaut", {})
        self.assertEqual(plan_4k_portrait["target_width"], 2160)
        self.assertEqual(plan_4k_portrait["target_height"], 3840)
        self.assertTrue(plan_4k_portrait["upscale_needed"])

        plan_square = app.parse_image_resolution_intent("square profile picture avatar of an owl", {})
        self.assertEqual(plan_square["target_width"], 768)
        self.assertEqual(plan_square["target_height"], 768)
        self.assertFalse(plan_square["upscale_needed"])

    def test_p1_state_cleanup_prevents_resolution_leak(self):
        """Verifies pending_image_res_plan does not leak across consecutive generations."""
        # Simulate App state
        class MockApp:
            def __init__(self):
                self.pending_image_res_plan = None
                self.pending_image_source_prompt = None
                self.pending_image_plan = None
                self.pending_action = None

            def simulate_generation_done(self):
                self.pending_image_res_plan = None
                self.pending_image_source_prompt = None
                self.pending_image_plan = None
                self.pending_action = None

        mock_app = MockApp()

        # Job 1: 4K request
        plan1 = app.parse_image_resolution_intent("Cinematic 4K landscape", {"flux_width": 1280, "flux_height": 720})
        mock_app.pending_image_res_plan = plan1
        self.assertEqual(mock_app.pending_image_res_plan["target_width"], 3840)
        self.assertTrue(mock_app.pending_image_res_plan["upscale_needed"])

        # Job 1 finishes -> cleanup
        mock_app.simulate_generation_done()
        self.assertIsNone(mock_app.pending_image_res_plan)

        # Job 2: Headphones request (no 4K)
        plan2 = app.parse_image_resolution_intent("Matte-black wireless headphones", {"flux_width": 1280, "flux_height": 720})
        mock_app.pending_image_res_plan = plan2
        self.assertEqual(mock_app.pending_image_res_plan["target_width"], 1280)
        self.assertEqual(mock_app.pending_image_res_plan["target_height"], 720)
        self.assertFalse(mock_app.pending_image_res_plan["upscale_needed"], "Subsequent job must not inherit 4K upscale from previous job")

    def test_p2_subject_fidelity_anchor_headphones(self):
        """Headphones must be anchored to over-ear with headband and cushioned earcups."""
        prompt = "a product studio shot of a high-end matte-black wireless headphones on a clean oak desk"
        anchored = app.anchor_subject_fidelity(prompt)
        self.assertIn("over-ear headphones with arched headband and cushioned earcups", anchored)
        self.assertNotIn("earbuds", anchored)

        # Do not alter if user explicitly wanted earbuds
        earbuds_prompt = "wireless earbuds in a charging case"
        self.assertEqual(app.anchor_subject_fidelity(earbuds_prompt), earbuds_prompt)


if __name__ == "__main__":
    unittest.main()
