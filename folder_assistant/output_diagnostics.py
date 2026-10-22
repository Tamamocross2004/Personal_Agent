"""Describe saved response mechanics, without repairing or regrading its content."""


def diagnose(finish_reason,*,issues=None,checked=False,usage_known=True):
    issues=issues or []
    if finish_reason=='length':
        code='output_limit';message='服务报告输出达到上限，回答可能被截断；即使JSON完整也不计作该批完成。'
        suggestion='可显式拆小未完成批次，或核对预算后为新请求设置输出上限；会另行计费。'
    elif finish_reason!='stop':
        code='unfinished_response';message='服务未报告正常结束，不能把本次响应当成完整交付。'
        suggestion='先核对结束原因与服务状态；原响应和费用保留，不自动重试。'
    elif any(i['code']=='invalid_schema' for i in issues):
        code='invalid_structure';message='服务正常结束，但响应未满足JSON/字段类型约定。'
        suggestion='核对原始响应与约定；增加输出上限不保证能解决格式错误。'
    elif issues:
        code='contract_violation';message='服务正常结束，但引用、原句或字段条件校验未通过。'
        suggestion='根据具体校验问题核对资料与原始响应；不自动补值、改引文或放宽规则。'
    elif checked:
        code='structure_checked';message='结构和依据原句检查已完成，未认证事实、完整抽取或语义正确性。'
        suggestion='核对内容与范围；这不是正式审稿通过。'
    else:
        code='not_checked';message='原始响应已保存，尚未完成结构与依据检查。'
        suggestion='先查看任务状态；用量未知或已停止时不自动继续调用。'
    return {'version':1,'code':code,'finish_reason':finish_reason,'message':message,'suggestion':suggestion,
            'validation':'invalid' if issues else ('checked' if checked else 'not_run'),
            'issues':issues,'usage_status':'known' if usage_known else 'unknown',
            'semantic_status':'not_certified'}
