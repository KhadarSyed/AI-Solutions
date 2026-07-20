from app.tools.enrichment.pexels import _pick_video


def test_pick_video_prefers_landscape_hd():
    payload = {"videos": [
        {"id": 1, "width": 1920, "height": 1080, "image": "https://p/poster1.jpg",
         "user": {"name": "Jane"},
         "video_files": [
             {"link": "https://v/sd.mp4", "quality": "sd", "width": 640, "height": 360},
             {"link": "https://v/hd.mp4", "quality": "hd", "width": 1920, "height": 1080},
         ]},
    ]}
    out = _pick_video(payload)
    assert out is not None
    assert out["video_url"] == "https://v/hd.mp4"
    assert out["poster"] == "https://p/poster1.jpg"
    assert out["author"] == "Jane"


def test_pick_video_none_when_empty():
    assert _pick_video({"videos": []}) is None
    assert _pick_video({}) is None
