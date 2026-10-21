"""运行真实desktop worker/research/tools；模型用离线夹具替代。"""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_acceptance_fixture import OfflineModel,settings
import desktop_app.worker as worker
import main
from desktop_rag_fixture import inject_clients
inject_clients()

root=Path(sys.argv[1])
(root/'.desktop-test-worker.json').write_text(json.dumps({'pid':__import__('os').getpid()}),encoding='utf-8')
request=json.loads((root/'.desktop-request.json').read_text(encoding='utf-8'))
configured=settings(root.parent)
worker.Settings=lambda:configured
main.Settings=lambda **kw:type(configured)(_env_file=None,deepseek_api_key='offline-stage14-test-key',**kw)
worker.LLMClient=lambda config:OfflineModel(config,request['question'],root)
# resume_research直接创建LLMClient，仍使用原始恢复逻辑和预算。
main.LLMClient=worker.LLMClient
worker.main()
