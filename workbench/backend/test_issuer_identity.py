import unittest
import app  # Establish the same financial-engine import path as the API.
from unittest.mock import patch
from financial_engine.security_identity import identify_security
from financial_engine.pdf_adapter import _pdf_metadata
from financial_engine.disclosure import DisclosureError, resolve_security


class IssuerIdentityTests(unittest.TestCase):
    def test_three_gorges_glossary_references_are_not_report_issuers(self):
        identity=identify_security([
            (1,'公司代码：600905 公司简称：三峡能源'),
            (4,'第一节 释义....................................5\n第二节 公司简介和主要财务指标........6'),
            (5,'第一节 释义\n常用词语释义\n司，股票代码：000883\n金风科技 指 金风科技股份有限公司，股票代码：002202'),
            (6,'股票代码：000883\n第二节 公司简介和主要财务指标\n一、公司信息'),
            (7,'五、公司股票简况\n股票种类 股票上市交易所 股票简称 股票代码 变更前股票简称\nA股 上海证券交易所 三峡能源 600905')])
        self.assertEqual(identity['stock_code'],'600905')
        self.assertEqual(identity['status'],'pdf_code_reported')
        self.assertEqual({s['stock_code'] for s in identity['ignored_sources']},{'000883','002202'})
        self.assertEqual({s['page'] for s in identity['sources']},{1,7})

    def test_conflicting_cover_and_company_profile_remain_blocked(self):
        identity=identify_security([(1,'公司代码：600905 公司简称：三峡能源'),
            (7,'第二节 公司基本情况\n证券代码：002202 证券简称：另一公司')])
        self.assertEqual(identity['status'],'ambiguous')
        self.assertIsNone(identity['stock_code'])

    def test_stock_summary_table_can_identify_issuer_without_cover_code(self):
        identity=identify_security([(7,'第二节 公司简介和主要财务指标\n股票种类 股票上市交易所 股票简称 股票代码\nA股 上海证券交易所 三峡能源 600905\nH股 香港联交所 其他简称 09888')])
        self.assertEqual(identity['stock_code'],'600905')

    def test_other_company_reference_without_issuer_code_is_not_adopted(self):
        identity=identify_security([(4,'第二节 经营情况\n参股公司股票代码：002202\n金风科技指金风科技股份有限公司，股票代码002202')])
        self.assertEqual(identity['status'],'absent')
        self.assertFalse(identity['sources'])
        cover=identify_security([(1,'金风科技股份有限公司，股票代码：002202')])
        self.assertEqual(cover['status'],'absent')

    def test_glossary_only_and_continuation_page_never_confirm_subject(self):
        identity=identify_security([(4,'第一节 释义\n公司甲 指 公司，证券代码：000883'),(5,'股票代码：002202')])
        self.assertEqual(identity['status'],'absent')

    def test_h_share_and_six_digit_postcode_are_not_a_share_identity(self):
        identity=identify_security([(6,'第二节 公司信息\n证券代码：09888\n邮政编码：100000')])
        self.assertEqual(identity['status'],'absent')

    def test_spaced_cover_and_separate_label_value(self):
        self.assertEqual(identify_security([(1,'公 司 代 码 ： 600905 公司简称：三峡能源')])['stock_code'],'600905')
        self.assertEqual(identify_security([(1,'A股代码：600905')])['stock_code'],'600905')
        self.assertEqual(identify_security([(8,'第二节 公司基本信息\n证券代码\n002074')])['stock_code'],'002074')

    def test_a_share_identity_inside_same_line_basic_fields(self):
        identity=identify_security([(10,'第二节 公司简介\nA 股股票简称 宁德时代 A 股股票代码 300750')])
        self.assertEqual(identity['stock_code'],'300750')


class CoverCompanyNameTests(unittest.TestCase):
    def test_full_legal_name_preserves_chinese_and_ascii_parentheses(self):
        for name in ['中国三峡新能源（集团）股份有限公司','中国三峡新能源(集团)股份有限公司','宁德时代新能源科技股份有限公司']:
            self.assertEqual(_pdf_metadata(name+'2025年年度报告'),(name,2025))

    def test_separate_or_wrapped_cover_lines_do_not_include_header(self):
        cover='公司代码：600905 公司简称：三峡能源\n中国三峡新能源\n（集团）股份有限公司\n2025 年年度报告'
        self.assertEqual(_pdf_metadata(cover),('中国三峡新能源（集团）股份有限公司',2025))
        cover='CTG\n中国三峡新能源（集团）股份有限公司\n2025年年度报告'
        self.assertEqual(_pdf_metadata(cover),('中国三峡新能源（集团）股份有限公司',2025))

    def test_generic_suffix_or_unbalanced_name_is_not_an_issuer(self):
        for name in ['股份有限公司','有限公司','集团有限公司','中国三峡新能源（集团股份有限公司']:
            self.assertEqual(_pdf_metadata(name+'2025年年度报告'),(None,2025))

    def test_empty_or_ambiguous_official_lookup_explains_problem_in_chinese(self):
        with patch('financial_engine.disclosure._post_form',return_value=[]):
            with self.assertRaisesRegex(DisclosureError,'未在证券目录查到'):
                resolve_security('股份有限公司')
        candidates=[{'category':'A股','code':code,'orgId':code} for code in ['300750','002074']]
        with patch('financial_engine.disclosure._post_form',return_value=candidates):
            with self.assertRaisesRegex(DisclosureError,'候选代码'):
                resolve_security('模糊公司名称')


if __name__=='__main__':unittest.main()
