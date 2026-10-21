import json

from agent.evidence import EvidenceLedger
from tools.evidence import make_read_evidence
from tools.registry import ToolRegistry
from tools.report import make_write_report
from tools.session import ResearchSession


def test_read_support_is_bounded_exact_and_bound_to_requested_source(tmp_path):
    ledger=EvidenceLedger(tmp_path,'仅依据给定材料')
    source=ledger.add('user','甲'*800+'目标27.40元'+'乙'*1400)
    session=ResearchSession(evidence=ledger)
    reg=ToolRegistry([make_read_evidence(session)])
    result=json.loads(reg.execute('read_evidence',{'source_id':source,'query':'目标'}).content)
    support=result['support']
    assert support['source_id']==source and len(support['excerpt'])<=600
    assert '目标27.40元' in support['excerpt']
    left,right=result['support_text_range']
    assert ledger.records[source]['text'][left:right]==support['excerpt']
    missing=json.loads(reg.execute('read_evidence',{'source_id':source,'query':'未读原文'}).content)
    assert missing['match'] is False and 'support' not in missing


def test_wrong_reference_nesting_remains_error_and_does_not_write(tmp_path):
    session=ResearchSession(evidence=EvidenceLedger(tmp_path,'报价27元'))
    reg=ToolRegistry([make_write_report(tmp_path,session,question='报价27元')])
    result=reg.execute('write_report',{'content':'报价27元',
        'claims':[{'quote':'报价27元','kind':'direct','source_id':'user-1','excerpt':'报价27元'}]})
    assert result.is_error and 'supports' in result.content and 'claims' in result.content
    assert not (tmp_path/'report.md').exists()
