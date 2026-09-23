"""Build vector-only PDF geometry from the trusted eFP SVG (development only).

Requires skia-pathops==0.9.1 and fonttools==4.61.1; no server dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from fontTools.svgLib.path import parse_path
from fontTools.ttLib import TTFont
import pathops


STATIC = Path(__file__).parent / "static" / "efp"


def number(value: float) -> str:
    return f"{value:.5f}".rstrip("0").rstrip(".") if value else "0"


def pdf_path(path: pathops.Path) -> str:
    commands = []
    current = (0.0, 0.0)
    for verb, points in path:
        if verb == pathops.PathVerb.QUAD:
            control, end = points
            points = tuple(tuple(a + (b - a) * 2 / 3 for a, b in zip(anchor, control))
                           for anchor in (current, end)) + (end,)
            verb = pathops.PathVerb.CUBIC
        operation = {
            pathops.PathVerb.MOVE: "m", pathops.PathVerb.LINE: "l",
            pathops.PathVerb.CUBIC: "c", pathops.PathVerb.CLOSE: "h",
        }.get(verb)
        if operation is None:
            raise ValueError(f"Unsupported path verb: {verb}")
        commands.append(" ".join([*(number(n) for point in points for n in point), operation]))
        if points:
            current = points[-1]
    return "\n".join(commands)


class Geometry:
    def __init__(self, root: ET.Element):
        self.by_id = {node.attrib["id"]: node for node in root.iter() if "id" in node.attrib}
        self.cache: dict[ET.Element, pathops.Path] = {}

    def reference(self, value: str) -> ET.Element:
        match = re.fullmatch(r"url\(#([^()]+)\)", value)
        return self.by_id[match[1] if match else value.removeprefix("#")]

    def union(self, nodes) -> pathops.Path:
        paths = [self.shape(node) for node in nodes if node.tag.rsplit("}", 1)[-1] not in {"title", "desc"}]
        result = pathops.Path()
        if paths:
            pathops.union(paths, result.getPen())
        return result

    def shape(self, node: ET.Element) -> pathops.Path:
        if node in self.cache:
            return self.cache[node]
        if node.get("transform"):
            raise ValueError("Template transforms need explicit support before rebuilding PDF assets")
        tag = node.tag.rsplit("}", 1)[-1]
        path = pathops.Path()
        if tag == "path":
            parse_path(node.attrib["d"], path.getPen())
        elif tag == "rect":
            x, y, width, height = (float(node.get(key, "0")) for key in ["x", "y", "width", "height"])
            parse_path(f"M{x} {y}h{width}v{height}h{-width}z", path.getPen())
        elif tag == "use":
            path = self.shape(self.reference(node.attrib["href"]))
        elif tag in {"g", "clipPath"}:
            path = self.union(node)
        else:
            raise ValueError(f"Unsupported template node: {tag}")
        if node.get("clip-path"):
            path = pathops.op(path, self.shape(self.reference(node.attrib["clip-path"])), pathops.PathOp.INTERSECTION)
        if node.get("mask"):
            mask = self.reference(node.attrib["mask"])
            if mask.get("maskUnits") != "userSpaceOnUse" or any(child.get("fill") not in {"white", "black"} for child in mask):
                raise ValueError("Only the template's binary vector masks are supported")
            white = self.union(child for child in mask if child.get("fill") == "white")
            black = self.union(child for child in mask if child.get("fill") == "black")
            path = pathops.op(path, white, pathops.PathOp.INTERSECTION)
            path = pathops.op(path, black, pathops.PathOp.DIFFERENCE)
        self.cache[node] = path
        return path


def build(font_directory: Path, license_file: Path) -> None:
    source = STATIC / "potato-template.svg"
    root = ET.fromstring(source.read_bytes())
    geometry = Geometry(root)
    output = {
        "templateSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "viewBox": [float(n) for n in root.attrib["viewBox"].split()],
        "regions": {node.attrib["data-tissue"]: pdf_path(geometry.shape(node))
                    for node in root.iter() if "data-tissue" in node.attrib},
        "linework": [pdf_path(geometry.shape(node)) for node in geometry.by_id["original-linework-v2"]],
        "strokeWidth": float(geometry.by_id["original-linework-v2"].attrib["stroke-width"]),
        "fonts": [],
    }
    fonts = STATIC / "fonts"
    fonts.mkdir(exist_ok=True)
    for style in ["Regular", "Bold"]:
        filename = f"LiberationSans-{style}.ttf"
        source_font = font_directory / filename
        font = TTFont(source_font)
        units = font["head"].unitsPerEm
        metric = lambda n: round(n * 1000 / units, 5)
        cmap = font.getBestCmap()
        output["fonts"].append({
            "file": "fonts/" + filename,
            "name": font["name"].getDebugName(6),
            "widths": [metric(font["hmtx"][cmap[code]][0]) for code in range(32, 127)],
            "bbox": [metric(getattr(font["head"], key)) for key in ["xMin", "yMin", "xMax", "yMax"]],
            "ascent": metric(font["hhea"].ascent), "descent": metric(font["hhea"].descent),
            "capHeight": metric(font["OS/2"].sCapHeight),
            "sha256": hashlib.sha256(source_font.read_bytes()).hexdigest(),
        })
        shutil.copyfile(source_font, fonts / filename)
    shutil.copyfile(license_file, fonts / "LICENSE.txt")
    (STATIC / "pdf-geometry.json").write_text(json.dumps(output, separators=(",", ":")) + "\n")
    print(f"Built {len(output['regions'])} vector regions, {len(output['linework'])} outline paths, and two embedded fonts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-directory", type=Path, default=Path("/usr/share/fonts/truetype/liberation"))
    parser.add_argument("--font-license", type=Path, default=Path("/usr/share/doc/fonts-liberation/copyright"))
    args = parser.parse_args()
    build(args.font_directory, args.font_license)
