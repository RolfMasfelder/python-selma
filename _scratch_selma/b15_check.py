import importlib.util
import os

os.environ.pop("SELMA_WEBCHAT_URL", None)
spec = importlib.util.spec_from_file_location("sb_dash", "/home/rolf/workspace/selma/src/selma/dashboard.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert (
    m.resolve_webchat_stream_url(["run", "gateway-url=http://127.0.0.1:9999/webchat/stream/"])
    == "http://127.0.0.1:9999/webchat/stream"
)
assert m.resolve_webchat_stream_url(["run"]) == "http://localhost:8000/webchat/stream"
os.environ["SELMA_WEBCHAT_URL"] = "http://env.test:9999/webchat/stream/"
assert m.resolve_webchat_stream_url(["run"]) == "http://env.test:9999/webchat/stream"
del os.environ["SELMA_WEBCHAT_URL"]
