## Setup
python -m venv .venv

source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

## Structure
- config/      per-site crawler configs
- data/        raw (not tracked) and processed (tracked) data
- src/         extraction and crawler code