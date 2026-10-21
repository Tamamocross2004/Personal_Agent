"""词法保护的边界与反例；不把存在性测试当成语义正确性测试。"""
from decimal import Decimal

import pytest

from agent.claim_numbers import check_direct, numbers


def check(quote,text,*,page=43,name='manual.txt',excerpt=None,extra=None):
    records={'bound':{'kind':'document','text':text,'page':page,'document_name':name}}
    records.update(extra or {})
    return check_direct(quote,[{'source_id':'bound','excerpt':text if excerpt is None else excerpt}],records)


@pytest.mark.parametrize('identity',['RZ64','Cache19','v3.11.2','protocol_42','Module7.ext'])
def test_identity_uses_whole_bound_record_or_document_name(identity):
    assert not check(identity+'的容量为27',identity+'说明\n容量为27',excerpt='容量为27')
    assert not check(identity+'的容量为27','容量为27',name=identity+'.txt')
    with pytest.raises(ValueError,match='标识符'):
        check(identity+'的容量为27','X'+identity+'\n容量为27')
    with pytest.raises(ValueError,match='标识符'):
        check(identity+'的容量为27','容量为27',extra={'other':{'kind':'user','text':identity}})


def test_identifier_digits_cannot_supply_factual_quantity():
    assert check('数量为20','型号M20，数量未知')=={Decimal(20)}
    assert check('RZ64的容量为28','RZ64容量为27')=={Decimal(28)}
    assert check('费用为27.41元','费用为27.40元')=={Decimal('27.41')}
    assert check('费用43元','见第43页，费用未知')=={Decimal(43)}
    assert check('费用4.5元','Section 4.5 费用说明，金额未知')=={Decimal('4.5')}


def test_page_locator_validates_metadata_and_does_not_authorize_money():
    assert not check('容量为27（PDF第43页）','容量为27')
    with pytest.raises(ValueError,match='页码'):
        check('容量为27（第44页）','容量为27，参考数44')
    with pytest.raises(ValueError,match='页码'):
        check('容量为27（第43页）','容量为27，参考数43',page=None)
    assert check('费用43元（第43页）','费用未知')=={Decimal(43)}
    with pytest.raises(ValueError,match='页码'):
        check('第43–44页','27')
    records={str(p):{'kind':'document','text':'容量为27','page':p} for p in (43,44)}
    refs=[{'source_id':p,'excerpt':'容量为27'} for p in records]
    assert not check_direct('容量为27（pages 43-44）',refs,records)


def test_section_requires_bound_read_heading_and_is_not_numeric_evidence():
    assert not check('第4.5节解释容量27','4.5 Rules\n容量27',excerpt='容量27')
    with pytest.raises(ValueError,match='章节'):
        check('第4.5节解释容量27','金额4.5，容量27',name='4.5 Rules.txt')
    assert check('数量4.5（section 4.5）','4.5 Rules\n数量未知',excerpt='数量未知')=={Decimal('4.5')}
    assert check('有4节课程','有3节课程')=={Decimal(4)}


def test_numeric_formats_units_and_citation_markers():
    assert numbers('-2、2、1e3、1,000.5、128K、20kg；型号Q20；v4.1；[1]；[source-8]',['source-8'])=={
        Decimal(-2),Decimal(2),Decimal(1000),Decimal('1000.5'),Decimal(128),Decimal(20)}
    assert numbers('[1,2,3]')=={Decimal(1),Decimal(2),Decimal(3)}
    assert numbers('30-40')=={Decimal(30),Decimal(40)}
    records={'evidence42':{'kind':'user','text':'数量27'}}
    assert not check_direct('数量27[evidence42]',[{'source_id':'evidence42','excerpt':'数量27'}],records)


@pytest.mark.parametrize('label',['7','12.8','3.4.9'])
@pytest.mark.parametrize('style',['Chapter {}: Heading','Section {}：Heading','## {} Heading','### {}. Heading','## 第{}节 标题'])
def test_heading_delimiters_and_markdown_are_general_not_question_exceptions(label,style):
    assert not check('第'+label+'节说明数量27',style.format(label)+'\n数量27',excerpt='数量27')


@pytest.mark.parametrize('text',['正文提到 Chapter 7: 但不是标题','70: Other heading','7.1 Other section'])
def test_heading_prefix_and_inline_mentions_cannot_substitute_for_exact_heading(text):
    with pytest.raises(ValueError,match='章节'):
        check('第7节说明数量27',text+'\n数量27',excerpt='数量27')


@pytest.mark.parametrize('reference',['quartz-view(8)','matrix_copy(3p)','loader.ext(1)','parse-json(5)'])
def test_man_section_locator_requires_bound_read_heading(reference):
    text=reference+'\n===========\nThe default is disabled.'
    assert not check(reference+'说明默认关闭',text,excerpt='The default is disabled.')
    assert check(reference+'说明默认关闭','Other manual\nThe default is disabled.',
                 name=reference+'.txt',excerpt='The default is disabled.')  # 文件名不能证明已读标题。


def test_parenthesized_values_and_wrong_section_remain_numeric_checks():
    assert check('费用(7)元','费用(6)元')=={Decimal(7)}
    assert check('f(7)返回结果','f(6)返回结果')=={Decimal(7)}
    assert check('quartz(7)说明','quartz(8)\n说明')=={Decimal(7)}
    assert check('容量为8','quartz(8)\n====\n容量未知')=={Decimal(8)}
    assert check('quartz(8)费用为8元','quartz(8)\n====\n费用未知',excerpt='费用未知')=={Decimal(8)}
    assert check('parse(3p)费用为3元','parse(3p)\n====\n费用未知',excerpt='费用未知')=={Decimal(3)}
    assert check('f(7)返回结果','f(7)\n输入未知',excerpt='输入未知')=={Decimal(7)}
