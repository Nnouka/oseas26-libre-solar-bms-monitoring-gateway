import sys
from pathlib import Path

# Make `import outbox` work when pytest is started from the service folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
