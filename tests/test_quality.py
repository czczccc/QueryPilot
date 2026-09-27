"""质量识别单元测试（纯函数）。"""

from app.services.quality import parse_quality

GB = 1024**3


def _f(name, size=0, is_dir=False):
    return {"file_name": name, "size": size, "dir": is_dir}


def test_resolution_from_filename_variants():
    cases = {
        "流浪地球2.2023.2160p.WEB-DL.H265.HDR.mkv": "2160p",
        "流浪地球2 4K 杜比视界.mp4": "2160p",
        "漫长的季节.BD1080P.国语中字.mkv": "1080p",
        "某剧.E01.HD720P.mp4": "720p",
        "老电影.DVDRip.avi": "SD",
    }
    for name, expected in cases.items():
        assert parse_quality([_f(name, GB)]).resolution == expected, name


def test_resolution_not_confused_by_numbers():
    # 年份、集数、"14k" 这类不应被识别为分辨率
    info = parse_quality([_f("电影.2014k.mkv", 0), _f("第1080集.txt")])
    assert info.resolution is None


def test_resolution_guessed_from_single_file_size():
    info = parse_quality([_f("电影.mkv", 20 * GB)])
    assert info.resolution == "2160p"
    assert info.resolution_guessed is True
    assert parse_quality([_f("电影.mkv", 4 * GB)]).resolution == "1080p"
    assert parse_quality([_f("电影.mkv", 300 * 1024**2)]).resolution == "SD"


def test_size_guess_skipped_for_multiple_videos():
    info = parse_quality([_f("E01.mkv", 20 * GB), _f("E02.mkv", 20 * GB)])
    assert info.resolution is None
    assert info.video_count == 2
    assert info.size_bytes == 40 * GB


def test_title_contributes_to_detection():
    info = parse_quality([_f("E01.mkv", GB)], title="漫长的季节 4K 全集")
    assert info.resolution == "2160p"


def test_hdr_codec_source_and_subtitles():
    info = parse_quality([
        _f("Movie.2160p.BluRay.REMUX.HEVC.DV.mkv", 60 * GB),
        _f("Movie.chs.srt", 100),
    ])
    assert info.hdr is True
    assert info.codec == "H.265"
    assert info.source == "REMUX"
    assert info.has_subtitle is True
    assert info.video_count == 1


def test_low_quality_detected_but_not_ts_extension():
    assert parse_quality([_f("新片.HD-TC.mp4", GB)]).low_quality is True
    assert parse_quality([_f("新片 枪版.mp4", GB)]).low_quality is True
    # .ts 是正常的视频扩展名，不代表枪版
    info = parse_quality([_f("演唱会.1080p.ts", 5 * GB)])
    assert info.low_quality is False
    assert info.video_count == 1


def test_directories_counted_only_for_names():
    info = parse_quality([_f("某剧 4K 全30集", is_dir=True), _f("E01.mp4", GB), _f("E02.mp4", GB)])
    assert info.resolution == "2160p"
    assert info.video_count == 2


def test_score_orders_quality_sensibly():
    uhd = parse_quality([_f("M.2160p.BluRay.HDR.mkv", 30 * GB)]).score
    fhd = parse_quality([_f("M.1080p.WEB-DL.mkv", 4 * GB)]).score
    cam = parse_quality([_f("M.1080p.HDCAM.mkv", 2 * GB)]).score
    unknown = parse_quality([]).score
    assert uhd > fhd > unknown > cam


def test_empty_files():
    info = parse_quality([])
    assert info.resolution is None
    assert info.video_count == 0
    assert info.size_bytes is None
