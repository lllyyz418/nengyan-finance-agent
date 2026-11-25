"""Cross-report risks must stop when source basis is inconsistent."""
import unittest

from financial_engine import evaluate_context_risks
from financial_engine.context_risk import AGING_CODES


def context_fixture():
    old_id, new_id = 'old2024', 'new2025'
    old = {'document_id': old_id, 'report_year': 2024, 'report_type': 'annual', 'security_code': '300750'}
    new = {'document_id': new_id, 'report_year': 2025, 'report_type': 'annual', 'security_code': '300750'}
    history_analysis = {'documents': [old, new], 'risk_assessments': [
        {'document_id': old_id, 'period': '2024', 'rule_id': 'receivables_aging_pressure',
         'status': 'triggered', 'assessment_id': 'old-aging', 'evidence_fact_ids': ['old-ar'],
         'conditions': [
             {'condition_code': 'gross_receivables_growth_above_revenue_growth', 'passed': True,
              'observed': {'growth_gap_percentage_points': '1.25'}},
             {'condition_code': 'over_1y_share_increased', 'passed': True,
              'observed': {'share_change_percentage_points': '0.5'}},
         ]},
        {'document_id': new_id, 'period': '2025', 'rule_id': 'receivables_aging_pressure',
         'status': 'triggered', 'assessment_id': 'new-aging', 'evidence_fact_ids': ['new-ar'],
         'conditions': [
             {'condition_code': 'gross_receivables_growth_above_revenue_growth', 'passed': True,
              'observed': {'growth_gap_percentage_points': '2.5'}},
             {'condition_code': 'over_1y_share_increased', 'passed': True,
              'observed': {'share_change_percentage_points': '0.75'}},
         ]},
    ]}
    checks = [{'year': 2024, 'metric_code': code, 'status': 'matched',
               'original_fact_id': 'old-'+code, 'later_comparative_fact_id': 'new-'+code}
              for code in AGING_CODES]
    gross = {'metric_code': 'gross_margin', 'from_year': 2024, 'to_year': 2025,
             'from_metric_id': 'old-margin', 'to_metric_id': 'new-margin',
             'status': 'calculated', 'basis_check': 'matched_comparatives',
             'absolute_change': '-2'}
    peer_gross = {'metric_code': 'gross_margin', 'report_year': 2025, 'unit': 'percent',
                  'valid_peer_count': 2, 'target_minus_peer_median': '-3',
                  'target': {'status': 'available', 'document_id': new_id,
                             'metric_id': 'new-margin', 'input_fact_ids': ['new-rev', 'new-cost']},
                  'peer_median': {'status': 'calculated', 'input_metric_ids': ['peer-a-margin', 'peer-b-margin']},
                  'peers': [{'status': 'available', 'document_id': 'peer-a',
                             'input_fact_ids': ['a-rev', 'a-cost']},
                            {'status': 'available', 'document_id': 'peer-b',
                             'input_fact_ids': ['b-rev', 'b-cost']}]}
    return {'identity': {'stock_code': '300750'}, 'current': {'documents': [new]},
            'history': {'status': 'complete', 'analysis': history_analysis,
                        'comparison': {'restatement_checks': checks, 'changes': [gross]}},
            'peers': {'status': 'complete', 'comparison': {'metric_comparisons': [peer_gross]}}}


class ContextRiskTests(unittest.TestCase):
    def test_two_independent_triggered_years_and_valid_peer_margin(self):
        result = evaluate_context_risks(context_fixture())
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([item['status'] for item in result['assessments']], ['triggered', 'triggered'])
        self.assertEqual(len(result['findings']), 2)
        self.assertEqual(result['data_quality']['status'], 'no_difference_detected')
        self.assertIn('old-ar', result['assessments'][0]['evidence_fact_ids'])
        self.assertEqual(result['assessments'][0]['conditions'][0]['observed']['growth_gap_percentage_points'], '1.25')
        self.assertEqual(result['assessments'][0]['conditions'][1]['observed']['over_1y_share_change_percentage_points'], '0.75')
        self.assertIn('peer-a-margin', result['assessments'][1]['evidence_metric_ids'])
        self.assertEqual(result['assessments'][1]['conditions'][0]['observed']['change_percentage_points'], '-2')

    def test_comparative_difference_blocks_persistent_signal(self):
        sample = context_fixture()
        check = sample['history']['comparison']['restatement_checks'][0]
        check['status'] = 'difference'
        result = evaluate_context_risks(sample)
        self.assertEqual(result['data_quality']['status'], 'review_required')
        self.assertEqual(result['assessments'][0]['status'], 'insufficient_data')
        self.assertIn('COMPARATIVE_DIFFERENCE:accounts_receivable_gross',
                      result['assessments'][0]['missing_or_invalid'])
        self.assertNotIn(result['assessments'][0]['assessment_id'],
                         [item['finding_id'] for item in result['findings']])

    def test_annual_status_without_matching_conditions_is_withheld(self):
        sample = context_fixture()
        sample['history']['analysis']['risk_assessments'][0]['conditions'][0]['passed'] = False
        result = evaluate_context_risks(sample)
        self.assertEqual(result['assessments'][0]['status'], 'insufficient_data')
        self.assertIn('ANNUAL_RULE_CONDITIONS_INCONSISTENT_2024',
                      result['assessments'][0]['missing_or_invalid'])

    def test_float_or_missing_annual_gap_cannot_support_persistent_signal(self):
        for invalid in (1.25, None):
            with self.subTest(invalid=invalid):
                sample = context_fixture()
                sample['history']['analysis']['risk_assessments'][0]['conditions'][0]['observed']['growth_gap_percentage_points'] = invalid
                result = evaluate_context_risks(sample)
                self.assertEqual(result['assessments'][0]['status'], 'insufficient_data')

    def test_unverified_gross_margin_history_is_withheld(self):
        sample = context_fixture()
        sample['history']['comparison']['changes'][0]['basis_check'] = 'unchecked_comparatives'
        result = evaluate_context_risks(sample)
        self.assertEqual(result['assessments'][1]['status'], 'insufficient_data')
        self.assertIn('GROSS_MARGIN_COMPARATIVE_UNVERIFIED', result['assessments'][1]['missing_or_invalid'])

    def test_two_peers_required_and_float_change_rejected(self):
        sample = context_fixture()
        sample['peers']['comparison']['metric_comparisons'][0]['valid_peer_count'] = 1
        self.assertEqual(evaluate_context_risks(sample)['assessments'][1]['status'], 'insufficient_data')
        sample = context_fixture()
        sample['history']['comparison']['changes'][0]['absolute_change'] = -2.0
        result = evaluate_context_risks(sample)
        self.assertIn('INVALID_GROSS_MARGIN_CHANGE', result['assessments'][1]['missing_or_invalid'])

    def test_not_requested_and_h1_are_explicit(self):
        sample = context_fixture()
        sample['history'] = {'status': 'not_requested', 'analysis': None, 'comparison': None}
        sample['peers'] = {'status': 'not_requested', 'comparison': None}
        result = evaluate_context_risks(sample)
        self.assertEqual([a['status'] for a in result['assessments']], ['not_evaluated', 'not_evaluated'])
        self.assertEqual(result['data_quality']['status'], 'not_requested')
        sample['current']['documents'][0]['report_type'] = 'half_year'
        self.assertEqual(evaluate_context_risks(sample)['status'], 'not_evaluated')

    def test_no_comparative_checks_cannot_claim_consistency(self):
        sample = context_fixture()
        sample["history"]["comparison"]["restatement_checks"] = []
        result = evaluate_context_risks(sample)
        self.assertEqual(result["data_quality"]["status"], "insufficient_data")
        self.assertEqual(result["assessments"][0]["status"], "insufficient_data")

    def test_missing_prior_report_does_not_infer_absence_of_risk(self):
        sample = context_fixture()
        sample['history']['analysis']['documents'] = [sample['current']['documents'][0]]
        result = evaluate_context_risks(sample)
        self.assertEqual(result['assessments'][0]['status'], 'insufficient_data')
        self.assertEqual(result['data_quality']['status'], 'insufficient_data')


if __name__ == '__main__':
    unittest.main()
