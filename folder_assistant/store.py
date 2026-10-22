from __future__ import annotations

from contextlib import contextmanager
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import uuid

SUPPORTED={'.txt','.md','.pdf','.csv','.docx','.xlsx'}
EXCLUDED={'.git','.venv','venv','node_modules','__pycache__','.ssh','.aws','.azure','.gnupg'}
PATTERNS=('.env*','*.pem','*.key','*.pfx','*.p12','id_rsa*','id_ed25519*','credentials*')
MAX_ENTRIES=2000
MAX_TOTAL_BYTES=200_000_000
MAX_FILE_BYTES=20_000_000
MAX_DEPTH=12


def identity(s):
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def blocked(s):
    # REPARSE_POINT plus cloud OFFLINE/RECALL flags; do not hydrate placeholders.
    return stat.S_ISLNK(s.st_mode) or bool(getattr(s,'st_file_attributes',0)&(0x400|0x1000|0x40000|0x400000))


def local_path(path):
    raw=str(path)
    if raw.startswith(('\\\\','//')):raise ValueError('首版只支持本地磁盘目录，不支持网络或设备路径')
    p=Path(path).absolute()
    for node in [*reversed(p.parents),p]:
        if blocked(node.lstat()):raise ValueError('路径含链接、重解析点或云端占位文件')
    return p.resolve(strict=True)


def private_name(name):
    low=name.lower()
    return low in EXCLUDED or any(fnmatch.fnmatchcase(low,p) for p in PATTERNS)


class FolderStore:
    def __init__(self,root:Path,*,excluded_roots=()):
        self.root=root.resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.excluded_roots=(self.root,*(Path(p).resolve() for p in excluded_roots))
        self.lock=threading.RLock()
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,identity TEXT NOT NULL,active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY,folder_id TEXT NOT NULL,payload TEXT NOT NULL);''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.root/'catalog.sqlite3',timeout=15)
        db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def folders(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT id,path,active FROM folders ORDER BY rowid')]

    def grant(self,path):
        with self.lock:
            try:p=local_path(path);s=p.stat()
            except OSError:raise ValueError('目录不可访问') from None
            if not stat.S_ISDIR(s.st_mode) or p==Path(p.anchor):raise ValueError('请选择具体资料子目录')
            if any(private_name(x) for x in p.parts) or any(p.is_relative_to(x) for x in self.excluded_roots):
                raise ValueError('该目录属于排除范围')
            with self.connect() as db:
                old=db.execute('SELECT id FROM folders WHERE path=?',(str(p),)).fetchone()
                key=old['id'] if old else uuid.uuid4().hex
                db.execute('INSERT INTO folders VALUES(?,?,?,1) ON CONFLICT(path) DO UPDATE SET identity=excluded.identity,active=1',
                    (key,str(p),json.dumps(identity(s)[:2])))
            return {'id':key,'path':str(p),'active':1}

    def revoke(self,key):
        with self.lock,self.connect() as db:
            if not db.execute('SELECT id FROM folders WHERE id=?',(key,)).fetchone():raise KeyError('资料目录不存在')
            db.execute('UPDATE folders SET active=0 WHERE id=?',(key,))
        return {'id':key,'active':0,'notice':'已取消授权；已有扫描清单和任务快照仍保留，不代表彻底删除历史数据。'}

    def authorized(self,key):
        with self.connect() as db:row=db.execute('SELECT * FROM folders WHERE id=?',(key,)).fetchone()
        if not row or not row['active']:raise ValueError('资料目录未授权或授权已取消')
        try:p=local_path(row['path'])
        except OSError:raise ValueError('资料目录已移动或不可访问') from None
        if identity(p.stat())[:2]!=json.loads(row['identity']):raise ValueError('资料根目录已被替换，请重新选择')
        return p

    def policy(self):
        return {'extensions':sorted(SUPPORTED),'excluded_names':sorted(EXCLUDED),'excluded_patterns':list(PATTERNS),
            'max_entries':MAX_ENTRIES,'max_total_bytes':MAX_TOTAL_BYTES,'max_file_bytes':MAX_FILE_BYTES,
            'max_depth':MAX_DEPTH,'cloud_calls':False,'mode':'local_read_only',
            'notice':'本阶段只浏览及本地解析；不调用模型、嵌入或重排，不注册代码执行工具。'}

    def scan(self,key,*,check=lambda:None,progress=lambda **v:None):
        with self.lock:
            root=self.authorized(key);rows=[];total=0;limits=[]
            def walk(parent,depth):
                nonlocal total
                try:
                    checked=local_path(parent)
                    if not checked.is_relative_to(root):raise ValueError('扫描路径已越出授权范围')
                    self.authorized(key)
                    with os.scandir(parent) as entries:
                        for entry in entries:
                            check()
                            if len(rows)%25==0:progress(phase='scan',completed=len(rows),total=None,current=entry.name)
                            if len(rows)>=MAX_ENTRIES:
                                limits.append('达到条目上限，未继续扫描剩余目录');return
                            relative=(parent/entry.name).relative_to(root).as_posix()
                            row={'id':hashlib.sha256(relative.encode()).hexdigest(),'relative_path':relative,
                                 'kind':'unknown','status':'ready','size':None,'modified_ns':None,'reason':''}
                            rows.append(row)
                            if private_name(entry.name):row.update(status='excluded',reason='排除目录或敏感文件名');continue
                            try:
                                s=Path(entry.path).lstat()  # Windows DirEntry cached stat omits file identity/link count.
                                if blocked(s):row.update(status='excluded',reason='链接、重解析点或不可用云端占位');continue
                                p=Path(entry.path)
                                if any(p.is_relative_to(x) for x in self.excluded_roots):
                                    row.update(status='excluded',reason='应用索引或任务产物目录');continue
                                if stat.S_ISDIR(s.st_mode):
                                    row['kind']='directory'
                                    if depth>=MAX_DEPTH:row.update(status='excluded',reason='超过最大目录深度');limits.append('达到目录深度上限')
                                    else:walk(p,depth+1)
                                    if len(rows)>=MAX_ENTRIES or total>MAX_TOTAL_BYTES:return
                                    continue
                                row.update(kind='file',size=s.st_size,modified_ns=s.st_mtime_ns,identity=identity(s),suffix=p.suffix.lower())
                                if not stat.S_ISREG(s.st_mode) or s.st_nlink>1:row.update(status='excluded',reason='非普通文件或多硬链接');continue
                                total+=s.st_size
                                if total>MAX_TOTAL_BYTES:
                                    row.update(status='over_limit',reason='目录总字节超过上限');limits.append('达到总大小上限，未扫描其余内容');return
                                if s.st_size>MAX_FILE_BYTES:row.update(status='over_limit',reason='单文件超过20MB')
                                elif p.suffix.lower() not in SUPPORTED:row.update(status='unsupported',reason='首版不支持该格式')
                            except OSError:row.update(status='unreadable',reason='无法读取文件属性或目录')
                except OSError:limits.append('部分目录无法枚举：'+parent.relative_to(root).as_posix())
            walk(root,0)
            check()
            self.authorized(key)
            result={'id':uuid.uuid4().hex,'folder_id':key,'entries':rows,'total_bytes':total,
                    'complete':not limits,'limits':list(dict.fromkeys(limits)), 'policy':self.policy()}
            with self.connect() as db:db.execute('INSERT INTO scans VALUES(?,?,?)',(result['id'],key,json.dumps(result,ensure_ascii=False)))
            progress(phase='scan',completed=len(rows),total=len(rows),current='扫描清单已保存')
            return result

    def manifest(self,key,scan_id=None):
        self.authorized(key)
        with self.connect() as db:
            row=db.execute('SELECT payload FROM scans WHERE folder_id=?'+(' AND id=?' if scan_id else '')+' ORDER BY rowid DESC LIMIT 1',
                (key,scan_id) if scan_id else (key,)).fetchone()
        if not row:raise KeyError('尚无扫描清单，请先扫描')
        return json.loads(row['payload'])

    def preview(self,key,scan_id,file_id,*,offset=0,limit=50):
        import subprocess,sys,tempfile
        from .parsing import VERSION
        if offset<0 or not 1<=limit<=100:raise ValueError('预览分页无效')
        with self.lock:
            row,data=self.read(key,scan_id,file_id)
            digest=hashlib.sha256(data).hexdigest()
            cache=self.root/'parsed';cache.mkdir(exist_ok=True)
            path=cache/(digest+'-'+str(VERSION)+row['suffix']+'.json')
            hit=False;result=None
            if path.is_file() and not path.is_symlink() and path.stat().st_size<=20_000_000:
                try:
                    candidate=json.loads(path.read_text('utf-8'))
                    if candidate.get('file_sha256')==digest and candidate.get('parser_version')==VERSION:
                        result=candidate;hit=True
                except (ValueError,UnicodeError):pass
            if result is None:
                with tempfile.TemporaryDirectory(prefix='parse-',dir=self.root) as temporary:
                    snapshot=Path(temporary)/'source.bin';snapshot.write_bytes(data)
                    env={k:v for k,v in os.environ.items() if k.lower() in {'systemroot','windir','path','temp','tmp','lang'}}
                    try:
                        process=subprocess.run([sys.executable,'-X','utf8','-m','folder_assistant.parse_worker',str(snapshot),row['suffix']],
                            cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,timeout=20)
                    except subprocess.TimeoutExpired:return {'status':'parse_timeout','message':'解析超过20秒，已停止本次解析，源文件未修改'}
                    try:payload=json.loads(process.stdout)
                    except (ValueError,UnicodeError):return {'status':'parse_failed','message':'解析子进程未正常返回，未使用不完整结果'}
                    if not payload.get('ok'):return {k:v for k,v in payload.items() if k!='ok'}
                    result=payload['result']
                    if result['file_sha256']!=digest:raise ValueError('解析输入版本不一致')
                    text=json.dumps(result,ensure_ascii=False)
                    if len(text.encode())>20_000_000:raise ValueError('解析记录超过20MB上限')
                    temp=cache/(uuid.uuid4().hex+'.tmp');temp.write_text(text,encoding='utf-8');temp.replace(path)
            # Do not serve a stale cached version after revocation, source replacement or mid-parse edits.
            _,current=self.read(key,scan_id,file_id)
            if hashlib.sha256(current).hexdigest()!=digest:raise ValueError('解析期间源文件变化，请重新扫描')
            return {'status':'ready','folder_id':key,'scan_id':scan_id,'file_id':file_id,'relative_path':row['relative_path'],
                    'file_sha256':digest,'parser_version':VERSION,'cache_hit':hit,'offset':offset,'limit':limit,
                    'total_blocks':len(result['blocks']),'blocks':result['blocks'][offset:offset+limit],
                    'warnings':result['warnings'],'cloud_calls':False}

    def read(self,key,scan_id,file_id):
        """Read by manifest identity only; never accept an arbitrary model path."""
        with self.lock:
            root=self.authorized(key);manifest=self.manifest(key,scan_id)
            row=next((x for x in manifest['entries'] if x['id']==file_id),None)
            if not row or row['kind']!='file' or row['status']!='ready':raise ValueError('文件不在可读取清单中')
            p=root/row['relative_path']
            try:
                real=local_path(p)
                if not real.is_relative_to(root) or any(real.is_relative_to(x) for x in self.excluded_roots):raise ValueError('文件越出授权范围')
                with real.open('rb') as stream:
                    s=os.fstat(stream.fileno())
                    if not stat.S_ISREG(s.st_mode) or s.st_nlink>1 or blocked(s) or identity(s)!=row['identity']:
                        raise ValueError('文件已变化，请重新扫描')
                    # Validate actual handle location as well as path resolution (Windows rename/link races).
                    if os.name=='nt':
                        import ctypes,msvcrt
                        fn=ctypes.windll.kernel32.GetFinalPathNameByHandleW
                        fn.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32];fn.restype=ctypes.c_uint32
                        buf=ctypes.create_unicode_buffer(32768)
                        n=fn(msvcrt.get_osfhandle(stream.fileno()),buf,len(buf),0)
                        if not n or n>=len(buf):raise ValueError('无法核对实际读取路径')
                        resolved=buf.value
                        if resolved.startswith('\\\\?\\UNC\\'):raise ValueError('拒绝网络文件句柄')
                        if resolved.startswith('\\\\?\\'):resolved=resolved[4:]
                        if Path(resolved)!=real:raise ValueError('文件句柄路径发生变化')
                    elif Path('/proc/self/fd').exists():
                        if Path(os.readlink('/proc/self/fd/'+str(stream.fileno())))!=real:raise ValueError('文件句柄路径发生变化')
                    data=stream.read(MAX_FILE_BYTES+1)
                    if len(data)>MAX_FILE_BYTES or identity(os.fstat(stream.fileno()))!=identity(s):raise ValueError('读取时文件变化或超限')
                if local_path(p)!=real or identity(p.stat())!=identity(s):raise ValueError('读取后文件已变化，请重新扫描')
                self.authorized(key)
                return row,data
            except OSError:raise ValueError('文件已移动、删除或不可读取，请重新扫描') from None
