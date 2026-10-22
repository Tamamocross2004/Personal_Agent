"""Scope, accounting and cancellation guarantees, without cloud requests."""
from desktop_app.usage import PricingBook,PriceProfile
from folder_assistant.usage import summarize_folder
from test_folder_index import library,sync
from test_folder_answers import service,ask,finished


def test_usage_deduplicates_index_callbacks_and_keeps_unpriced(library,tmp_path):
    _,folders,fid,index,embedding,_=library
    jobs,chat=service(library)
    book=PricingBook(tmp_path/'prices.json')
    book.save('deepseek','deepseek-flash',PriceProfile(provider='deepseek',model='deepseek-flash',input_per_million=2,cached_per_million=2,output_per_million=8))
    jobs.pricing=book
    built=finished(jobs,jobs.start(fid,'sync',{'scan_id':folders.scan(fid)['id']}))
    assert built['progress']['phase']=='done'
    value=finished(jobs,ask(library,jobs))
    before=(len(embedding.calls),len(chat.calls))
    folders.revoke(fid)
    stats=summarize_folder(jobs,fid,book)
    assert stats['calls']==len(index.store(fid).events())+1
    assert stats['by_operation']['llm']['calls']==1
    assert stats['by_operation']['embedding']['calls']==len(embedding.calls)
    assert stats['by_operation']['llm']['unpriced_calls']==0
    assert stats['estimated_cost'] is None and float(stats['known_cost'])>0
    assert stats['by_operation']['embedding']['unpriced_calls']>0
    assert stats==summarize_folder(jobs,fid,book) and before==(len(embedding.calls),len(chat.calls))


def test_failed_unknown_usage_not_zero(library,tmp_path):
    sync(library);jobs,chat=service(library);chat.fail=True
    finished(jobs,ask(library,jobs))
    result=summarize_folder(jobs,library[2],PricingBook(tmp_path/'prices.json'))
    assert result['by_operation']['llm']['unknown_usage_calls']==1
    assert result['estimated_cost'] is None


def test_stopped_scan_never_replaces_saved_manifest(library):
    _,folders,fid,_,_,_=library
    first=folders.scan(fid)
    class Cancelled(ValueError):pass
    def stop():raise Cancelled('stop')
    import pytest
    with pytest.raises(Cancelled):folders.scan(fid,check=stop)
    assert folders.manifest(fid)['id']==first['id']


def test_scan_job_reports_saved_manifest(library):
    jobs,_=service(library)
    value=finished(jobs,jobs.start(library[2],'scan',{}))
    assert value['status']=='completed'
    assert value['progress']['completed']==len(value['result']['entries'])
    assert library[1].manifest(library[2])['id']==value['result']['id']
