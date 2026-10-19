"""有界证据账本与关键结论关联。只校验定位/版本，不认证语义蕴含或事实真伪。"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mapping_sha(mapping: dict) -> str:
    return sha(json.dumps({k:mapping.get(k) for k in ('request_sha256','report_sha256','status','claims')},
                          ensure_ascii=False,sort_keys=True))


def write_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + '.tmp')
    if path.is_symlink() or temp.is_symlink():
        raise ValueError('证据文件不得为符号链接')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temp.replace(path)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = Field(min_length=1, max_length=60)
    excerpt: str = Field(min_length=1, max_length=600)


class Claim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quote: str = Field(min_length=2, max_length=600)
    kind: Literal['direct', 'calculated', 'inference', 'unknown']
    supports: list[EvidenceRef] = Field(default_factory=list, max_length=4)
    assumptions: str = Field(default='', max_length=600)
    limits: str = Field(default='', max_length=600)


class EvidenceLedger:
    MAX_RECORDS = 128
    MAX_TEXT = 300000

    def __init__(self, task_dir: Path, question: str):
        self.path = task_dir/'evidence.json'
        self.request_sha256 = sha(question)
        self.records: dict[str, dict] = {}
        self.omitted = 0
        self.add('user', question, source_id='user-1', description='本次用户输入；不是外部事实认证')

    def add(self, kind: str, text: str, *, source_id: str = '', **metadata) -> str | None:
        if not text:
            return None
        if len(self.records) >= self.MAX_RECORDS or sum(len(r['text']) for r in self.records.values())+min(len(text),12000) > self.MAX_TEXT:
            self.omitted += 1
            self.save()
            return None
        source_id = source_id or f"{kind}-{1+sum(r['kind']==kind for r in self.records.values())}"
        if source_id in self.records:
            raise ValueError('证据ID不可覆盖')
        self.records[source_id] = {'id':source_id, 'kind':kind, 'text':text[:12000],
            'text_sha256':sha(text[:12000]), 'original_sha256':sha(text),
            'truncated':len(text)>12000, 'recorded_at':now(), **metadata}
        self.save()
        return source_id

    def save(self) -> None:
        write_json(self.path, {'schema_version':1,'request_sha256':self.request_sha256,
            'records':list(self.records.values()),'omitted_records':self.omitted,
            'limits':{'max_records':self.MAX_RECORDS,'max_text_characters':self.MAX_TEXT},
            'meaning':'ID、原文和哈希可核对，不保证来源真实或原文支持结论。'})

    def capture(self, tool: str, arguments: str, content: str, call_id: str, metadata: list[dict] | None, visible_limit: int) -> list[str]:
        ids = []
        if tool == 'fetch_url':
            for item in metadata or []:
                start = item['output_start']
                shown = max(0, min(item['output_end'],visible_limit)-start)
                if not shown:
                    continue
                text = content[start:start+shown]
                if text != item['text'][:shown]:
                    continue  # 只记录最终实际返回给模型的前缀；不信任正文自称的偏移。
                source = self.add('web',text,tool_call_id=call_id,url=item['url'],title=item['title'],
                    start=item['start'],end=item['start']+shown, fetched_at=item['fetched_at'],
                    page_date=item['page_date'],page_date_kind='unverified_metadata',
                    page_text_sha256=item.get('page_text_sha256'),
                    delivery_truncated=shown<len(item['text']))
                if source: ids.append(source)
        elif tool in ('calculator','run_python','days_between'):
            text = '工具输入：'+arguments[:4000]+'\n成功输出：'+content[:6000]
            source = self.add('calculation',text,tool=tool,tool_call_id=call_id,
                              delivery_truncated=len(content)>visible_limit or len(content)>6000 or len(arguments)>4000)
            if source: ids.append(source)
        return ids

    def validate(self, report: str, claims: list[Claim]) -> list[dict]:
        result = []
        for claim in claims:
            if claim.quote not in report:
                raise ValueError('结论原句不在当前报告中：'+claim.quote[:80])
            if claim.kind != 'unknown' and not claim.supports:
                raise ValueError('非unknown结论必须关联实际证据')
            if claim.kind == 'inference' and not claim.assumptions.strip():
                raise ValueError('推断必须说明假设和适用条件')
            refs = []
            for ref in claim.supports:
                source = self.records.get(ref.source_id)
                if not source or sha(source['text']) != source['text_sha256'] or ref.excerpt not in source['text']:
                    raise ValueError('证据ID不存在、哈希无效或摘录未逐字出现在实际返回内容中：'+ref.source_id)
                if claim.kind == 'direct' and source['kind'] not in ('web','user'):
                    raise ValueError('direct仅用于用户材料/已读原文；历史摘要和计算不能冒充当前直接事实')
                refs.append({**ref.model_dump(),'source_kind':source['kind'],'source_sha256':source['text_sha256']})
            if claim.kind == 'calculated' and not any(r['source_kind']=='calculation' for r in refs):
                raise ValueError('calculated结论必须关联本次成功计算，不能只给原始数字')
            if any(r['source_kind']=='history' for r in refs) and not claim.limits.strip():
                raise ValueError('历史证据须说明记录日期与当前适用性的限制')
            result.append({**claim.model_dump(),'supports':refs,'link_status':'located',
                           'semantic_support':'not_certified'})
        return result

    def prepare(self, path: Path, report: str, claims: list[Claim] | None, *, complete: bool) -> dict:
        previous = {}
        sidecar = path.with_suffix('.evidence.json')
        broken = False
        if sidecar.is_file() and not sidecar.is_symlink():
            try:
                if sidecar.stat().st_size > 100000: raise ValueError('关联文件过大')
                previous = json.loads(sidecar.read_text(encoding='utf-8'))
                if not isinstance(previous,dict): raise ValueError('关联格式无效')
            except (ValueError,OSError):
                previous,broken = {},True
        same = previous.get('report_sha256') == sha(report) and previous.get('request_sha256') == self.request_sha256
        if claims is not None:
            mapped = self.validate(report,claims)
        elif same:
            try:
                restored=[]
                for c in previous.get('claims',[]):
                    for ref in c['supports']:
                        if self.records[ref['source_id']]['text_sha256']!=ref['source_sha256']:
                            raise ValueError('旧证据关联版本失效')
                    restored.append(Claim.model_validate({**{k:c[k] for k in Claim.model_fields},
                        'supports':[{k:r[k] for k in EvidenceRef.model_fields} for r in c['supports']]}))
                mapped = self.validate(report,restored)
            except (ValueError,KeyError,TypeError):
                mapped,broken = [],True
        else:
            mapped = []
        invalidated = (max(int(previous.get('invalidated_claims',0)),int(broken),
                          len(previous.get('claims',[])) if not same else 0) if claims is None else 0)
        return {'schema_version':1,'request_sha256':self.request_sha256,'report_sha256':sha(report),
            'status':'draft' if not complete else 'mapped' if mapped else 'stale' if invalidated else 'unmapped',
            'claims':mapped,'invalidated_claims':invalidated,'previous_report_sha256':previous.get('report_sha256'),
            'coverage':'仅作者选择的关键结论；未关联的其余文本未认证。located只校验定位，不校验蕴含。',
            'semantic_support':'not_certified'}

    def packet(self, mapping: dict, budget: int = 14000) -> dict:
        items, omitted = [], 0
        for claim in mapping.get('claims',[]):
            sources = []
            for ref in claim['supports']:
                record = self.records.get(ref['source_id'])
                if not record:
                    omitted += 1
                    continue
                pos = record['text'].find(ref['excerpt'])
                context = record['text'][max(0,pos-250):pos+len(ref['excerpt'])+250]
                sources.append({k:v for k,v in record.items() if k!='text'} | {'context':context,
                    'context_is_excerpt':len(context)<len(record['text']),
                    **({'history_header':record['text'].split('历史摘要：',1)[0][:800]} if record['kind']=='history' else {})})
            item = {'claim':claim,'sources':sources}
            if len(json.dumps(items+[item],ensure_ascii=False))>budget:
                omitted += 1
            else: items.append(item)
        return {'status':mapping.get('status','unmapped'),'items':items,'omitted_items':omitted,
                'ledger_omitted_records':self.omitted,'semantic_support':'not_certified'}


def audit_mapping(root: Path, report_name: str = 'report.md') -> dict:
    ledger=json.loads((root/'evidence.json').read_text(encoding='utf-8'))
    path=root/Path(report_name).name
    mapping=json.loads(path.with_suffix('.evidence.json').read_text(encoding='utf-8'))
    if mapping['request_sha256']!=ledger['request_sha256'] or mapping['report_sha256']!=sha(path.read_text(encoding='utf-8')):
        raise ValueError('任务/报告版本不匹配，关联已过期')
    records={r['id']:r for r in ledger['records']}
    if len(records)!=len(ledger['records']): raise ValueError('证据ID重复')
    for r in records.values():
        if sha(r['text'])!=r['text_sha256']: raise ValueError('证据正文哈希不匹配')
        if r['kind']=='web' and r['end']-r['start']!=len(r['text']): raise ValueError('网页偏移不匹配')
    report=path.read_text(encoding='utf-8')
    for claim in mapping['claims']:
        if claim['quote'] not in report: raise ValueError('结论原句失效')
        for ref in claim['supports']:
            r=records[ref['source_id']]
            if ref['source_sha256']!=r['text_sha256'] or ref['excerpt'] not in r['text']:
                raise ValueError('证据关联失效')
    return {'status':mapping['status'],'claims':len(mapping['claims']),
            'mapping_sha256':mapping_sha(mapping),
            'claim_kinds':sorted({c['kind'] for c in mapping['claims']}),
            'source_kinds':sorted({r['source_kind'] for c in mapping['claims'] for r in c['supports']}),
            'records':len(records),'omitted_records':ledger['omitted_records'],
            'structural_links_valid':True,'semantic_support':'not_certified'}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='离线核对证据ID、摘录、哈希和报告版本；不调用模型')
    parser.add_argument('task_dir',type=Path)
    parser.add_argument('--report',default='report.md')
    args=parser.parse_args(argv)
    try:
        result=audit_mapping(args.task_dir,args.report)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['status']=='mapped' else 1
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print('证据核对失败：'+str(exc))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
