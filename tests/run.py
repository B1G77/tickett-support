"""Minimal test runner (pytest is not installed; stdlib only)."""
import importlib, inspect, sys, tempfile, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
fails = total = 0
only = set(sys.argv[1:])   # optional: run only these test modules, e.g. test_router
for f in sorted(Path(__file__).parent.glob("test_*.py")):
    if only and f.stem not in only:
        continue
    mod = importlib.import_module(f.stem)
    for name, fn in sorted(vars(mod).items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        total += 1
        try:
            if "tmp_path" in inspect.signature(fn).parameters:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
        except Exception:
            fails += 1
            print(f"FAIL {f.stem}.{name}\n{traceback.format_exc(limit=2)}")
print(f"{total - fails}/{total} passed")
sys.exit(1 if fails else 0)
