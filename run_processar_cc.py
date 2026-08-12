# run_processar_cc.py
import sys
from pathlib import Path

# Garante que a pasta do projeto está no path (caso não esteja)
base = Path(__file__).resolve().parent
if str(base) not in sys.path:
    sys.path.insert(0, str(base))

from processar_cc.main import main

if __name__ == "__main__":
    main()