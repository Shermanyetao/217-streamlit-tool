from __future__ import annotations

import argparse
import http.cookiejar
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


API_BASE_URL = "https://dispatch-api.uniuni.com"
DISPATCH_REFERER = "https://dispatch.uniuni.com/"
AUTO_JUMP_PATH = Path("/Users/uniuni/Documents/Codex/2026-05-03/new-chat-2/auto_jump.py")
XLSX_EXTENSIONS = {".xlsx", ".xlsm", ".xltx", ".xltm"}
XLS_EXTENSIONS = {".xls"}


class DispatchApi:
    def __init__(self) -> None:
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.user: dict | None = None

    def request(self, path: str, method: str = "GET", payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            API_BASE_URL + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": DISPATCH_REFERER.rstrip("/"),
                "Referer": DISPATCH_REFERER,
            },
        )
        try:
            with self.opener.open(request, timeout=180) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {path}: {text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error from {path}: {exc}") from exc

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from {path}: {text[:500]}") from exc

    def login(self, username: str, password: str) -> dict:
        result = self.request(
            f"/map/login?username={urllib.parse.quote(username)}",
            method="POST",
            payload={"password": password},
        )
        if result.get("status") != "SUCCESS":
            raise RuntimeError(f"Login failed: {result}")
        self.user = result.get("data") or {}
        return self.user

    def update_orders_to_217(
        self,
        orders: list[str],
        warehouse_id: int | str,
        batch_number: str = "",
    ) -> dict:
        return self.request(
            "/business/updateordersto217",
            method="POST",
            payload={
                "batch_number": batch_number,
                "tnos": ",".join(orders),
                "warehouse_id": warehouse_id,
            },
        )


def load_credentials() -> tuple[str, str]:
    username = os.getenv("UNIUNI_USER")
    password = os.getenv("UNIUNI_PASS")
    if username and password:
        return username, password

    try:
        import streamlit as st

        username = st.secrets.get("UNIUNI_USER")
        password = st.secrets.get("UNIUNI_PASS")
        if username and password:
            return str(username), str(password)
    except Exception:
        pass

    if not AUTO_JUMP_PATH.exists():
        raise FileNotFoundError(
            "Missing credentials. Set UNIUNI_USER and UNIUNI_PASS environment variables."
        )
    spec = importlib.util.spec_from_file_location("auto_jump_credentials", AUTO_JUMP_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot read credential config: {AUTO_JUMP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DEFAULT_USERNAME, module.DEFAULT_PASSWORD


def parse_order_numbers(args: argparse.Namespace) -> list[str]:
    values = []
    values.extend(args.orders or [])
    if args.file:
        path = Path(args.file).expanduser()
        values.extend(read_order_values_from_file(path))
    if args.stdin:
        values.extend(re.split(r"[\s,;]+", sys.stdin.read()))

    orders = []
    for value in values:
        clean = str(value).strip()
        if not clean:
            continue
        if not clean.upper().startswith("CNGF"):
            print(f"[SKIP] {clean} does not start with CNGF")
            continue
        orders.append(clean)
    return list(dict.fromkeys(orders))


def read_order_values_from_file(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix in XLSX_EXTENSIONS:
        return read_xlsx_cells(path)
    if suffix in XLS_EXTENSIONS:
        return read_xls_cells(path)

    text = read_text_auto_encoding(path)
    return re.split(r"[\s,;]+", text)


def read_text_auto_encoding(path: Path) -> str:
    for encoding in ["utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"]:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore")


def read_xlsx_cells(path: Path) -> list[str]:
    namespaces = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    }
    values = []
    try:
        with zipfile.ZipFile(path) as workbook:
            shared_strings = read_shared_strings(workbook, namespaces)
            sheet_names = sorted(
                name
                for name in workbook.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            for sheet_name in sheet_names:
                root = ET.fromstring(workbook.read(sheet_name))
                for cell in root.findall(".//main:c", namespaces):
                    value = cell_value(cell, shared_strings, namespaces)
                    if value:
                        values.extend(re.split(r"[\s,;]+", value))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{path} is not a valid .xlsx file") from exc
    return values


def read_shared_strings(workbook: zipfile.ZipFile, namespaces: dict[str, str]) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("main:si", namespaces):
        parts = [node.text or "" for node in item.findall(".//main:t", namespaces)]
        strings.append("".join(parts))
    return strings


def cell_value(cell: ET.Element, shared_strings: list[str], namespaces: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", namespaces)).strip()

    value = cell.find("main:v", namespaces)
    if value is None or value.text is None:
        return ""

    text = value.text.strip()
    if cell_type == "s":
        try:
            return shared_strings[int(text)].strip()
        except (IndexError, ValueError):
            return ""
    return text


def read_xls_cells(path: Path) -> list[str]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Reading .xls requires pandas and xlrd. Save the file as .xlsx if needed."
        ) from exc

    values = []
    workbook = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    for sheet in workbook.values():
        for value in sheet.to_numpy().ravel():
            if pd.isna(value):
                continue
            values.extend(re.split(r"[\s,;]+", str(value)))
    return values


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def append_jsonl(path: str | None, record: dict) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update CNGF orders to 217 by calling the UniUni dispatch API."
    )
    parser.add_argument("orders", nargs="*", help="CNGF order numbers")
    parser.add_argument("--file", help="read order numbers from a text or Excel file")
    parser.add_argument("--stdin", action="store_true", help="read order numbers from stdin")
    parser.add_argument("--batch-size", type=int, default=300, help="API chunk size")
    parser.add_argument("--warehouse-id", help="override warehouse_id; defaults to login city_id")
    parser.add_argument("--batch-number", default="", help="optional API batch_number value")
    parser.add_argument("--log-file", help="write raw API results as JSONL")
    parser.add_argument("--summary-file", help="write a text summary")
    parser.add_argument("--dry-run", action="store_true", help="parse input but do not update")
    args = parser.parse_args()

    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")

    orders = parse_order_numbers(args)
    if not orders and not args.batch_number:
        raise ValueError("No CNGF order numbers or batch number were provided.")

    print(f"Loaded {len(orders)} order(s).")
    if args.dry_run:
        print("Dry run only. No API update was sent.")
        return

    username, password = load_credentials()
    api = DispatchApi()
    user = api.login(username, password)
    warehouse_id = args.warehouse_id or user.get("city_id")
    if not warehouse_id:
        raise ValueError("Cannot determine warehouse_id. Pass --warehouse-id explicitly.")

    if args.log_file:
        Path(args.log_file).write_text("", encoding="utf-8")

    total_updated = 0
    succeed_tnos: list[str] = []
    failed_tnos: list[str] = []
    not_found_tnos: list[str] = []

    order_chunks = chunks(orders, args.batch_size) if orders else [[]]
    for chunk_index, order_chunk in enumerate(order_chunks, start=1):
        result = api.update_orders_to_217(
            order_chunk,
            warehouse_id=warehouse_id,
            batch_number=args.batch_number,
        )
        data = result.get("data") or {}
        updated = int(data.get("updated") or 0)
        succeed = data.get("succeed_tno") or []
        failed = data.get("failed_tno") or []
        not_found = data.get("not_found") or []

        total_updated += updated
        succeed_tnos.extend(succeed)
        failed_tnos.extend(failed)
        not_found_tnos.extend(not_found)

        start = (chunk_index - 1) * args.batch_size + 1
        end = start + len(order_chunk) - 1
        append_jsonl(
            args.log_file,
            {
                "chunk": chunk_index,
                "start": start,
                "end": end,
                "response": result,
            },
        )
        print(
            "chunk "
            f"{chunk_index} ({start}-{end}): "
            f"updated={updated}, succeed={len(succeed)}, "
            f"failed={len(failed)}, not_found={len(not_found)}",
            flush=True,
        )

    summary = (
        f"orders={len(orders)}\n"
        f"warehouse_id={warehouse_id}\n"
        f"total_updated={total_updated}\n"
        f"succeed_count={len(succeed_tnos)}\n"
        f"failed_count={len(failed_tnos)}\n"
        f"not_found_count={len(not_found_tnos)}\n"
        f"failed_sample={failed_tnos[:20]}\n"
        f"not_found_sample={not_found_tnos[:20]}\n"
    )
    print(summary)
    if args.summary_file:
        Path(args.summary_file).write_text(summary, encoding="utf-8")


if __name__ == "__main__":
    main()
