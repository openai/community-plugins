"""Build or verify an assembly recipe's offline BOM workbook, CSV and board guide."""

import argparse
import csv
import json
import re
import textwrap
from copy import deepcopy
from decimal import Decimal
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
FIELDS = ("ID", "Scope", "Group", "Part", "Qty", "Unit", "Supply", "Specification", "Source")
SHEET_FIELDS = tuple(field for field in FIELDS if field != "Scope")
MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
ET.register_namespace("x", MAIN[1:-1])
ET.register_namespace("r", DOC_REL[1:-1])


def read_config(recipe):
    config = json.loads((recipe / "assembly.json").read_text(encoding="utf-8"))["bom"]
    scopes, title = config["scopes"], config["title"]
    if not isinstance(title, str) or not title.strip():
        raise ValueError("The recipe needs a BOM title")
    if not isinstance(scopes, list) or len(scopes) != 2:
        raise ValueError("The recipe must name both BOM sheets")
    for scope in scopes:
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 31 or re.search(r"[\\/:*?\[\]]", scope):
            raise ValueError(f"Invalid Excel sheet name: {scope!r}")
        if scope.startswith("'") or scope.endswith("'"):
            raise ValueError(f"Excel sheet names cannot start or end with an apostrophe: {scope!r}")
    if scopes[0].casefold() == scopes[1].casefold():
        raise ValueError("BOM sheet names must be distinct")
    return title, scopes


def valid_source(target):
    if not target:
        return True
    url = urlsplit(target)
    if url.scheme or url.netloc:
        return url.scheme == "https" and bool(url.hostname)
    path = PurePosixPath(url.path)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts and "\\" not in target and ":" not in target


def read_parts(path, scopes):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("Missing or reordered BOM columns")
        rows = list(reader)
    seen = set()
    for row in rows:
        key = row["ID"]
        if not key or key != key.strip() or key in seen:
            raise ValueError(f"Invalid or repeated BOM ID: {key}")
        seen.add(key)
        required = ("Group", "Part", "Unit", "Supply", "Specification")
        if None in row or None in row.values() or row["Scope"] not in scopes or not all(row[f] for f in required):
            raise ValueError(f"Incomplete catalog entry: {key}")
        qty = row["Qty"]
        if qty:
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", qty) or Decimal(qty) <= 0:
                raise ValueError(f"Invalid quantity for {key}")
        elif row["Unit"] != "as needed":
            raise ValueError(f"Missing quantity for {key}")
        if not valid_source(row["Source"]):
            raise ValueError(f"Expected an HTTPS source or a relative artifact path for {key}")
    if not rows:
        raise ValueError("The parts catalog is empty")
    return rows


def xml(element):
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def source_label(target):
    if not target:
        return ""
    if target.startswith("https://"):
        return "Source link"
    return "Design file"


def make_sheet(template, rows, title, scope):
    root = ET.fromstring(template)
    data = root.find(MAIN + "sheetData")
    data[0][0].find(MAIN + "v").text = f"{title}  /  {scope}"
    data[1][0].find(MAIN + "v").text = f"{len(rows)} line items for one assembly. Supply identifies items to make, purchase or obtain with another part."
    odd, even = list(data)[3:]
    data.remove(odd)
    data.remove(even)
    links = ET.Element(MAIN + "hyperlinks")
    relationships = ET.Element(PKG_REL + "Relationships")
    for index, part in enumerate(rows):
        number = index + 4
        row = deepcopy((odd, even)[index % 2])
        row.set("r", str(number))
        wrapped = {"ID": 9, "Group": 15, "Part": 27, "Unit": 11, "Supply": 20, "Specification": 54}
        lines = max(len(textwrap.wrap(part[field], width)) for field, width in wrapped.items())
        row.set("ht", str(max(32, 15 * lines + 8)))
        for field, cell in zip(SHEET_FIELDS, row, strict=True):
            letter = cell.get("r")[0]
            cell.set("r", f"{letter}{number}")
            text = part[field]
            if field == "Source":
                text = source_label(text)
            cell.find(MAIN + "v").text = text
        if part["Source"]:
            rid = f"rId{number}"
            ET.SubElement(links, MAIN + "hyperlink", {"ref": f"H{number}", DOC_REL + "id": rid})
            ET.SubElement(relationships, PKG_REL + "Relationship", {
                "Id": rid, "Type": DOC_REL[1:-1] + "/hyperlink",
                "Target": part["Source"], "TargetMode": "External",
            })
        data.append(row)
    root.insert(list(root).index(data) + 1, ET.Element(MAIN + "autoFilter", {"ref": f"A3:H{3+len(rows)}"}))
    if len(links):
        root.insert(list(root).index(root.find(MAIN + "pageMargins")), links)
    # Keep the sourcing notes on the same landscape printed page as their part.
    sheet_pr = ET.Element(MAIN + "sheetPr")
    ET.SubElement(sheet_pr, MAIN + "pageSetUpPr", {"fitToPage": "1"})
    root.insert(0, sheet_pr)
    ET.SubElement(root, MAIN + "pageSetup", {"orientation": "landscape", "paperSize": "1", "fitToWidth": "1", "fitToHeight": "0"})
    return xml(root), xml(relationships)


def write_workbook(path, parts, title, scopes):
    # Layout is an editable two-sheet workbook. Rows 4 and 5 carry the alternating
    # styles. Keeping the template local lets installed demos run fully offline.
    with ZipFile(HERE / "layout.xlsx") as source:
        members = {name: source.read(name) for name in source.namelist()}
    workbook = ET.fromstring(members["xl/workbook.xml"])
    for sheet, scope in zip(workbook.findall(f".//{MAIN}sheet"), scopes, strict=True):
        sheet.set("name", scope)
    members["xl/workbook.xml"] = xml(workbook)
    for index, scope in enumerate(scopes, 1):
        member = f"xl/worksheets/sheet{index}.xml"
        relations = f"xl/worksheets/_rels/sheet{index}.xml.rels"
        rows = [part for part in parts if part["Scope"] == scope]
        members[member], members[relations] = make_sheet(members[member], rows, title, scope)
    with ZipFile(path, "w") as output:
        for name, data in sorted(members.items()):
            # ZipInfo's fixed DOS epoch keeps clean builds byte-for-byte stable.
            output.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)


def check(output, parts, title, scopes, guide):
    if read_parts(output / "bom.csv", scopes) != parts:
        raise ValueError("Delivered CSV differs from the source catalog")
    if (output / "boards.md").read_bytes() != guide:
        raise ValueError("Board guide differs from the recipe")
    with ZipFile(output / "bom.xlsx") as book:
        names = ET.fromstring(book.read("xl/workbook.xml")).findall(f".//{MAIN}sheet")
        if [sheet.get("name") for sheet in names] != scopes:
            raise ValueError("Unexpected BOM sheet names")
        for index, scope in enumerate(scopes, 1):
            sheet = ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))
            rels = ET.fromstring(book.read(f"xl/worksheets/_rels/sheet{index}.xml.rels"))
            targets = {rel.get("Id"): rel.get("Target") for rel in rels}
            links = {link.get("ref"): targets[link.get(DOC_REL + "id")] for link in sheet.findall(f".//{MAIN}hyperlink")}
            rows = sheet.findall(f"{MAIN}sheetData/{MAIN}row")
            parts_here = [part for part in parts if part["Scope"] == scope]
            if rows[0][0].findtext(MAIN + "v") != f"{title}  /  {scope}":
                raise ValueError(f"Unexpected workbook title in {scope}")
            if len(rows) != len(parts_here) + 3 or sheet.find(MAIN + "autoFilter").get("ref") != f"A3:H{len(rows)}":
                raise ValueError(f"Incomplete filtered range in {scope}")
            if sheet.find(f".//{MAIN}pane").get("topLeftCell") != "A4":
                raise ValueError(f"Missing frozen header in {scope}")
            headers = [c.findtext(MAIN + "v") for c in rows[2]]
            if headers != list(SHEET_FIELDS):
                raise ValueError(f"Unexpected columns in {scope}")
            for number, (row, part) in enumerate(zip(rows[3:], parts_here, strict=True), 4):
                values = [cell.findtext(MAIN + "v") for cell in row]
                expected = [part[f] for f in SHEET_FIELDS[:-1]] + [source_label(part["Source"])]
                if values != expected or links.get(f"H{number}", "") != part["Source"]:
                    raise ValueError(f"Workbook content differs for {part['ID']}")
                if part["Qty"] and row[3].get("t") != "n":
                    raise ValueError(f"Quantity must be numeric for {part['ID']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--check", action="store_true", help="Verify previously generated files")
    args = parser.parse_args()
    title, scopes = read_config(args.recipe)
    source = args.recipe / "catalog" / "parts.csv"
    parts = read_parts(source, scopes)
    guide = (args.recipe / "boards.md").read_bytes()
    if not args.check:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "bom.csv").write_bytes(source.read_bytes())
        (args.output / "boards.md").write_bytes(guide)
        write_workbook(args.output / "bom.xlsx", parts, title, scopes)
    check(args.output, parts, title, scopes, guide)
    print(f"Verified {len(parts)} catalog lines across {' and '.join(scopes)}.")


if __name__ == "__main__":
    main()
