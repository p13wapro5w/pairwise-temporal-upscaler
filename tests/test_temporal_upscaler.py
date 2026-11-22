import pytest
import render.temporal_upscaler as mod

CASES = [
    ("history_reproject", (1, 2)),
    ("disocclusion_mask", (1, 2)),
    ("jitter_sequence", (8, 2)),
    ("sharpen_clamp", (0.5, 0.2)),
    ("ui_split", (1, 2)),
    ("history_reset", (1, 2)),
    ("depth_normal_edge", (1, 2)),
    ("transparent_upscale", (1, 2)),
    ("latency_comp", (0.5, 1.0)),
    ("quality_transition", (1, 2)),
    ("upscale_filter", (2, 4)),
    ("sample_count", (8, 4)),
    ("motion_scale", (2.0, 1.0)),
    ("feedback_weight", (0.9, 0.5)),
    ("output_guard", (1, 2)),
]

@pytest.mark.parametrize("fn_name,args", CASES)
def test_contract(fn_name, args):
    fn = getattr(mod, fn_name)
    assert fn(*args) is not None
