"""有界证据账本与关键结论关联。只校验定位/版本，不认证语义蕴含或事实真伪。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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


def locate_excerpt(text: str, excerpt: str) -> str | None:
    if excerpt in text:
        return excerpt
    words=re.findall(r'\S+',excerpt)
    if not words:
        return None
    match=re.search(r'\s+'.join(re.escape(w) for w in words),text)
    return match[0] if match else None  # 只容许空白差异，保存的是实际原文，不替换词语/标点。


def reading_coverage(records, source: dict) -> dict:
    intervals = sorted((r['start'], r['end']) for r in records
                       if r['kind']=='web' and r.get('url')==source.get('url')
                       and r.get('page_text_sha256')==source.get('page_text_sha256'))
    end = total = 0
    for left, right in intervals:
        total += max(0, right-max(left,end))
        end = max(end,right)
    size = source.get('page_characters')
    return {'delivered_characters':total, 'page_characters':size,
            'full_page_delivered':bool(source.get('page_text_sha256') and size is not None and total==size),
            'meaning':'仅计本次保留的实际送达字符，缓存全文及未命中检索不算已读'}


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
    kind: Literal['direct', 'calculated', 'inference', 'unknown', 'observed']
    supports: list[EvidenceRef] = Field(default_factory=list, max_length=4)
    assumptions: str = Field(default='', max_length=600)
    limits: str = Field(default='', max_length=600)
    scope: Literal['excerpt', 'whole_document'] | None = Field(
        default=None, description='网页结论的覆盖范围；whole_document要求关联网页版本已完整送达。未填不代表全文已读。')
    artifact_version: Literal['current','snapshot'] = Field(
        default='current', description='文件证据默认证明当前版本并检查哈希；snapshot仅描述记录时快照，必须填limits注明历史范围，不证明当前内容。')


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
                    page_characters=item.get('page_characters'), delivery_truncated=shown<len(item['text']))
                if source: ids.append(source)
        elif tool in ('calculator','run_python','days_between'):
            text = '工具输入：'+arguments[:4000]+'\n成功输出：'+content[:6000]
            source = self.add('calculation',text,tool=tool,tool_call_id=call_id,
                              delivery_truncated=len(content)>visible_limit or len(content)>6000 or len(arguments)>4000)
            if source: ids.append(source)
        return ids

    def execution(self, tool: str, arguments: str, content: str, call_id: str, is_error: bool) -> str | None:
        """派发结果仅证明本次工具返回，不把失败变成成功计算/网页证据。"""
        text = ('tool: '+tool+'\noutcome: '+('error' if is_error else 'success')
                +'\narguments:\n'+arguments[:1000]+'\nreturned:\n'+content[:2000])
        return self.add('execution', text, tool=tool, tool_call_id=call_id,
                        is_error=is_error, delivery_truncated=len(arguments)>1000 or len(content)>2000,
                        description='本次工具派发的真实返回；不证明网页事实、外部副作用或所有调用次数')

    def web_coverage(self, source: dict) -> dict:
        return reading_coverage(self.records.values(), source)

    def artifact(self, filename: str) -> str:
        from agent.artifacts import read_json_artifact
        item = read_json_artifact(self.path.parent, filename)
        text = json.dumps(item, ensure_ascii=False, indent=2)
        if len(text)>12000:
            raise ValueError('产物快照超过证据单项上限，未登记；请提供较小的结构化产物')
        for record in self.records.values():
            if record['kind']=='artifact' and record['text']==text:
                return record['id']
        source = self.add('artifact', text, artifact_path=filename, artifact_sha256=item['sha256'],
                          description='本次读取的文件字节版本、JSON样本和类型；不认证业务值正确或文件作者')
        if source is None:
            raise ValueError('证据账本已满，未保留本次产物证据')
        return source

    def validate(self, report: str, claims: list[Claim]) -> list[dict]:
        missing_limits = [f'claims[{i}].limits（原句：{c.quote[:60]}）'
                          for i,c in enumerate(claims) if not c.limits.strip()
                          and any(self.records.get(r.source_id,{}).get('kind')=='history' for r in c.supports)]
        if missing_limits:
            raise ValueError('历史证据须说明记录日期与当前适用性的限制；请逐项填写'+'；'.join(missing_limits)
                             +'。limits是每条claim的字段，不是只在报告正文写日期；unknown引用历史也要填。'
                             '用read_evidence读取真实历史日期，不用当前系统日期补造。')
        result = []
        for index, claim in enumerate(claims):
            if claim.quote not in report:
                raise ValueError('结论原句不在当前报告中：'+claim.quote[:80])
            if claim.kind != 'unknown' and not claim.supports:
                raise ValueError('非unknown结论必须关联实际证据')
            if claim.kind == 'inference' and not claim.assumptions.strip():
                raise ValueError('推断必须说明假设和适用条件')
            refs = []
            for ref in claim.supports:
                source = self.records.get(ref.source_id)
                if not source or sha(source['text']) != source['text_sha256']:
                    raise ValueError('证据ID不存在或哈希无效：'+ref.source_id+'；用read_evidence查看本次保留的ID。')
                resolved=locate_excerpt(source['text'],ref.excerpt)
                if resolved is None:
                    raise ValueError('摘录不在证据'+ref.source_id+'中：'+ref.excerpt[:100]
                        +'；用read_evidence(source_id="'+ref.source_id+'", query="关键词")读取真实原文，不要反复猜ID或重算。')
                if claim.kind == 'direct' and source['kind'] not in ('web','user'):
                    raise ValueError('direct仅用于用户材料/已读原文；历史摘要和计算不能冒充当前直接事实')
                if source['kind'] == 'artifact' and claim.artifact_version=='current':
                    from agent.artifacts import check_artifact_version
                    check_artifact_version(self.path.parent, source)
                if claim.kind == 'observed' and source['kind'] not in ('execution','artifact'):
                    raise ValueError('observed仅关联本次execution工具返回记录或artifact文件记录；用户要求不能证明实际执行')
                refs.append({**ref.model_dump(),'excerpt':resolved,'requested_excerpt':ref.excerpt,
                    'locator':'exact' if resolved==ref.excerpt else 'whitespace',
                    'source_kind':source['kind'],'source_sha256':source['text_sha256']})
            if claim.kind == 'calculated' and not any(r['source_kind']=='calculation' for r in refs):
                raise ValueError('calculated结论必须关联本次成功计算，不能只给原始数字')
            if claim.artifact_version=='snapshot' and (claim.kind!='observed' or not claim.limits.strip()
                    or not refs or any(r['source_kind']!='artifact' for r in refs)):
                raise ValueError('snapshot仅用于observed历史文件快照，须全部关联artifact并填写limits说明记录时范围，不证明当前内容')
            if claim.scope is not None:
                web = [self.records[r['source_id']] for r in refs if r['source_kind']=='web']
                if not web:
                    raise ValueError('scope仅用于关联网页证据的结论')
                if claim.scope=='whole_document' and any(not self.web_coverage(r)['full_page_delivered'] for r in web):
                    raise ValueError('结论声明覆盖全文，但关联网页版本未完整送达；请补读或限定结论范围')
            if claim.kind == 'direct':
                def numbers(text):
                    from decimal import Decimal
                    # 标准编号及本次真实证据ID是引用标记，不是原句中的事实数值。
                    def bracket(match):
                        if match[1].isdigit() or match[1] in self.records:
                            return ''
                        try:
                            values = json.loads(match[0],parse_float=Decimal,parse_int=Decimal)
                            if isinstance(values,list) and all(isinstance(v,Decimal) for v in values):
                                return ' '.join(map(str,values))
                        except ValueError:
                            pass
                        return match[0]
                    text = re.sub(r'\[([^\[\]\n]+)\]',bracket,text)
                    return {Decimal(n.replace(',','')) for n in re.findall(
                        r'(?<![\d.])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?',text)}
                supplied = set().union(*(numbers(r['excerpt']) for r in refs))
                novel = numbers(claim.quote)-supplied
                if novel:
                    calculations = [r['id'] for r in self.records.values() if r['kind']=='calculation'][-8:]
                    raise ValueError(f'claims[{index}].kind=direct原句含摘录未提供的数值{sorted(map(str,novel))}；'
                                     '仅资料部分保留direct；计算部分拆成另一条claims，kind=calculated并关联本次成功计算'
                                     f'{calculations}的真实excerpt（先read_evidence）。不能只改正文或重发同一kind。'
                                     '若确为原始资料，请补上覆盖主体、数值和单位的真实摘录。原句：'+claim.quote[:100])
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
                    restored.append(Claim.model_validate({**{k:c[k] for k in Claim.model_fields if k in c},
                        'supports':[{k:r[k] for k in EvidenceRef.model_fields} for r in c['supports']]}))
                mapped = self.validate(report,restored)
                for old,current in zip(previous.get('claims',[]),mapped):
                    for before,after in zip(old['supports'],current['supports']):
                        requested=before.get('requested_excerpt',before['excerpt'])
                        if locate_excerpt(self.records[after['source_id']]['text'],requested)!=after['excerpt']:
                            raise ValueError('旧摘录定位信息失效')
                        after['requested_excerpt']=requested
                        after['locator']='exact' if requested==after['excerpt'] else 'whitespace'
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
        items, sources, omitted = [], {}, 0
        for claim in mapping.get('claims',[]):
            candidate={key:{**value,'contexts':list(value['contexts'])} for key,value in sources.items()}
            missing=False
            for ref in claim['supports']:
                record = self.records.get(ref['source_id'])
                if not record:
                    missing=True
                    continue
                pos = record['text'].find(ref['excerpt'])
                context = record['text'][max(0,pos-250):pos+len(ref['excerpt'])+250]
                entry=candidate.setdefault(record['id'],{k:v for k,v in record.items() if k!='text'} |
                    {'contexts':[],'context_is_excerpt':len(context)<len(record['text']),
                     **({'read_coverage':self.web_coverage(record)} if record['kind']=='web' else {}),
                     **({'history_header':record['text'].split('历史摘要：',1)[0][:800]} if record['kind']=='history' else {})})
                if not any(context in old for old in entry['contexts']):
                    entry['contexts']=[old for old in entry['contexts'] if old not in context]+[context]
            item = {'claim':claim}
            if missing or len(json.dumps({'items':items+[item],'sources':candidate},ensure_ascii=False))>budget:
                omitted += 1
            else:
                items.append(item)
                sources=candidate
        return {'status':mapping.get('status','unmapped'),'items':items,'sources':sources,'omitted_items':omitted,
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
        if claim.get('scope') is not None:
            web = [records[r['source_id']] for r in claim['supports'] if records[r['source_id']]['kind']=='web']
            if not web or (claim['scope']=='whole_document' and any(
                    not reading_coverage(records.values(),r)['full_page_delivered'] for r in web)):
                raise ValueError('网页结论范围与实际送达记录不匹配')
        for ref in claim['supports']:
            r=records[ref['source_id']]
            if ref['source_sha256']!=r['text_sha256'] or ref['excerpt'] not in r['text']:
                raise ValueError('证据关联失效')
            if r['kind']=='artifact' and claim.get('artifact_version','current')=='current':
                from agent.artifacts import check_artifact_version
                check_artifact_version(root,r)
        if claim.get('artifact_version')=='snapshot' and (claim['kind']!='observed' or not claim.get('limits','').strip()
                or not claim['supports'] or any(records[r['source_id']]['kind']!='artifact' for r in claim['supports'])):
            raise ValueError('历史快照关联缺少类型、来源或适用范围')
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
