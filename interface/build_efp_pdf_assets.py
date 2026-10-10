"""Build vector-only PDF geometry from the trusted eFP SVG (development only).

Requires skia-pathops==0.9.1 and fonttools==4.61.1; no server dependency.
"""
from __future__ import annotations

import argparse
import hashlib
from itertools import groupby
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from fontTools.misc.transform import Transform
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
        self.cache: dict[tuple[ET.Element, str], pathops.Path] = {}
        self.mask_cache: dict[ET.Element, pathops.Path] = {}

    @staticmethod
    def transform(node: ET.Element) -> Transform:
        """Resolve the affine placement operations used by the trusted template."""
        value = node.get("transform", "")
        result = Transform()
        cursor = 0
        for match in re.finditer(r"(matrix|translate|scale)\s*\(([^()]*)\)", value):
            if value[cursor:match.start()].strip(" ,\t\r\n"):
                raise ValueError(f"Unsupported template transform: {value}")
            arguments = [float(part) for part in re.split(r"[\s,]+", match[2].strip()) if part]
            operation = match[1]
            if operation == "matrix" and len(arguments) == 6:
                result = result.transform(Transform(*arguments))
            elif operation == "translate" and len(arguments) in {1, 2}:
                result = result.translate(arguments[0], arguments[1] if len(arguments) == 2 else 0)
            elif operation == "scale" and len(arguments) in {1, 2}:
                result = result.scale(arguments[0], arguments[-1])
            else:
                raise ValueError(f"Unsupported template transform: {value}")
            cursor = match.end()
        if value[cursor:].strip(" ,\t\r\n"):
            raise ValueError(f"Unsupported template transform: {value}")
        if node.tag.rsplit("}", 1)[-1] == "use":
            result = result.translate(float(node.get("x", "0")), float(node.get("y", "0")))
        return result

    def reference(self, value: str) -> ET.Element:
        match = re.fullmatch(r"url\(#([^()]+)\)", value)
        return self.by_id[match[1] if match else value.removeprefix("#")]

    @staticmethod
    def union_paths(paths) -> pathops.Path:
        # These are separate SVG paint operations, not contours of one shape.
        # Concatenating contours lets opposite winding cancel their overlap and
        # loses each element's fill rule, leaving holes in tissue and mask fills.
        # Normalize each element first so outer contours and holes have a
        # consistent winding before combining them into one nonzero-fill path.
        normalized = [pathops.simplify(path) for path in paths]
        result = pathops.Path()
        if normalized:
            pathops.union(normalized, result.getPen())
        return result

    def union(self, nodes, fill_rule: str = "nonzero") -> pathops.Path:
        return self.union_paths(self.shape(node, fill_rule) for node in nodes
                                if node.tag.rsplit("}", 1)[-1] not in {"title", "desc"})

    def primitive(self, node: ET.Element, fill_rule: str = "nonzero") -> pathops.Path:
        tag = node.tag.rsplit("}", 1)[-1]
        path = pathops.Path()
        fill_rule = node.get("fill-rule", fill_rule)
        if fill_rule not in {"nonzero", "evenodd"}:
            raise ValueError(f"Unsupported template fill rule: {fill_rule}")
        path.fillType = pathops.FillType.EVEN_ODD if fill_rule == "evenodd" else pathops.FillType.WINDING
        if tag == "path":
            parse_path(node.attrib["d"], path.getPen())
        elif tag == "rect":
            x, y, width, height = (float(node.get(key, "0")) for key in ["x", "y", "width", "height"])
            parse_path(f"M{x} {y}h{width}v{height}h{-width}z", path.getPen())
        elif tag == "ellipse":
            cx, cy, rx, ry = (float(node.get(key, "0")) for key in ["cx", "cy", "rx", "ry"])
            # A zero radius disables SVG rendering, including its stroke.
            if rx > 0 and ry > 0:
                parse_path(f"M{cx-rx} {cy}a{rx} {ry} 0 1 0 {2*rx} 0a{rx} {ry} 0 1 0 {-2*rx} 0z", path.getPen())
        else:
            raise ValueError(f"Unsupported template node: {tag}")
        return path

    def clipped(self, node: ET.Element, path: pathops.Path) -> pathops.Path:
        if node.get("clip-path"):
            path = pathops.op(path, self.shape(self.reference(node.attrib["clip-path"])), pathops.PathOp.INTERSECTION)
        if node.get("mask"):
            mask = self.reference(node.attrib["mask"])
            if mask not in self.mask_cache:
                if mask.get("maskUnits") != "userSpaceOnUse" or mask.get("maskContentUnits", "userSpaceOnUse") != "userSpaceOnUse":
                    raise ValueError("Only user-space binary vector masks are supported")
                white = self.painted_area(mask, "white")
                bounds = ET.Element("rect", {key: mask.attrib[key] for key in ("x", "y", "width", "height")})
                self.mask_cache[mask] = pathops.op(white, self.primitive(bounds), pathops.PathOp.INTERSECTION)
            path = pathops.op(path, self.mask_cache[mask], pathops.PathOp.INTERSECTION)
        return path

    def shape(self, node: ET.Element, fill_rule: str = "nonzero") -> pathops.Path:
        fill_rule = node.get("fill-rule", fill_rule)
        key = (node, fill_rule)
        if key in self.cache:
            return self.cache[key]
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "use":
            path = self.shape(self.reference(node.attrib["href"]), fill_rule)
        elif tag in {"g", "clipPath"}:
            path = self.union(node, fill_rule)
        else:
            path = self.primitive(node, fill_rule)
        path = self.clipped(node, path).transform(*self.transform(node))
        self.cache[key] = path
        return path

    def paint_operations(self, node: ET.Element, inherited: dict[str, str] | None = None):
        """Flatten opaque black/white SVG paint in document order, including strokes."""
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"title", "desc"}:
            return
        style = {"fill": "black", "fill-rule": "nonzero", "stroke": "none", "stroke-width": "1", "stroke-miterlimit": "4",
                 "stroke-linecap": "butt", "stroke-linejoin": "miter", **(inherited or {})}
        style.update({key: node.attrib[key] for key in style if key in node.attrib})
        if tag in {"g", "mask"}:
            operations = (operation for child in node for operation in self.paint_operations(child, style))
        elif tag == "use":
            operations = self.paint_operations(self.reference(node.attrib["href"]), style)
        else:
            path = self.primitive(node, style["fill-rule"])
            operations = []
            if style["fill"] != "none":
                operations.append((path, style["fill"]))
            if style["stroke"] != "none" and float(style["stroke-width"]) > 0:
                stroke = pathops.Path(path)
                cap = {"butt": pathops.LineCap.BUTT_CAP, "round": pathops.LineCap.ROUND_CAP, "square": pathops.LineCap.SQUARE_CAP}
                join = {"miter": pathops.LineJoin.MITER_JOIN, "round": pathops.LineJoin.ROUND_JOIN, "bevel": pathops.LineJoin.BEVEL_JOIN}
                stroke.stroke(float(style["stroke-width"]), cap[style["stroke-linecap"]],
                              join[style["stroke-linejoin"]], float(style["stroke-miterlimit"]))
                # Skia creates conics for round stroke segments; PDF and the
                # boolean path normalizer consume polynomial curves.
                stroke.convertConicsToQuads(0.001)
                operations.append((stroke, style["stroke"]))
        transform = self.transform(node)
        for path, colour in operations:
            yield self.clipped(node, path).transform(*transform), colour

    def painted_area(self, node: ET.Element, colour: str) -> pathops.Path:
        result = pathops.Path()
        colours = {"white": "white", "#ffffff": "white", "black": "black", "#000000": "black"}
        def normalized(operation):
            paint = operation[1]
            normalized = colours.get(paint.lower())
            if normalized is None:
                raise ValueError(f"Only opaque black/white template artwork is supported: {paint}")
            return normalized

        # Adjacent paint of one colour commutes. Batch it to avoid repeatedly
        # resolving the complete detailed flower outline for every tiny stroke.
        for paint, operations in groupby(self.paint_operations(node), normalized):
            path = self.union_paths(item[0] for item in operations)
            operation = pathops.PathOp.UNION if paint == colour else pathops.PathOp.DIFFERENCE
            result = pathops.op(result, path, operation)
        return result


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
        "filledLinework": [pdf_path(geometry.painted_area(geometry.by_id["flower-detail-linework-v9"], "black"))],
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
