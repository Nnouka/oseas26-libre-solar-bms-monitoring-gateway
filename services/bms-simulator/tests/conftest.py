import sys
from pathlib import Path

# Make `import physical_battery` work when pytest is started from the service folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
