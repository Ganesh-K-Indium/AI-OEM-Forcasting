import os

# LightGBM and PyTorch each bundle libomp; on macOS loading both in one process aborts without this guard.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
