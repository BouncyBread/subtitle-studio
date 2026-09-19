from app.progress import parse_progress, read_log, snapshot, log_snapshot


def test_actual_frames_and_average_eta():
    line=' 24%|██▍       | 125042/518908 [03:20<10:12, 643.07frames/s]'
    value=parse_progress([line])
    assert value['percent']==24.1
    assert value['processed_seconds']==1250.42
    assert value['elapsed_seconds']==200
    assert value['eta_seconds']==630


def test_download_bars_and_incomplete_updates_are_not_audio():
    assert parse_progress(['Fetching 4 files: 50%|████| 2/4 [00:00<00:00, 17.23it/s]']) is None
    assert parse_progress(['50%|██| 3000/6000 [00:']) is None
    value=parse_progress(['0%| | 0/6000 [00:00<?, ?frames/s]'])
    assert value['percent']==0 and value['eta_seconds'] is None


def test_hour_duration_and_finished():
    value=parse_progress(['100%|████| 720000/720000 [1:02:03<00:00, 193.39frames/s]'])
    assert value['elapsed_seconds']==3723
    assert value['eta_seconds']==0


def test_bounded_log_and_terminal_snapshot(tmp_path):
    path=tmp_path/'worker.log'
    path.write_text('old data\r'*20000+'50%|██| 3000/6000 [00:10<00:10, 300frames/s]\r')
    lines=read_log(path)
    assert len('\n'.join(lines).encode())<=65536
    assert snapshot(tmp_path,'transcribing')['eta_seconds']==10
    assert snapshot(tmp_path,'cancelled')['eta_seconds'] is None
    assert len(log_snapshot(tmp_path)['lines'])<=80
    assert log_snapshot(tmp_path)['seconds_since_update'] is not None


def test_missing_log_is_safe(tmp_path):
    assert read_log(tmp_path/'missing')==[]
    assert snapshot(tmp_path,'queued') is None
    assert log_snapshot(tmp_path)=={'lines':[],'seconds_since_update':None}
