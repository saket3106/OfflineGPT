"""Problem-data quantities must not become response-format requirements."""
import unittest
from intelligence import analyze_request,validate_response
from router import extract_constraints


class QuantityScopeTests(unittest.TestCase):
    def test_object_counts_are_data_not_bullet_instructions(self):
        for prompt in [
            'A shopkeeper purchases 15 items for 450 each, sells 10 at 20% profit and 5 at 10% loss. Find overall profit.',
            'A crate holds 8 items costing 17 each. What is the total cost?',
            'The buyer purchased exactly 12 items. Explain how the discount is calculated.',
            'There are 11 items in the queue. What does FIFO mean?']:
            with self.subTest(prompt=prompt):
                self.assertIsNone(extract_constraints(prompt)['bullet_count'])

    def test_explicit_list_and_bullet_constraints_are_preserved(self):
        for prompt,expected in [('Use exactly 4 bullet points.',4),('Answer in 3 bullets.',3),
                                ('List exactly 5 benefits.',5),('Give me 6 items.',6),
                                ('Return 2 items.',2),('Provide 7 examples.',7)]:
            with self.subTest(prompt=prompt):
                self.assertEqual(extract_constraints(prompt)['bullet_count'],expected)

    def test_a_correct_word_problem_answer_has_no_invented_format_failure(self):
        prompt='A crate holds 8 items costing 17 each. What is the total cost?'
        analysis=analyze_request(prompt)
        self.assertNotEqual(analysis['category'],'exact_format')
        self.assertFalse(any('bullet' in error.lower() for error in validate_response('The total cost is 136.',analysis)))

    def test_real_format_requirement_is_not_removed_by_problem_quantities(self):
        prompt='A crate holds 8 items costing 17 each. Explain the calculation in exactly 2 bullet points.'
        analysis=analyze_request(prompt)
        self.assertEqual(analysis['constraints']['bullet_count'],2)
        self.assertTrue(any('bullet' in error.lower() for error in validate_response('The total cost is 136.',analysis)))


if __name__=='__main__':unittest.main()
