"""Read-only task exports. User text is never emitted as executable spreadsheet formulas."""
import csv
import io
import json
from pathlib import Path
import re
from uuid import uuid4

from knowledge.documents import sha
from knowledge.locking import lease
from .store import local_path


NAMES={'summary.md','comparison.csv','comparison.xlsx','sources.json','sources.csv','record.json'}


def safe_csv(value):
    value='' if value is None else str(value)
    return "'"+value if value.lstrip(' \t\r\n\ufeff').startswith(('=','+','-','@')) else value


def csv_bytes(rows):
    stream=io.StringIO(newline='')
    writer=csv.writer(stream,quoting=csv.QUOTE_ALL)
    writer.writerows([[safe_csv(v) for v in row] for row in rows])
    return stream.getvalue().encode('utf-8-sig')


def md(value):
    text=str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    return re.sub(r'([\\`*_{}\[\]()#!|])',r'\\\1',text)


def export_job(jobs,jid):
    job=jobs.get(jid)
    if job['kind']!='answer' or job['status'] in ('running','queued'): raise ValueError('请等待短答结束后导出；失败或中断记录也可导出核对')
    if not re.fullmatch('[a-f0-9]{32}',jid): raise ValueError('任务ID无效')
    root=local_path(jobs.folders.root)
    output=root/'outputs'/jid
    # Reject any pre-existing link before creating or writing into the fixed task directory.
    for p in (root/'outputs',output):
        if p.exists() or p.is_symlink():
            if local_path(p)!=p or not p.is_dir(): raise ValueError('导出路径无效')
        else:p.mkdir()
    if not local_path(output).is_relative_to(root): raise ValueError('导出越出任务目录')
    with lease(output/'.export.lock'):
        manifest=output/'export.json'
        if manifest.is_symlink(): raise ValueError('导出清单不得为链接')
        signature=sha(json.dumps(job,sort_keys=True,ensure_ascii=False))
        if manifest.exists():
            saved=json.loads(manifest.read_text('utf-8'))
            if saved['record_sha256']!=signature: raise ValueError('任务记录发生变化；原导出已保留，未覆盖')
            for name,digest in saved['files'].items():
                if name not in NAMES or local_path(output/name)!=(output/name) or sha((output/name).read_bytes())!=digest:
                    raise ValueError('已有导出文件发生变化；保留原文件，未自动覆盖')
            return {'directory':str(output),'files':list(saved['files']),'cached':True}
        answer=(job.get('result') or {}).get('answer') or {}
        evidence={e['citation_id']:e for e in job.get('evidence',[])}
        source={'task_id':jid,'generation':job['inputs']['generation'],'review_status':'not_reviewed',
                'scope_files':job['inputs']['source_snapshot'],'delivered_evidence':list(evidence.values()),
                'retrieval':job.get('retrieval'),
                'meaning':'范围文件不等于模型阅读全文；引用/定位仅认证保存来源，不认证结论正确。'}
        def refs(ids):
            return '; '.join(cid+' '+evidence[cid]['name']+' '+json.dumps(evidence[cid]['source_location'],ensure_ascii=False)+
                             ' SHA256 '+evidence[cid]['file_sha256'] for cid in ids)
        comparison=[['subject','field','value','status','citations_and_source_versions']]
        for row in answer.get('comparison',[]):comparison.append([row['subject'],row['field'],row['value'],row['status'],refs(row['citations'])])
        sources=[['relative_path','file_sha256','scan_id','generation','delivered_citations','meaning']]
        for doc in source['scope_files']:
            ids=[cid for cid,e in evidence.items() if e['doc_id']==doc['id']]
            sources.append([doc['name'],doc['file_sha256'],doc['scan_id'],source['generation'],','.join(ids),
                            '实际送达有限片段' if ids else '仅在指定范围，不代表已阅读'])
        lines=['# 文件夹资料短答','',md(job['inputs']['query']),'',
               '未正式审稿；执行状态：'+job['status']+'；内容状态：'+(job.get('result') or {}).get('content_status','未形成答案'),
               '固定索引：'+source['generation'],'']
        if job.get('message'): lines.extend([md(job['message']),''])
        for finding in answer.get('findings',[]):lines.extend(['- '+md(finding['kind']+'：'+finding['statement'])+' '+md(' '.join('['+c+']' for c in finding['citations'])),''])
        for unknown in answer.get('unknowns',[]):lines.append('- 待核对：'+md(unknown))
        lines.extend(['','## 实际送达证据',''])
        for cid,e in evidence.items():lines.extend([md('['+cid+'] '+e['name']+' '+json.dumps(e['source_location'],ensure_ascii=False)),
             'SHA256 '+e['file_sha256'],'',*['> '+md(line) for line in e['text'].splitlines()],''])
        payload={'summary.md':'\n'.join(lines).encode('utf-8'), 'comparison.csv':csv_bytes(comparison),'sources.csv':csv_bytes(sources),
                 'sources.json':json.dumps(source,ensure_ascii=False,indent=2).encode('utf-8'),
                 'record.json':json.dumps(job,ensure_ascii=False,indent=2).encode('utf-8')}
        if any((output/name).exists() or (output/name).is_symlink() for name in NAMES):
            raise ValueError('该任务有不完整导出；已保留文件，请核对后手动另存，未覆盖')
        from openpyxl import Workbook
        workbook=Workbook();sheet=workbook.active;sheet.title='comparison'
        for row in comparison:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value,str):cell.data_type='s'
        sheet.freeze_panes='A2'
        info=workbook.create_sheet('sources')
        for row in sources:
            info.append(row)
            for cell in info[info.max_row]:
                if isinstance(cell.value,str):cell.data_type='s'
        buffer=io.BytesIO();workbook.save(buffer);workbook.close();payload['comparison.xlsx']=buffer.getvalue()
        # Write-once files. An interrupted partial export is retained for diagnosis, never replaced.
        for name,content in payload.items():
            with (output/name).open('xb') as stream: stream.write(content)
        with manifest.open('x',encoding='utf-8') as stream:
            json.dump({'record_sha256':signature,'files':{name:sha(data) for name,data in payload.items()}},stream,ensure_ascii=False,indent=2)
        return {'directory':str(output),'files':list(payload),'cached':False}
