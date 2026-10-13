import sys
from pathlib import Path

# Make `import battery` work when pytest is started from the service folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
