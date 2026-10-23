import unittest
from math_engine import extract_and_evaluate_math

class CompleteArithmeticTests(unittest.TestCase):
 def test_complete_grouped_unicode_expression(self):
  for text in ['Calculate (875 × 46) + (1440 ÷ 12) − 275.','What is (12 * 9) + (45 / 5) - 8?','Calculate 3 * (7 + 5).']:
   expected=40095 if '875' in text else 109 if '12 * 9' in text else 36
   result=extract_and_evaluate_math(text);self.assertIsNotNone(result);self.assertEqual(result['result'],expected)
 def test_invalid_group_is_not_silently_reduced_to_an_inner_expression(self):
  self.assertIsNone(extract_and_evaluate_math('Calculate (875 * 46) + (1440 / 12 - 275'))
