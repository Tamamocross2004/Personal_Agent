"""有界解析和按段落/句界切片；偏移始终对应保存的提取文本。"""
import hashlib
from pathlib import Path
import re

MAX_BYTES=20_000_000
MAX_TEXT=1_000_000


def sha(value):
    return hashlib.sha256(value if isinstance(value,bytes) else value.encode('utf-8')).hexdigest()


def parse(data: bytes, name: str):
    if not data or len(data)>MAX_BYTES: raise ValueError('文件为空或超过20MB')
    if Path(name).name!=name or '\\' in name or '/' in name or len(name)>180:
        raise ValueError('文件名无效')
    suffix=Path(name).suffix.lower()
    if suffix in ('.txt','.md'):
        try: pages=[data.decode('utf-8-sig')]
        except UnicodeDecodeError: raise ValueError('文本文件须为UTF-8编码') from None
    elif suffix=='.pdf':
        try:
            import io
            from pypdf import PdfReader
            reader=PdfReader(io.BytesIO(data),strict=False)
            if reader.is_encrypted: raise ValueError('不支持加密PDF')
            if len(reader.pages)>500: raise ValueError('PDF超过500页')
            pages=[page.extract_text() or '' for page in reader.pages]
        except ImportError: raise ValueError('PDF依赖未安装，请安装本项目rag依赖') from None
        except ValueError: raise
        except Exception: raise ValueError('PDF解析失败，未修改知识库') from None
    else: raise ValueError('仅支持PDF、TXT、Markdown')
    if sum(len(p) for p in pages)>MAX_TEXT: raise ValueError('提取文本超过100万字符')
    if not any(p.strip() for p in pages): raise ValueError('没有可提取文字；扫描PDF首版不支持OCR')
    return [{'page':i+1,'text':text} for i,text in enumerate(pages)]


def split(pages, size=700, overlap=120):
    if not 0<=overlap<size: raise ValueError('重叠长度须小于切片长度')
    result=[]
    for page in pages:
        text=page['text']; start=0; ordinal=0
        while start<len(text):
            end=min(start+size,len(text))
            if end<len(text):
                breaks=[m.end()+start for m in re.finditer(r'\n\n|\n|[。！？.!?]\s*',text[start:end])
                        if m.end()>size//2]
                if breaks: end=breaks[-1]
            if text[start:end].strip():
                result.append({'page':page['page'],'ordinal':ordinal,'start':start,'end':end,
                               'text':text[start:end],'text_sha256':sha(text[start:end])})
                ordinal+=1
            if end==len(text): break
            start=max(start+1,end-overlap)
    return result
