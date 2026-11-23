"""Keep the standalone uploader independent of the site's imports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "uploader"))
