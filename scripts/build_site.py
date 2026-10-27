"""Assemble the GitHub Pages landing page without dependencies or app access."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
import shutil
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "website"
BRAND_ASSETS = {
    "campresso-mark.svg": "campresso-mark.svg",
    "campresso-vertical.svg": "campresso-vertical.svg",
    "campresso-icon.svg": "campresso-icon.svg",
    "campresso-icon.png": "campresso-icon.png",
    "campresso-brand-board.png": "campresso-social.png",
}


class PageReferences(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.references = []

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if identifier := values.get("id"):
            if identifier in self.ids:
                raise ValueError(f"Duplicate HTML identifier: {identifier}")
            self.ids.add(identifier)
        for attribute in ("href", "src"):
            if reference := values.get(attribute):
                self.references.append(reference)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    output = parser.parse_args().output.resolve()
    # A build never overwrites existing files or deletes an existing directory.
    if output.exists():
        raise ValueError(f"Choose a new output folder; {output} already exists.")
    if output.is_relative_to(SOURCE) or output == ROOT:
        raise ValueError("Build output must be separate from the source files.")

    page = PageReferences()
    page.feed((SOURCE / "index.html").read_text(encoding="utf-8"))
    files = {path.relative_to(SOURCE).as_posix() for path in SOURCE.rglob("*") if path.is_file()}
    files.update(f"assets/{name}" for name in BRAND_ASSETS.values())
    for reference in page.references:
        url = urlsplit(reference)
        if url.scheme or url.netloc:
            if url.scheme != "https":
                raise ValueError(f"Expected HTTPS for external reference: {reference}")
            continue
        path = unquote(url.path)
        if path and path not in files:
            raise ValueError(f"Missing landing-page asset: {reference}")
        if url.fragment and not path and unquote(url.fragment) not in page.ids:
            raise ValueError(f"Missing landing-page anchor: {reference}")
    for name in BRAND_ASSETS:
        if not (ROOT / "assets" / "brand" / name).is_file():
            raise ValueError(f"Missing brand asset: {name}")

    shutil.copytree(SOURCE, output)
    (output / "assets").mkdir(exist_ok=True)
    for source, destination in BRAND_ASSETS.items():
        shutil.copyfile(ROOT / "assets" / "brand" / source, output / "assets" / destination)
    (output / ".nojekyll").touch()
    print(f"Landing page built at {output}; local assets and anchors checked.")


if __name__ == "__main__":
    main()
