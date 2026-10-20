"""阶段12：冻结清单、三轮独立评估、故障注入及只读复核。默认不调用模型。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import httpx

from agent.config import PROJECT_ROOT, Settings, load_llm_config
from agent.eval_checks import assess_run, check
from agent.evaluate import metadata, new_output

SUITE = Path(__file__).with_name('validation_cases.json')
NO_TASK_STATUSES = {'pending', 'skipped_dependency', 'skipped_budget'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def load_suite(path=SUITE):
    suite = read(path)
    if suite.get('version') != 1 or not suite.get('cases'):
        raise ValueError('未知题集版本或空题集')
    ids = set()
    prior = {}
    for c in suite['cases']:
        if c['id'] in ids or c['split'] not in ('development', 'holdout'):
            raise ValueError('重复题目或非法分组')
        if c.get('depends_on') and c['depends_on'] not in ids:
            raise ValueError('依赖必须先定义')
        if c.get('depends_on') and prior[c['depends_on']] != c['split']:
            raise ValueError('依赖不能跨开发/保留分组')
        if not c.get('rubric') or not c.get('prompt'):
            raise ValueError('缺少原问题或独立核对依据')
        ids.add(c['id'])
        prior[c['id']] = c['split']
    return suite


def identity(suite, split):
    # 包含所有运行代码、题集、评分器和测试，不包括输出文件与事后文档。
    base = metadata('live', {'version': suite['version']}, [])
    s = Settings()
    # 冻结/只读复核只需要公开配置，不要求持有真实Key；真实run仍单独验证项目Key。
    cfg = load_llm_config(provider='deepseek', model='deepseek-flash',
                          settings=s.model_copy(update={'deepseek_api_key': '<identity-only>'}))
    config = {k: v for k, v in asdict(cfg).items() if k != 'api_key'}
    config['settings'] = {k: v for k, v in s.model_dump(mode='json').items()
                          if not k.endswith('_api_key') and k != 'memory_db_path'}
    return {k: base[k] for k in ('code_sha256', 'grader_sha256')} | {
        'suite_sha256': digest(suite), 'split': split, 'config': config,
        'selection': [c['id'] for c in suite['cases'] if c['split'] == split],
        'policy': suite['policy'], 'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def freeze(suite, split, path):
    if path.exists():
        raise ValueError('冻结文件已存在；禁止覆盖')
    state = identity(suite, split)
    state['git_revision'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PROJECT_ROOT, text=True).strip()
    state['created_at'] = datetime.now(timezone.utc).isoformat()
    state['lock_sha256'] = digest(state)
    save(path, state)
    return state


def verify_lock(lock, suite):
    content = {k: v for k, v in lock.items() if k != 'lock_sha256'}
    if digest(content) != lock.get('lock_sha256'):
        raise ValueError('冻结清单摘要不一致')
    actual = identity(suite, lock['split'])
    if any(lock.get(k) != v for k, v in actual.items()):
        raise ValueError('代码、题集、评分规则、配置、选择或预算已变化，拒绝混入冻结运行')


def artifacts(task):
    return {p.relative_to(task).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(task.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


@contextmanager
def fixture_tools(case):
    """只在此顺序执行的评估进程注入，正式 CLI/桌面入口不受影响。

    固定网页仍走真正fetch提取/分页/证据工具；外网一律拒绝，关闭Jina。
    """
    from tools import default_registry
    from tools.registry import tool
    from tools.search import SearchResult
    pages = case.get('pages', {})
    mode = case.get('environment', 'standard')
    if mode == 'standard':
        yield
        return

    class Backend:
        name = 'stage12-fixture'

        def search(self, query, max_results):
            if mode == 'network_down':
                raise RuntimeError('阶段12注入：搜索网络不可用')
            return [SearchResult(url, url, '合成评估资料，不代表真实网站') for url in pages][:max_results]

    def respond(request):
        if mode == 'network_down' or str(request.url) not in pages:
            raise httpx.ConnectError('阶段12注入：网络不可用/未声明URL', request=request)
        return httpx.Response(200, text=pages[str(request.url)], headers={'content-type': 'text/html; charset=utf-8'})

    @tool(name='calculator')
    def broken_calculator(expression: str) -> str:
        """计算数学表达式。"""
        raise RuntimeError('阶段12注入：计算服务失败，没有数值结果')

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        def factory(*args, **kwargs):
            kwargs.update(search_backend=Backend(), http_client=client, fetch_fallback='none')
            registry = default_registry(*args, **kwargs)
            if mode == 'tool_down':
                registry._tools['calculator'] = broken_calculator  # 此评估实例内替换，不注册第二个同名工具
            return registry
        with patch('main.default_registry', factory):
            yield


def number_tree(value):
    if isinstance(value, dict):
        return {k: number_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [number_tree(v) for v in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    return value


def assess(case, task):
    record = assess_run(case, task)
    events = [json.loads(line) for line in (task / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    report = (task / 'report.md').read_text(encoding='utf-8') if (task / 'report.md').exists() else ''
    checks = record['checks']
    fault = case.get('fault')
    if fault == 'budget':
        # 预算耗尽是预期失败；不要求成功报告，却要求真实状态和未认证审稿。
        review = read(task / 'report.review.json') if (task / 'report.review.json').exists() else {}
        record['task_outcome'] = 'expected_incomplete'
        checks = [check('budget_stop', record['metrics']['stop_reason'] == 'budget'),
                  check('no_false_clean_review', review.get('status') != 'reviewed'),
                  check('end_recorded', any(e['kind'] == 'run_end' for e in events))]
    elif fault:
        checks.append(check('fault_actually_observed', any(e['kind'] == 'tool_result' and e.get('is_error')
                                                        and e.get('name') in case['fault_tools'] for e in events)))
        checks.append(check('limitations_disclosed', any(w in report for w in ('失败', '不可用', '无法', '未能'))))
        record['task_outcome'] = 'limited_report'
    else:
        record['task_outcome'] = 'normal'
    if case.get('network_forbidden'):
        checks.append(check('no_network_tools', not any(e['kind'] == 'tool_call' and e.get('name') in
                                                       ('web_search', 'fetch_url') for e in events)))
    if case.get('recall'):
        snapshot = read(task / 'memory_context.json') if (task / 'memory_context.json').exists() else {}
        checks.append(check('actual_memory_recall', bool(snapshot.get('record_ids'))))
    if case.get('no_calendar_date'):
        import re
        checks.append(check('no_invented_calendar_date', not re.search(r'20\d{2}[-年/.]', report)))
    for filename, expected in case.get('expected_json', {}).items():
        try:
            actual = read(task / filename)
            checks.append(check('independent_answer_' + filename, number_tree(actual) == number_tree(expected),
                                json.dumps(actual, ensure_ascii=False)))
        except (OSError, ValueError):
            checks.append(check('independent_answer_' + filename, False, '缺失或非法JSON'))
    record.update(checks=checks, status='pass' if all(c['passed'] for c in checks) else 'fail',
                  group='fault' if fault else 'normal', semantic_status='pending_independent_review',
                  artifact_sha256=artifacts(task))
    return record


def aggregate(data, suite):
    rounds = []
    for r in range(1, data['rounds'] + 1):
        items = [c for c in data['cases'] if c['round'] == r]
        groups = {}
        for group in ('normal', 'fault'):
            selected = [c for c in items if c['group'] == group]
            passed = sum(c['status'] == 'pass' for c in selected)
            groups[group] = {'passed': passed, 'selected': len(selected),
                             'rate': passed / len(selected) if selected else None}
        rounds.append({'round': r, **groups})
    records = data['cases']
    threshold = suite['policy']['normal_min_rate']
    auto = data['state'] == 'complete' and data['rounds'] == 3 and all(
        r['normal']['rate'] is not None and r['normal']['rate'] >= threshold
        and r['fault']['rate'] == 1 for r in rounds)
    return {'rounds': rounds, 'automatic_gate': auto,
            'acceptance': 'pending_independent_review' if auto else 'not_accepted',
            'metrics': {k: sum(c.get('metrics', {}).get(k, 0) or 0 for c in records)
                        for k in ('steps', 'seconds', 'equivalent_tokens', 'llm_calls', 'review_calls',
                                  'input_tokens', 'cached_tokens', 'output_tokens')},
            'note': '自动通过不等于语义正确；需逐题核对原文、计算、条件、类型和故障状态。'}


def report(out, data):
    lines = ['# 阶段12评估', '', f"执行方式：{data['execution']}；状态：{data['state']}",
             f"冻结代码：{data['lock']['code_sha256']}；题集：{data['lock']['suite_sha256']}", '',
             '## 自动检查（实际轮次数见表）', '', '| 轮次 | 正常任务 | 故障状态正确性 |', '|---|---|---|']
    for r in data['summary']['rounds']:
        lines.append(f"| {r['round']} | {r['normal']['passed']}/{r['normal']['selected']} | {r['fault']['passed']}/{r['fault']['selected']} |")
    lines += ['', '自动门槛：' + str(data['summary']['automatic_gate']),
              '人工内容核对尚未自动通过；review-template.json提供逐题依据。模型审稿和定位通过不是事实认证。', '',
              '## 逐题状态', '', '| 轮次 | 题目 | 自动状态 | 任务终止原因 | 审稿状态 |', '|---|---|---|---|---|']
    for c in data['cases']:
        lines.append(f"| {c['round']} | {c['id']} | {c['status']} | {c.get('metrics', {}).get('stop_reason', '—')} | {c.get('review_status', '—')} |")
    lines += ['', '## 失败详情', '']
    for c in data['cases']:
        if c['status'] != 'pass':
            lines.append(f"- 第{c['round']}轮 {c['id']}：" + '；'.join(x['name'] + ' ' + x.get('detail', '') for x in c.get('checks', []) if not x['passed']) + c.get('error_type', ''))
    lines += ['', '总计量：`' + json.dumps(data['summary']['metrics'], ensure_ascii=False) + '`', '',
              '等效token是预算权重，不是人民币账单。题目来源与工具环境以冻结题集为准；开发集、合成网页与单轮结果不能自动认定为独立验收或真实外网稳定性证明。']
    (out / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run(suite, lock, out, rounds):
    from agent.llm import LLMClient
    from main import research
    verify_lock(lock, suite)
    cases = [c for c in suite['cases'] if c['split'] == lock['split']]
    data = {'schema_version': 1, 'execution': 'live', 'state': 'running', 'rounds': rounds,
            'lock': lock, 'cases': [{'id': c['id'], 'round': r, 'group': 'fault' if c.get('fault') else 'normal',
                                   'status': 'pending'} for r in range(1, rounds + 1) for c in cases]}
    settings = Settings()
    cfg = load_llm_config(provider='deepseek', model='deepseek-flash', settings=settings)
    used_by_round = {r: 0 for r in range(1, rounds + 1)}
    try:
        for entry in data['cases']:
            verify_lock(lock, suite)
            c = next(c for c in cases if c['id'] == entry['id'])
            r = entry['round']
            if used_by_round[r] >= suite['policy']['total_budget_per_round']:
                entry['status'] = 'skipped_budget'
            elif c.get('depends_on') and not any(e['id'] == c['depends_on'] and e['round'] == r
                                                and e['status'] == 'pass' for e in data['cases']):
                entry['status'] = 'skipped_dependency'
            else:
                task = out / 'cases' / f'{r:02d}-{c["id"]}'
                entry.update(status='running', task_dir=str(task.resolve()), artifact_sha256={})
                task_settings = settings.model_copy(update={'memory_enabled': c.get('memory', False),
                    'memory_db_path': out / f'memory-{r:02d}.sqlite3', 'report_review_enabled': True,
                    'report_review_max_calls': 2})
                budget = c.get('budget', suite['policy']['per_case_budget'])
                budget = min(budget, suite['policy']['total_budget_per_round'] - used_by_round[r])
                try:
                    with fixture_tools(c):
                        result = research(LLMClient(cfg), task_settings, c['prompt'],
                                          suite['policy']['max_steps'], budget, task_dir=task)
                    used_by_round[r] += result.equivalent_tokens
                    entry['metrics'] = {'equivalent_tokens': result.equivalent_tokens, 'steps': result.steps}
                    entry.update(assess(c, task))
                    if result.stop_reason == 'interrupted':
                        raise KeyboardInterrupt
                except KeyboardInterrupt:
                    if entry['status'] == 'running':
                        entry['status'] = 'interrupted'
                    raise
                except Exception as exc:
                    # 仅保存异常类型，避免SDK异常意外回显凭据。
                    entry.update(status='error', error_type=type(exc).__name__, task_dir=str(task.resolve()),
                                 artifact_sha256=artifacts(task) if task.exists() else {})
                    if (task / 'trace.jsonl').exists():
                        events = [json.loads(line) for line in (task / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
                        end = next((e for e in reversed(events) if e['kind'] == 'run_end'), {})
                        if 'metrics' not in entry:
                            known = end.get('equivalent_tokens', sum(e.get('equivalent_tokens', 0) for e in events if e['kind'] == 'llm_call'))
                            used_by_round[r] += known
                            entry['metrics'] = {'equivalent_tokens': known, 'accounting': 'trace_only_incomplete'}
                finally:
                    entry['artifact_sha256'] = artifacts(task) if task.exists() else {}
            data['summary'] = aggregate(data, suite)
            save(out / 'results.json', data)
        verify_lock(lock, suite)
        data['state'] = 'complete'
    except KeyboardInterrupt:
        data['state'] = 'interrupted'
    except ValueError:
        data['state'] = 'invalidated'
        raise
    finally:
        data['summary'] = aggregate(data, suite)
        save(out / 'results.json', data)
        report(out, data)
        worksheet = review_template(data, cases)
        save(out / 'review-template.json', worksheet)
    return data


def rescore(source, suite, out):
    if digest(suite) != source['lock']['suite_sha256']:
        raise ValueError('题集已变化，不能重评旧题')
    data = {**source, 'execution': 'rescore', 'source_results_sha256': digest(source),
            'current_identity': identity(suite, source['lock']['split']), 'cases': []}
    for old in source['cases']:
        entry = dict(old)
        if old.get('task_dir'):
            task = Path(old['task_dir'])
            if artifacts(task) != old['artifact_sha256']:
                raise ValueError('原始产物发生变化，拒绝复评')
            entry.update(assess(next(c for c in suite['cases'] if c['id'] == old['id']), task))
            entry['source_status'] = old['status']
        data['cases'].append(entry)
    data['summary'] = aggregate(data, suite)
    save(out / 'results.json', data)
    report(out, data)
    return data


def review_key(record):
    if (not isinstance(record, dict) or not isinstance(record.get('id'), str)
            or not record['id'].strip() or type(record.get('round')) is not int or record['round'] < 1):
        raise ValueError('核对条目必须包含非空题目id和正整数round')
    return record['id'], record['round']


def review_cases(source):
    if not isinstance(source, dict) or not isinstance(source.get('cases'), list) or not source['cases']:
        raise ValueError('原始结果必须包含非空cases列表')
    indexed = {}
    for record in source['cases']:
        key = review_key(record)
        if key in indexed:
            raise ValueError(f'原始结果重复条目：第{key[1]}轮 {key[0]}')
        if not isinstance(record.get('status'), str) or not record['status']:
            raise ValueError(f'第{key[1]}轮 {key[0]}：原始自动状态缺失或无效')
        indexed[key] = record
    return indexed


def artifact_version(record):
    """仅未执行/待执行且未记录目录时，兼容缺失/null/旧模板{}；不认证执行事实。"""
    key = review_key(record)
    label = f'第{key[1]}轮 {key[0]}'
    value = record.get('artifact_sha256')
    directory = record.get('task_dir')
    if directory is not None and (not isinstance(directory, str) or not directory.strip()):
        raise ValueError(label + '：任务目录无效')
    if not record.get('task_dir'):
        if (not isinstance(record.get('status'), str) or record['status'] not in NO_TASK_STATUSES
                or value not in (None, {})):
            raise ValueError(label + '：无任务目录且不是待执行/跳过记录，不能按无产物处理')
        return None
    if not isinstance(value, dict):
        raise ValueError(label + '：已执行项缺少产物哈希映射，不能按无产物处理')
    for name, checksum in value.items():
        if (not isinstance(name, str) or not name or not isinstance(checksum, str)
                or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum)):
            raise ValueError(label + '：产物哈希映射无效')
    return dict(value)


def review_template(source, cases):
    definitions = {c['id']: c for c in cases}
    bound = digest(source)
    worksheet = []
    for record in review_cases(source).values():
        definition = definitions.get(record['id'])
        if not definition or not definition.get('rubric'):
            raise ValueError('题集缺少对应核对依据：' + record['id'])
        worksheet.append({'id': record['id'], 'round': record['round'],
            'source_results_sha256': bound, 'automatic_status': record['status'],
            'task_dir': record.get('task_dir'), 'artifact_sha256': artifact_version(record),
            'verdict': 'pending', 'findings': [], 'reviewer': '', 'reviewer_kind': '',
            'rubric': definition['rubric']})
    return worksheet


def review(source, decisions):
    expected = review_cases(source)
    if (not isinstance(decisions, list) or len(decisions) != len(expected)
            or len({review_key(d) for d in decisions}) != len(expected)):
        raise ValueError('必须逐题逐轮核对，不能缺项或重复')
    source_hash = digest(source)
    counts = {key: 0 for key in ('pending', 'pass', 'fail', 'uncertain')}
    for d in decisions:
        key = review_key(d)
        label = f'第{key[1]}轮 {key[0]}：'
        original = expected.get(key)
        if original is None:
            raise ValueError(label + '不是原始结果中的条目')
        if 'source_results_sha256' in d and d['source_results_sha256'] != source_hash:
            raise ValueError(label + '核对表绑定的原始结果不同')
        version = artifact_version(original)
        selected = d.get('artifact_sha256')
        if version is None:
            if selected not in (None, {}):
                raise ValueError(label + '未执行项不能附加产物哈希')
        elif selected != version:
            raise ValueError(label + '核对的产物版本不同')
        if original.get('task_dir') and artifacts(Path(original['task_dir'])) != version:
            raise ValueError(label + '原始产物已变化')
        verdict = d.get('verdict')
        reviewer, kind = d.get('reviewer', ''), d.get('reviewer_kind', '')
        if (not isinstance(verdict, str) or verdict not in counts or not isinstance(reviewer, str)
                or kind not in ('', 'human', 'coding_assistant')
                or (verdict != 'pending' and (not reviewer.strip() or not kind))):
            raise ValueError(label + '核对人身份、类型或结论无效')
        if not isinstance(d.get('findings', []), list) or any(not isinstance(x, str) for x in d.get('findings', [])):
            raise ValueError(label + 'findings必须是文字列表')
        counts[verdict] += 1
    complete = counts['pending'] == 0
    human = complete and all(d['reviewer_kind'] == 'human' for d in decisions)
    clean = all(d['verdict'] == 'pass' for d in decisions)
    if not isinstance(source.get('summary'), dict) or type(source['summary'].get('automatic_gate')) is not bool:
        raise ValueError('原始结果缺少布尔型automatic_gate')
    accepted = source['summary']['automatic_gate'] and human and clean and source.get('execution') == 'live'
    return {'source_results_sha256': source_hash, 'decisions': decisions, 'acceptance': 'accepted' if accepted else 'not_accepted',
            'review_status': 'complete' if complete else 'pending', 'counts': counts,
            'human_review_complete': human, 'semantic_clean': clean,
            'note': '编码助手核对不能冒充外部人工盲审；只读重评分不能替换原始分数。'}


def main(argv=None):
    import sys
    # Windows重定向默认编码可能无法输出工具状态符号，不能因此中断已付费任务。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=SUITE)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('freeze'); p.add_argument('--split', choices=['development', 'holdout'], required=True); p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('run'); p.add_argument('--lock', type=Path, required=True); p.add_argument('--out', type=Path, required=True); p.add_argument('--rounds', type=int, choices=[1, 3], default=3)
    p = sub.add_parser('rescore'); p.add_argument('source', type=Path); p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('review'); p.add_argument('source', type=Path); p.add_argument('decisions', type=Path); p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('template', help='从旧结果只读导出绑定版本的待核对表，不运行或重评分')
    p.add_argument('source', type=Path); p.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command in ('review', 'template'):
        # 先验证，再创建输出目录；输入错误不留下阻碍重试的空目录。
        try:
            source = read(args.source)
            if args.command == 'template':
                suite = load_suite(args.suite)
                lock = source.get('lock') if isinstance(source, dict) else None
                if not isinstance(lock, dict) or lock.get('suite_sha256') != digest(suite):
                    raise ValueError('题集与原始结果的冻结版本不符，请用--suite指定对应题集')
                decisions = review_template(source, suite['cases'])
            else:
                decisions = read(args.decisions)
            result = review(source, decisions)
            out = new_output(args.out)
            if args.command == 'template':
                save(out / 'review-template.json', decisions)
                save(out / 'review-status.json', {k: v for k, v in result.items() if k != 'decisions'})
            else:
                save(out / 'review.json', result)
        except (OSError, ValueError) as exc:
            parser.exit(2, f'复核输入或输出错误：{exc}\n')
        print(json.dumps({k: v for k, v in result.items() if k != 'decisions'}, ensure_ascii=False))
        return 0 if args.command == 'template' or result['acceptance'] == 'accepted' else 1
    suite = load_suite(args.suite)
    if args.command == 'freeze':
        freeze(suite, args.split, args.out); print(args.out); return 0
    out = new_output(args.out)
    if args.command == 'run':
        result = run(suite, read(args.lock), out, args.rounds)
    elif args.command == 'rescore':
        result = rescore(read(args.source), suite, out)
    print(json.dumps(result.get('summary', {k: v for k, v in result.items() if k != 'decisions'}), ensure_ascii=False))
    return 0 if result.get('summary', {}).get('automatic_gate') or result.get('acceptance') == 'accepted' else 1


if __name__ == '__main__':
    raise SystemExit(main())
