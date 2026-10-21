"""只读汇总真实调用事件；Decimal费率估算与任务费率快照。"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.config import ProviderName

SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'


class PriceProfile(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    provider: ProviderName
    model: str = Field(min_length=1,max_length=160)
    label: str = Field(default='自定义费率',min_length=1,max_length=120)
    input_per_million: Decimal = Field(ge=0,le=1000000)
    cached_per_million: Decimal = Field(ge=0,le=1000000)
    output_per_million: Decimal = Field(ge=0,le=1000000)
    source: str = Field(default='用户配置',max_length=300)
    verified_at: str = Field(default='',max_length=40)


def flash_profile(peak=True):
    return PriceProfile(provider='deepseek',model='deepseek-flash',
        label='Flash高峰参考（固定费率）' if peak else 'Flash空闲参考（固定费率）',
        input_per_million=2 if peak else 1,cached_per_million='0.04' if peak else '0.02',
        output_per_million=8 if peak else 4,source=SOURCE,verified_at='2026-10-19').model_dump(mode='json')


class PricingBook:
    def __init__(self,path:Path):
        self.path=path
        self.lock=threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():return {'profiles':[flash_profile()],'warning':None}
            try:
                if self.path.is_symlink() or self.path.stat().st_size>100000:raise ValueError('invalid pricing file')
                data=json.loads(self.path.read_text(encoding='utf-8'))
                profiles=[PriceProfile.model_validate(p).model_dump(mode='json') for p in data['profiles']]
                identities=[(p['provider'],p['model']) for p in profiles]
                if len(set(identities))!=len(identities):raise ValueError('duplicate model')
                return {'profiles':profiles,'warning':None}
            except (OSError,ValueError,KeyError,TypeError):
                return {'profiles':[],'warning':'费率配置不可读；费用暂停估算，请重新保存费率。'}

    def get(self,provider,model):
        return next((p for p in self.read()['profiles'] if p['provider']==provider and p['model']==model),None)

    def save(self,provider,model,profile=None):
        with self.lock:
            data=self.read()
            profiles=[p for p in data['profiles'] if (p['provider'],p['model'])!=(provider,model)]
            if profile:profiles.append(profile.model_dump(mode='json'))
            if len(profiles)>100:raise ValueError('最多保存100组模型费率')
            temporary=self.path.with_suffix('.tmp')
            if self.path.is_symlink() or temporary.is_symlink():raise ValueError('费率文件不得为符号链接')
            temporary.write_text(json.dumps({'profiles':profiles},ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.path)
            return self.read()


class TraceUsageReader:
    """只增量读取新完整行；统计不受前端400事件展示上限影响。"""
    def __init__(self):
        self.cache={}
        self.lock=threading.RLock()

    def read(self,path:Path):
        with self.lock:
            try:
                if path.is_symlink():return None
                stat=path.stat()
            except OSError:return None
            key=str(path)
            previous=self.cache.get(key)
            signature=(stat.st_dev,stat.st_ino)
            if (previous is None or previous['identity']!=signature or stat.st_size<previous['position']
                or (previous['size']==stat.st_size and previous['mtime']!=stat.st_mtime_ns)):
                previous={'identity':signature,'position':0,'events':[],'embedding_events':[],'invalid_lines':0,'finished':False}
            with path.open('rb') as stream:
                stream.seek(previous['position'])
                for _ in range(100000):
                    line=stream.readline(1_000_000)
                    if not line:break
                    if len(line)>=1_000_000 and not line.endswith(b'\n'):
                        while line and not line.endswith(b'\n'):line=stream.readline(1_000_000)
                        previous['invalid_lines']+=1;previous['position']=stream.tell()
                        continue
                    if not line.endswith(b'\n'):break
                    previous['position']=stream.tell()
                    try:event=json.loads(line)
                    except (ValueError,UnicodeDecodeError):previous['invalid_lines']+=1;continue
                    if not isinstance(event,dict):previous['invalid_lines']+=1;continue
                    kind=event.get('kind')
                    if not isinstance(kind,str):previous['invalid_lines']+=1;continue
                    if kind in {'llm_call','llm_error'}:previous['events'].append(event)
                    elif kind=='embedding_call':previous['embedding_events'].append(event)
                    elif kind in {'run_start','run_resume'}:previous['finished']=False
                    elif kind=='run_end':previous['finished']=True
            previous.update(size=stat.st_size,mtime=stat.st_mtime_ns)
            self.cache[key]=previous
            return {**previous,'events':list(previous['events']),'partial_line':previous['position']<stat.st_size}


def token(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None


def bucket():
    return {'calls':0,'returned':0,'failed':0,'input_tokens':0,'cached_tokens':0,'uncached_tokens':0,
            'output_tokens':0,'priced_calls':0,'unpriced_calls':0,'known_cost':Decimal(0)}


def summarize(trace,row,book:PricingBook):
    options=row.get('options') or {}
    warnings=set();reasons=set();profiles={};bases=set()
    pricing_data=book.read()
    current_profiles={(p['provider'],p['model']):p for p in pricing_data['profiles']}
    if pricing_data['warning']:warnings.add(pricing_data['warning'])
    totals=bucket();groups={name:bucket() for name in ('planning','agent','report_review','other')}
    attempts_lower=0;retry_lower=0;attempts_complete=True;usage_unknown=0;cache_unknown=0;cache_input=0;cache_known=0
    events=trace['events'] if trace else []
    available=trace is not None or row.get('state')=='queued' or row.get('result',{}).get('usage_not_started') is True
    for event in events:
        purpose=event.get('purpose')
        purpose=purpose if isinstance(purpose,str) and purpose in groups else 'other'
        group=groups[purpose]
        failed=event['kind']=='llm_error'
        for target in (totals,group):target['calls']+=1;target['failed' if failed else 'returned']+=1
        attempts=token(event.get('request_attempts'))
        attempts_complete &= attempts is not None
        attempts_lower+=attempts if attempts is not None else (0 if failed else 1)
        retry_lower+=max(0,attempts-1) if attempts is not None else 0
        if failed:
            for target in (totals,group):target['unpriced_calls']+=1
            reasons.add('失败请求的实际用量未知')
            continue
        p=token(event.get('prompt_tokens'));c=token(event.get('completion_tokens'));hit=token(event.get('cached_tokens'))
        usage_ok=event.get('usage_reported') is not False and p is not None and c is not None
        cache_ok=usage_ok and event.get('cache_usage_reported') is not False and hit is not None and hit<=p
        if 'usage_reported' not in event:warnings.add('旧日志未标记用量完整性，按已保存数值统计；旧失败/重试可能未记录。')
        if not usage_ok:usage_unknown+=1
        if usage_ok:
            for target in (totals,group):target['input_tokens']+=p;target['output_tokens']+=c
            if cache_ok:
                cache_input+=p;cache_known+=hit
                for target in (totals,group):target['cached_tokens']+=hit;target['uncached_tokens']+=p-hit
            else:cache_unknown+=1
        provider=event.get('provider') or options.get('provider')
        model=event.get('requested_model') or event.get('response_model')
        provider=provider if isinstance(provider,str) else None
        model=model if isinstance(model,str) else None
        snapshot=options.get('pricing')
        if isinstance(snapshot,dict) and (snapshot.get('provider'),snapshot.get('model'))==(provider,model):
            raw=snapshot;basis='task_snapshot'
        else:
            raw=current_profiles.get((provider,model));basis='current_reference'
        profile=None
        if raw:
            try:profile=PriceProfile.model_validate(raw)
            except ValueError:reasons.add('任务费率快照无效')
        if usage_ok and profile and (cache_ok or p==0 or profile.input_per_million==profile.cached_per_million):
            cached=hit if cache_ok else 0
            cost=(Decimal(p-cached)*profile.input_per_million+Decimal(cached)*profile.cached_per_million
                  +Decimal(c)*profile.output_per_million)/1_000_000
            for target in (totals,group):target['known_cost']+=cost;target['priced_calls']+=1
            bases.add(basis);profiles[(provider,model,basis)]=profile.model_dump(mode='json')
        else:
            for target in (totals,group):target['unpriced_calls']+=1
            if not usage_ok:reasons.add('模型未提供完整token用量')
            elif not profile:reasons.add('厂商/模型缺失或未配置匹配费率')
            else:reasons.add('缓存输入拆分未知，无法准确应用两档输入费率')
    if trace:
        if trace['invalid_lines']:warnings.add('日志包含损坏或超长事件，统计可能不完整。')
        if trace['partial_line']:warnings.add('日志末行尚不完整，暂未计入。')
        if not trace['finished'] and row.get('state') not in {'queued','resuming','running','cancelling'}:
            warnings.add('未取得完整结束记录，可能存在未返回或未落盘的请求。')
    elif not available:warnings.add('运行日志缺失；不把未知调用和费用记为零。')
    if not attempts_complete:warnings.add('部分旧调用未记录请求尝试数；重试次数只能给出下限。')
    def finish(value):
        value={**value,'known_cost':str(value['known_cost'])}
        readable=trace is None or (not trace['invalid_lines'] and not trace['partial_line'])
        value['estimated_cost']=value['known_cost'] if value['unpriced_calls']==0 and available and readable else None
        return value
    return {'available':available,**finish(totals),'by_purpose':{k:finish(v) for k,v in groups.items()},
        'http_attempts_lower_bound':attempts_lower,'http_attempts_complete':attempts_complete,
        'retry_attempts_lower_bound':retry_lower,
        'unknown_usage_calls':usage_unknown,'unknown_cache_calls':cache_unknown,
        'cache_hit_ratio':cache_known/cache_input if cache_input else None,
        'currency':'CNY','pricing_basis':sorted(bases),'profiles':list(profiles.values()),
        'unpriced_reasons':sorted(reasons),'warnings':sorted(warnings),
        'scope':'已记录调用的模型token估算；不含未返回用量、工具服务费用或账户优惠，不是平台账单。'}
