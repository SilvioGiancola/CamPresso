"""Check source syntax, package references and local documentation links.

Uses only the Python standard library. Does not import the app, run tests,
install dependencies or access Photos.
"""

import ast
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    package = ROOT / "src" / "video_queue"
    internal = {path.stem for path in package.glob("*.py")}
    sources = sorted(
        path
        for directory in ("src", "scripts", "tests")
        for path in (ROOT / directory).rglob("*.py")
    )
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if not node.level and node.module in internal:
                    raise ValueError(f"Use the video_queue package import in {path}:{node.lineno}")
                if node.level == 1 and node.module and path.is_relative_to(package):
                    if node.module.split(".")[0] not in internal:
                        raise ValueError(f"Missing relative module in {path}:{node.lineno}")

    for pattern in project["tool"]["setuptools"]["package-data"]["video_queue"]:
        if not list(package.glob(pattern)):
            raise ValueError(f"Package data does not match a file: {pattern}")
    for target in project["project"]["scripts"].values():
        module, function = target.split(":")
        path = ROOT / "src" / (module.replace(".", "/") + ".py")
        tree = ast.parse(path.read_text())
        if not any(isinstance(node, ast.FunctionDef) and node.name == function for node in tree.body):
            raise ValueError(f"Missing entry point: {target}")

    markdown = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), *(ROOT / "tests").glob("*.md")]
    for path in markdown:
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            url = urlsplit(link)
            if not url.scheme and not url.netloc and url.path:
                if not (path.parent / unquote(url.path)).exists():
                    raise ValueError(f"Broken local link in {path.relative_to(ROOT)}: {link}")

    for path in (ROOT / "Install.command", *sorted((ROOT / "scripts").glob("*.sh"))):
        subprocess.run(["bash", "-n", str(path)], check=True)
    print(f"Repository checks passed: {len(sources)} Python files, shell syntax, package references and local links.")


if __name__ == "__main__":
    main()
