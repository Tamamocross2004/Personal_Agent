"""Bounded subprocess protocol; fixed parser, no generated code."""
import json,sys
from pathlib import Path
from .parsing import extract

if __name__=='__main__':
    try:
        result={'ok':True,'result':extract(Path(sys.argv[1]).read_bytes(),sys.argv[2])}
    except ImportError:
        result={'ok':False,'status':'missing_dependency','message':'解析依赖缺失，请安装项目folder依赖'}
    except UnicodeDecodeError:
        result={'ok':False,'status':'unsupported_encoding','message':'文本或CSV须为UTF-8编码（支持BOM）'}
    except Exception as exc:
        result={'ok':False,'status':'parse_failed','message':str(exc)[:300] if isinstance(exc,ValueError) else '文件损坏、加密或结构无法解析：'+type(exc).__name__}
    print(json.dumps(result,ensure_ascii=False))
