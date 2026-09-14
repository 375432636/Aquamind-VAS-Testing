"""Fill the bundled Excel template without adding a runtime spreadsheet dependency."""

import copy
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from .evaluation import evaluation_rows

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("", NS)


def _tag(name):
    return f"{{{NS}}}{name}"


def _value(cell, value):
    for child in list(cell):
        cell.remove(child)
    if type(value) in {int, float}:
        cell.set("t", "n")
        ET.SubElement(cell, _tag("v")).text = str(value)
    else:
        # Inline strings keep user text literal, including leading =, + and @.
        cell.set("t", "inlineStr")
        text = ET.SubElement(ET.SubElement(cell, _tag("is")), _tag("t"))
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))


def _fill(xml, rows, start, *, context=None):
    sheet = ET.fromstring(xml)
    data = sheet.find(_tag("sheetData"))
    prototype = next(row for row in data if int(row.get("r")) == start)
    for row in list(data):
        if int(row.get("r")) >= start:
            data.remove(row)
    for index, values in enumerate(rows, start):
        row = copy.deepcopy(prototype)
        row.set("r", str(index))
        for column, (cell, value) in enumerate(zip(row, values)):
            cell.set("r", f"{chr(65 + column)}{index}")
            _value(cell, value)
        # The question column may contain long prompts; preserve readable wrapping.
        if start == 7:
            row.set("ht", str(max(48, ((len(str(values[4])) + 27) // 28) * 17)))
        data.append(row)
    if context:
        _value(data.find(f"{_tag('row')}[@r='3']/{_tag('c')}[@r='A3']"), context)
    end = start + len(rows) - 1
    width = len(prototype)
    # autoFilter is after sheetData and before pageMargins in SpreadsheetML.
    sheet.insert(
        list(sheet).index(data) + 1,
        ET.Element(
            _tag("autoFilter"), ref=f"A{start-1}:{chr(64+width)}{max(start-1,end)}"
        ),
    )
    return ET.tostring(sheet, encoding="utf-8", xml_declaration=True)


def export_excel(reports, path):
    reports = [report for report in reports if report.get("evaluation")]
    if not reports:
        return False
    rows = [row for report in reports for row in evaluation_rows(report)]
    sources = {
        "MOCK 模拟数据" if report.get("data_source") == "mock" else "真实采集"
        for report in reports
    }
    context = f"{' / '.join(sorted(sources))}；{len(reports)} 个 session，{len(rows)} 个问题。"
    if any(report.get("data_source") == "mock" for report in reports):
        context += "模拟耗时不代表服务性能。"
    sessions = [
        [
            report["evaluation"]["persona"],
            report.get("run_metadata", {}).get("environment", ""),
            report.get("run_metadata", {}).get("device_id", ""),
            "MOCK 模拟数据" if report.get("data_source") == "mock" else "真实采集",
            report.get("status", "unknown"),
        ]
        for report in reports
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).with_name("evaluation_template.xlsx")
    with ZipFile(template) as source, ZipFile(path, "w", ZIP_DEFLATED) as output:
        for name in source.namelist():
            content = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                content = _fill(content, rows, 7, context=context)
            elif name == "xl/worksheets/sheet2.xml":
                content = _fill(content, sessions, 2)
            output.writestr(name, content)
    return True
