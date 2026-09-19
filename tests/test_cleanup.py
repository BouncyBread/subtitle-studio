import json
from app.cleanup import collect


def job(root, name, stage='done', uploaded=True):
    folder=root/name;folder.mkdir()
    source=folder/'source.media';source.write_bytes(b'movie')
    (folder/'job.json').write_text(json.dumps({'source':str(source),'uploaded':uploaded}))
    (folder/'progress.json').write_text(json.dumps({'stage':stage}))
    return folder


def test_cleanup_keeps_results_models_originals_and_busy_files(tmp_path):
    completed=job(tmp_path,'finished')
    for name in ('result.json','live.srt','worker.log'):(completed/name).write_text('keep')
    (completed/'audio.wav').write_bytes(b'audio')
    original=job(tmp_path,'original',uploaded=False)
    active=job(tmp_path,'active',stage='transcribing')
    playing=job(tmp_path,'playing')
    busy=lambda folder:folder==playing
    preview=collect(tmp_path,busy)
    assert preview['files']==2 and preview['bytes']==10
    assert (completed/'source.media').exists()
    result=collect(tmp_path,busy,remove=True)
    assert result['files']==2 and result['skipped_jobs']==2
    assert not (completed/'source.media').exists()
    assert not (completed/'audio.wav').exists()
    for folder in (original,active,playing):assert (folder/'source.media').exists()
    for name in ('result.json','live.srt','worker.log'):assert (completed/name).read_text()=='keep'
    assert collect(tmp_path,busy,remove=True)['files']==0


def test_cleanup_never_follows_symlinks_or_arbitrary_metadata(tmp_path):
    root=tmp_path/'jobs';root.mkdir()
    original=tmp_path/'original.mp4';original.write_bytes(b'original')
    folder=job(root,'linked')
    (folder/'source.media').unlink();(folder/'source.media').symlink_to(original)
    (folder/'audio.wav').symlink_to(original)
    (folder/'job.json').write_text(json.dumps({'source':str(original),'uploaded':True}))
    assert collect(root,lambda f:False,remove=True)['files']==0
    assert original.read_bytes()==b'original'


def test_protect_copy_reused_as_source_by_queued_job(tmp_path):
    cached=job(tmp_path,'cached')
    queued=job(tmp_path,'queued',uploaded=False)
    (queued/'job.json').write_text(json.dumps({'source':str(cached/'source.media'),'uploaded':False}))
    (queued/'progress.json').unlink()
    assert collect(tmp_path,lambda f:False,remove=True)['files']==0
    assert (cached/'source.media').exists()
