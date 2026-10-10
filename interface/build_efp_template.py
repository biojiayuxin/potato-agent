"""Compose the supplied plant and flower drawings into the trusted eFP template.

Run this before build_efp_pdf_assets.py when either source drawing changes.
Only Python's standard library is required; the source path geometry is retained.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


STATIC = Path(__file__).parent / "static" / "efp"
NS = "http://www.w3.org/2000/svg"
PLACEMENT = "translate(550 35) scale(0.7)"
ET.register_namespace("", NS)


def tag(name: str) -> str:
    return f"{{{NS}}}{name}"


def namespace_ids(root: ET.Element) -> None:
    ids = {node.attrib["id"]: "detail-" + node.attrib["id"]
           for node in root.iter() if "id" in node.attrib}
    for node in root.iter():
        for key, value in list(node.attrib.items()):
            if key == "id":
                value = ids[value]
            elif key.rsplit("}", 1)[-1] == "href" and value.startswith("#"):
                value = "#" + ids[value[1:]]
            elif key in {"aria-labelledby", "aria-describedby"}:
                value = " ".join(ids[ref] for ref in value.split())
            else:
                value = re.sub(r"url\(#([^()]+)\)", lambda m: f"url(#{ids[m[1]]})", value)
            node.set(key, value)


def compose(plant: ET.Element, detail: ET.Element) -> ET.Element:
    root, detail = copy.deepcopy(plant), copy.deepcopy(detail)
    namespace_ids(detail)
    root.set("viewBox", "0 0 1120 1089.4")
    root.set("width", "1120")
    root.find(tag("title")).text = "Potato tissue template v9: plant and flower details"
    root.find(tag("desc")).text = (
        "17 independently colored tissues. Flower details at upper right share "
        "perianth and anther values with the plant; carpel is a separate tissue."
    )
    root.find(tag("defs")).extend(detail.find(tag("defs")))
    regions = {node.attrib["data-tissue"]: node for node in root.iter() if "data-tissue" in node.attrib}
    detail_regions = [node for node in detail if "data-tissue" in node.attrib]
    if {node.attrib["data-tissue"] for node in detail_regions} != {"perianth", "anther", "carpel"}:
        raise ValueError("Flower source must define perianth, anther and carpel")

    # The same public group owns all occurrences of an organ, including inset shapes.
    for source in detail_regions:
        tissue = source.attrib.pop("data-tissue")
        source.attrib.pop("fill", None)
        source.attrib.pop("data-label", None)
        placement = ET.Element(tag("g"), {"transform": PLACEMENT})
        placement.append(source)
        if tissue not in regions:
            regions[tissue] = ET.SubElement(root, tag("g"), {
                "id": tissue, "data-tissue": tissue, "data-label": "Carpel", "fill": "#DCE2E4",
            })
        regions[tissue].append(placement)

    # The source uses filled black/white artwork, then masked color overlays.
    # Keep that drawing order; the PDF builder compiles the artwork to black paths.
    linework = ET.Element(tag("g"), {
        "id": "flower-detail-linework-v9", "transform": PLACEMENT, "pointer-events": "none",
    })
    for node in detail:
        if node.tag not in {tag("defs"), tag("title"), tag("desc")} and node not in detail_regions:
            linework.append(node)
    root.insert(list(root).index(root.find(tag("defs"))) + 1, linework)
    return root


def build() -> None:
    root = compose(ET.parse(STATIC / "sources/potato-plant.svg").getroot(),
                   ET.parse(STATIC / "sources/flower-details.svg").getroot())
    source = ET.tostring(root, encoding="utf-8") + b"\n"
    (STATIC / "potato-template.svg").write_bytes(source)
    metadata_path = STATIC / "tissues.json"
    metadata = json.loads(metadata_path.read_text())
    metadata.update(template_version="v9", template_sha256=hashlib.sha256(source).hexdigest())
    if not any(tissue["id"] == "carpel" for tissue in metadata["tissues"]):
        index = next(i for i, tissue in enumerate(metadata["tissues"]) if tissue["id"] == "anther")
        metadata["tissues"].insert(index + 1, {"id": "carpel", "label": "心皮"})
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print("Built v9 plant and flower template with 17 tissue regions.")


if __name__ == "__main__":
    build()
