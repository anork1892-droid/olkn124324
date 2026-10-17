"""Set a modest worker count only for a newly installed Windows instance."""

import os
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from bountyos.store import Store


def main():
    data_dir = Path(os.environ.get("BOUNTYOS_DATA_DIR", PROJECT_DIR / "data")).resolve()
    existing = (data_dir / "bountyos.sqlite3").exists()
    store = Store(data_dir)
    if not existing:
        store.update_settings({"max_agents": 1})
        store.log("Windowsinstallation: en arbetsagent, lokal AI och budget 0 kr.")
        print("Configured one worker for a 16 GB computer. Paid API budget: 0 SEK.")
    else:
        print("Existing data and settings preserved.")


if __name__ == "__main__":
    main()
