"""Exercise the installed BOM generator without a spreadsheet runtime."""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


PACKAGE = Path(__file__).resolve().parents[3] / "plugins/physical-engineering"
CATALOG = PACKAGE / "scripts/assembly_catalog/build.py"
RECIPE = PACKAGE / "fixtures/whoop-65"


class WhoopBillOfMaterials(unittest.TestCase):
    def test_snap_parts_match_the_aircraft_and_rebuild_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "Rehearsal A"
            second = Path(folder) / "Rehearsal B"
            for destination in (first, second):
                subprocess.run([sys.executable, "-IS", str(CATALOG), str(destination), str(RECIPE)], check=True, capture_output=True)
            for name in ("bom.csv", "bom.xlsx", "boards.md"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            with (first / "bom.csv").open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            fitted = {row["ID"]: int(row["Qty"]) for row in rows if row["Scope"] == "Aircraft"}
            self.assertEqual(fitted, {
                "FR01": 1, "DK01": 1, "CY01": 1, "FC01": 1, "CA01": 1,
                "GM01": 4, "DS01": 4, "CT01": 1, "MO01": 4, "MS01": 12,
                "PR01": 2, "PR02": 2, "BA01": 1, "RB01": 2, "RP01": 2,
                "WH01": 4, "WH02": 1, "WH03": 1, "WH04": 1, "WH05": 1,
            })
            for scope in ("Aircraft", "Bench"):
                self.assertTrue(any(r["Scope"] == scope for r in rows))

            # A stale workbook can have the correct row count and plausible IDs.
            path = first / "bom.xlsx"
            with ZipFile(path) as source:
                members = {name: source.read(name) for name in source.namelist()}
            name = "xl/worksheets/sheet1.xml"
            sheet = ET.fromstring(members[name])
            quantity = sheet.find(".//{*}c[@r='D4']/{*}v")
            self.assertEqual(quantity.text, "1")
            quantity.text = "2"
            members[name] = ET.tostring(sheet)
            with ZipFile(path, "w") as altered:
                for name, contents in members.items():
                    altered.writestr(name, contents)
            result = subprocess.run([sys.executable, str(CATALOG), str(first), str(RECIPE), "--check"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Workbook content differs for FR01", result.stderr)

    def test_unrelated_assembly_keeps_its_title_scopes_ids_and_material_units(self):
        with tempfile.TemporaryDirectory() as folder:
            recipe = Path(folder) / "panel"
            output = Path(folder) / "delivery"
            (recipe / "catalog").mkdir(parents=True)
            config = {"bom": {"title": "Control panel", "scopes": ["Installed", "Bench"]}}
            (recipe / "assembly.json").write_text(json.dumps(config), encoding="utf-8")
            (recipe / "boards.md").write_text("# Lighting controller\n", encoding="utf-8")
            (recipe / "catalog/parts.csv").write_text(
                "ID,Scope,Group,Part,Qty,Unit,Supply,Specification,Source\n"
                "DIN-RAIL,Installed,Mounting,Rail,0.4,m,Cut from stock,35 mm section,drawings/rail.pdf\n"
                "Saw,Bench,Tooling,Band saw,1,ea,Workshop,Metal-cutting blade,\n", encoding="utf-8")
            subprocess.run([sys.executable, "-IS", str(CATALOG), str(output), str(recipe)], check=True, capture_output=True)
            self.assertEqual((output / "boards.md").read_bytes(), (recipe / "boards.md").read_bytes())
            with ZipFile(output / "bom.xlsx") as book:
                workbook = ET.fromstring(book.read("xl/workbook.xml"))
                self.assertEqual([sheet.get("name") for sheet in workbook.findall(".//{*}sheet")], ["Installed", "Bench"])
                sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
                self.assertEqual(sheet.findtext(".//{*}c[@r='A1']/{*}v"), "Control panel  /  Installed")
                self.assertEqual(sheet.findtext(".//{*}c[@r='D4']/{*}v"), "0.4")
                targets = ET.fromstring(book.read("xl/worksheets/_rels/sheet1.xml.rels"))
                self.assertEqual(targets[0].get("Target"), "drawings/rail.pdf")
