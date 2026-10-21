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
