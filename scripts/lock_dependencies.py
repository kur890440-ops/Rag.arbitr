"""Record the packages actually installed and tested, without editable local paths."""
from importlib.metadata import distributions
from pathlib import Path

packages = sorted(d.metadata["Name"] + "==" + d.version for d in distributions()
                  if d.metadata["Name"] != "rag-arbiter")
Path("requirements.lock").write_text("\n".join(packages) + "\n", encoding="utf-8")
