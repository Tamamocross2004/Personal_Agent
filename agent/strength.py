"""Verified provider capabilities. Unknown models/endpoints never get guessed parameters."""
from typing import Literal
from urllib.parse import urlsplit

ModelStrength=Literal['default','low','medium','high']
EFFORTS={'low':'low','medium':'high','high':'max'}
SOURCE='https://api-docs.deepseek.com/guides/thinking_mode/'


def capability(provider,model,base_url):
    endpoint=urlsplit(base_url)
    supported=(provider=='deepseek' and model in ('deepseek-flash','deepseek-v4-pro')
               and endpoint.scheme=='https' and endpoint.netloc.lower()=='api.deepseek.com'
               and endpoint.path.rstrip('/') in ('','/v1','/beta') and not endpoint.query and not endpoint.fragment)
    return {'provider':provider,'model':model,'supported':supported,
            'levels':['default',*EFFORTS] if supported else ['default'],
            'effort_mapping':EFFORTS if supported else {},'source':SOURCE if supported else None,
            'verified_at':'2026-10-22' if supported else None,
            'message':'低/中/高分别对应官方low/high/max；默认保持现有配置。思考消耗计入原输出和任务预算，可能增加耗时或导致正文截断，不保证更准确。'
                      if supported else '当前厂商、模型或接口地址尚未验证强度参数，仅支持默认；不会发送猜测的参数。'}


def resolve_strength(level,provider,model,base_url):
    if level not in ('default',*EFFORTS):raise ValueError('模型强度须为default、low、medium或high')
    if level=='default':return None
    if not capability(provider,model,base_url)['supported']:
        raise ValueError('当前模型或接口尚未验证思考强度支持，请选择默认')
    return EFFORTS[level]
