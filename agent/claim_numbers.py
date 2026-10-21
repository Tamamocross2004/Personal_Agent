"""直接结论的有界词法核对；标识符/定位/事实数值分开，不认证语义蕴含。"""
import json
import re
from decimal import Decimal


_NUMBER = re.compile(r'(?<![\d.])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?')
_IDENTIFIER = re.compile(r'(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*')
_PAGE = re.compile(r'(?:PDF\s*)?第\s*(\d+)(?:\s*[–—\-至]\s*(\d+))?\s*页|\b(?:PDF\s+)?(?:pages?|pp?\.)\s*(\d+)(?:\s*[–\-]\s*(\d+))?', re.I)
_SECTION = re.compile(r'第\s*(\d+(?:\.\d+)*)\s*[章节]|(?<![\d.])(\d+(?:\.\d+)+)\s*节|\b(?:section|chapter)\s+(\d+(?:\.\d+)*)', re.I)


def identifiers(text):
    return [m for m in _IDENTIFIER.finditer(text) if any(c.isdigit() for c in m[0])]


def numbers(text, record_ids=()):
    def bracket(match):
        if match[1].isdigit() or match[1] in record_ids: return ''
        try:
            values = json.loads(match[0], parse_float=Decimal, parse_int=Decimal)
            if isinstance(values, list) and all(isinstance(v, Decimal) for v in values):
                return ' '.join(map(str, values))
        except ValueError: pass
        return match[0]
    text = re.sub(r'\[([^\[\]\n]+)\]', bracket, text)
    # 来源中的显式页码/章节标记也不是金额或数量的数值依据。
    text = _PAGE.sub(' ', text)
    text = _SECTION.sub(' ', text)
    for match in reversed(identifiers(text)):
        text = text[:match.start()] + ' ' * len(match[0]) + text[match.end():]
    values = set()
    for match in _NUMBER.finditer(text):
        value = Decimal(match[0].replace(',', ''))
        if match.start() and text[match.start()-1] == '-' and (
                match.start() == 1 or not text[match.start()-2].isalnum()):
            value = -value
        values.add(value)
    return values


def check_direct(quote, refs, records):
    quote=re.sub(r'\[([^\[\]\n]+)\]',lambda m:' '*len(m[0])
                 if m[1].isdigit() or m[1] in records else m[0],quote)
    sources = [records[r['source_id']] for r in refs]
    # 只用绑定的实际送达记录和文档名称，不扫描未读原文或其他证据记录。
    identity_text = '\n'.join(s['text'] + '\n' + s.get('document_name', '') for s in sources)
    missing_ids = sorted({m[0] for m in identifiers(quote) if not re.search(
        r'(?<![A-Za-z0-9_])' + re.escape(m[0]) + r'(?![A-Za-z0-9_])', identity_text, re.I)})
    if missing_ids:
        raise ValueError('结论中的标识符未在绑定的已读来源或文档名中找到：' + ', '.join(missing_ids)
                         + '；须整词核对型号/版本，不能忽略其中数字；定位检查不认证属性归属。')
    pages = {s.get('page') for s in sources if s['kind'] == 'document'}
    masked = quote
    locations = []
    for match in _PAGE.finditer(quote):
        left, right = (match[1], match[2]) if match[1] else (match[3], match[4])
        first, last = int(left), int(right or left)
        if last < first or last-first > 50 or not set(range(first,last+1)) <= pages:
            raise ValueError('引用页码与绑定文档实际页码不符：' + match[0] + '；仅核对实际页序，不用正文金额冒充页码。')
        locations.append(match.span())
    for match in _SECTION.finditer(quote):
        label = next(v for v in match.groups() if v)
        heading = (r'(?:^|\n)\s*(?:#{1,6}\s+)?(?:第\s*' + re.escape(label) + r'\s*[章节]|'
                   r'(?:(?:Chapter|Section)\s+)?' + re.escape(label) + r'(?!\d|\.\d)(?:\s|[:：]|\.(?=\s)|$))')
        if not any(re.search(heading, s['text'], re.I) for s in sources):
            raise ValueError('引用章节没有绑定已读标题依据：' + match[0] + '；不能从文件名或未读目录补充定位。')
        locations.append(match.span())
    for left, right in sorted(locations, reverse=True):
        masked = masked[:left] + ' ' * (right-left) + masked[right:]
    supplied = set().union(*(numbers(r['excerpt'], records) for r in refs))
    return numbers(masked, records) - supplied
