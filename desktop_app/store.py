"""本地会话索引；研究产物仍以任务目录为准。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, path: Path):
        self.path=path
        path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,title TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,session_id TEXT NOT NULL,question TEXT NOT NULL,
                task_dir TEXT NOT NULL UNIQUE,state TEXT NOT NULL,created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,options TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id,created_at);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def sessions(self, archived=False):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM sessions WHERE archived=? ORDER BY updated_at DESC,rowid DESC',(int(archived),))]

    def session(self, sid):
        with self.connect() as db:
            row=db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        if row is None: raise KeyError('会话不存在')
        return dict(row)

    def create_session(self,title='新研究'):
        sid=uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,0,?,?)',(sid,title[:120],now(),now()))
        return self.session(sid)

    def update_session(self,sid,*,title=None,archived=None):
        old=self.session(sid)
        with self.connect() as db:
            db.execute('UPDATE sessions SET title=?,archived=?,updated_at=? WHERE id=?',
                ((title or old['title'])[:120],old['archived'] if archived is None else int(archived),now(),sid))
        return self.session(sid)

    @staticmethod
    def decode(row):
        value=dict(row)
        value['options']=json.loads(value['options'])
        value['result']=json.loads(value['result'])
        return value

    def runs(self,sid=None):
        with self.connect() as db:
            sql='SELECT * FROM runs'+(' WHERE session_id=?' if sid else '')+' ORDER BY created_at,rowid'
            return [self.decode(r) for r in db.execute(sql,(sid,) if sid else ())]

    def run(self,rid):
        with self.connect() as db: row=db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
        if row is None: raise KeyError('任务不存在')
        return self.decode(row)

    def add_run(self,sid,question,task_dir,options,*,state='queued',result=None):
        self.session(sid)
        rid=uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                (rid,sid,question,str(task_dir),state,now(),now(),json.dumps(options,ensure_ascii=False),json.dumps(result or {},ensure_ascii=False)))
            db.execute('UPDATE sessions SET updated_at=? WHERE id=?',(now(),sid))
        return self.run(rid)

    def set_run(self,rid,state,result=None):
        old=self.run(rid)
        with self.connect() as db:
            db.execute('UPDATE runs SET state=?,updated_at=?,result=? WHERE id=?',
                (state,now(),json.dumps(result if result is not None else old['result'],ensure_ascii=False),rid))
            db.execute('UPDATE sessions SET updated_at=? WHERE id=?',(now(),old['session_id']))
        return self.run(rid)
