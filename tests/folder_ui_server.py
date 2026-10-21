"""Isolated real API for folder desktop checks; never starts research workers."""
import json,os,socket,sys,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from agent.config import Settings
from desktop_app.manager import Manager
from desktop_app.server import create_app
from desktop_app.lifecycle import watch_parent

fixture=Path(sys.argv[1])
parent=int(sys.argv[sys.argv.index('--parent')+1])
threading.Thread(target=watch_parent,args=(parent,),daemon=True).start()
token=os.environ.pop('PERSONAL_AGENT_DESKTOP_TOKEN')
grant=os.environ.pop('PERSONAL_AGENT_FOLDER_GRANT_TOKEN')
manager=Manager(fixture/'state',start=False,settings=Settings(_env_file=None,memory_enabled=False))
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'event':'ready','port':sock.getsockname()[1]}),flush=True)
try:uvicorn.Server(uvicorn.Config(create_app(manager,token,Path(__file__).resolve().parents[1]/'desktop'/'dist',folder_grant_token=grant),log_level='warning',access_log=False)).run(sockets=[sock])
finally:manager.close();sock.close()
