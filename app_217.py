from __future__ import annotations

import importlib.util
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
API_SCRIPT_PATH = BASE_DIR / "dispatch_217_api.py"


@st.cache_resource
def load_api_module():
    spec = importlib.util.spec_from_file_location("api_217", API_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {API_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_text_orders(text: str) -> list[str]:
    return re.split(r"[\s,;]+", text or "")


def normalize_orders(values: list[str]) -> tuple[list[str], list[str]]:
    orders = []
    skipped = []
    for value in values:
        clean = str(value).strip()
        if not clean:
            continue
        if not clean.upper().startswith("CNGF"):
            skipped.append(clean)
            continue
        orders.append(clean)
    return list(dict.fromkeys(orders)), skipped


def parse_uploaded_file(uploaded_file, api_module) -> list[str]:
    if uploaded_file is None:
        return []
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(uploaded_file.getbuffer())
        temp_path = Path(handle.name)
    try:
        return api_module.read_order_values_from_file(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def extract_search_rows(response: dict) -> list[dict]:
    data = response.get("data") or {}
    if isinstance(data, list):
        return data
    rows = data.get("data")
    if isinstance(rows, list):
        return rows
    return []


def get_tno(row: dict) -> str:
    for key in ["tno", "trackingNo", "tracking_no"]:
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def get_status(row: dict) -> str:
    for key in ["latest_status", "state", "status"]:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def precheck_204_orders(api, api_module, orders: list[str], batch_size: int) -> tuple[list[str], list[dict], list[str], list[dict]]:
    rows_by_tno = {}
    query_responses = []
    for chunk in api_module.chunks(orders, batch_size):
        response = api.multiple_search_by_tno(chunk, page_size=max(len(chunk), 20))
        query_responses.append(response)
        for row in extract_search_rows(response):
            tno = get_tno(row)
            if tno:
                rows_by_tno[tno.upper()] = row

    eligible = []
    skipped = []
    not_found = []
    for order in orders:
        row = rows_by_tno.get(order.upper())
        if not row:
            not_found.append(order)
            continue
        status = get_status(row)
        if status == "204":
            eligible.append(order)
        else:
            skipped.append({
                "tno": order,
                "status": status or "UNKNOWN",
                "reason": "not_204",
            })
    return eligible, skipped, not_found, query_responses


def run_update(api_module, orders: list[str], warehouse_id: str, batch_size: int, batch_number: str) -> dict:
    username, password = api_module.load_credentials()
    api = api_module.DispatchApi()
    user = api.login(username, password)
    final_warehouse_id = warehouse_id.strip() or str(user.get("city_id") or "")
    if not final_warehouse_id:
        raise ValueError("Cannot determine warehouse_id. Please enter it manually.")

    chunk_results = []
    total_updated = 0
    succeed_tnos = []
    failed_tnos = []
    not_found_tnos = []

    eligible_orders, skipped_status, precheck_not_found, precheck_responses = precheck_204_orders(
        api,
        api_module,
        orders,
        batch_size,
    )
    not_found_tnos.extend(precheck_not_found)

    for chunk_index, chunk in enumerate(api_module.chunks(eligible_orders, batch_size), start=1):
        response = api.update_orders_to_217(
            chunk,
            warehouse_id=final_warehouse_id,
            batch_number=batch_number.strip(),
        )
        data = response.get("data") or {}
        updated = int(data.get("updated") or 0)
        succeed = data.get("succeed_tno") or []
        failed = data.get("failed_tno") or []
        not_found = data.get("not_found") or []
        total_updated += updated
        succeed_tnos.extend(succeed)
        failed_tnos.extend(failed)
        not_found_tnos.extend(not_found)
        chunk_results.append({
            "chunk": chunk_index,
            "orders": len(chunk),
            "updated": updated,
            "succeed": len(succeed),
            "failed": len(failed),
            "not_found": len(not_found),
            "response": response,
        })

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "warehouse_id": final_warehouse_id,
        "orders_count": len(orders),
        "eligible_204_count": len(eligible_orders),
        "skipped_status": skipped_status,
        "precheck_responses": precheck_responses,
        "total_updated": total_updated,
        "succeed_tnos": succeed_tnos,
        "failed_tnos": failed_tnos,
        "not_found_tnos": not_found_tnos,
        "chunk_results": chunk_results,
    }


def render_result(result: dict) -> None:
    st.subheader("结果")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("输入单数", result["orders_count"])
    c2.metric("204 可转", result.get("eligible_204_count", result["orders_count"]))
    c3.metric("已更新", result["total_updated"])
    c4.metric("跳过非 204", len(result.get("skipped_status", [])))
    c5.metric("找不到", len(result["not_found_tnos"]))

    st.write("批次结果")
    st.dataframe([
        {k: item[k] for k in ["chunk", "orders", "updated", "succeed", "failed", "not_found"]}
        for item in result["chunk_results"]
    ], use_container_width=True)

    if result["failed_tnos"]:
        st.error("Failed")
        st.code("\n".join(result["failed_tnos"]))
    if result["not_found_tnos"]:
        st.warning("Not Found")
        st.code("\n".join(result["not_found_tnos"]))
    if result.get("skipped_status"):
        st.warning("Skipped: status is not 204")
        st.dataframe(result["skipped_status"], use_container_width=True)
    if result["succeed_tnos"]:
        st.success("Succeed")
        st.code("\n".join(result["succeed_tnos"][:200]))

    summary = (
        f"timestamp={result['timestamp']}\n"
        f"warehouse_id={result['warehouse_id']}\n"
        f"orders={result['orders_count']}\n"
        f"eligible_204_count={result.get('eligible_204_count', result['orders_count'])}\n"
        f"total_updated={result['total_updated']}\n"
        f"succeed_count={len(result['succeed_tnos'])}\n"
        f"failed_count={len(result['failed_tnos'])}\n"
        f"not_found_count={len(result['not_found_tnos'])}\n"
        f"skipped_not_204_count={len(result.get('skipped_status', []))}\n"
        f"failed_sample={result['failed_tnos'][:20]}\n"
        f"not_found_sample={result['not_found_tnos'][:20]}\n"
        f"skipped_not_204_sample={result.get('skipped_status', [])[:20]}\n"
    )
    st.download_button("下载 summary.txt", summary, file_name="217_summary.txt")
    st.download_button(
        "下载 raw_log.json",
        json.dumps(result, ensure_ascii=False, indent=2),
        file_name="217_raw_log.json",
        mime="application/json",
    )


def main() -> None:
    st.set_page_config(page_title="217 转换工具", layout="wide")
    st.title("217 转换工具")
    st.caption("上传或粘贴 CNGF 单号，调用 Dispatch API 批量转 217。")

    if not API_SCRIPT_PATH.exists():
        st.error(f"找不到 API 脚本: {API_SCRIPT_PATH}")
        st.stop()
    api_module = load_api_module()

    left, right = st.columns([2, 1])
    with left:
        uploaded = st.file_uploader("上传 txt / xlsx / xls 文件", type=["txt", "csv", "xlsx", "xlsm", "xls"])
        pasted = st.text_area("或者直接粘贴 CNGF 单号", height=180, placeholder="CNGF001...\nCNGF002...")
    with right:
        warehouse_id = st.text_input("Warehouse ID", value="31", help="ATL 通常是 31；留空则使用账号默认 city_id。")
        batch_size = st.number_input("Batch size", min_value=1, max_value=1000, value=300, step=50)
        batch_number = st.text_input("Batch Number 可选", value="")

    raw_values = []
    try:
        raw_values.extend(parse_uploaded_file(uploaded, api_module))
    except Exception as exc:
        st.error(f"读取上传文件失败: {exc}")
    raw_values.extend(parse_text_orders(pasted))

    orders, skipped = normalize_orders(raw_values)
    st.subheader("预览")
    c1, c2 = st.columns(2)
    c1.metric("识别到 CNGF 单号", len(orders))
    c2.metric("跳过非 CNGF 内容", len(skipped))

    if orders:
        st.code("\n".join(orders[:300]), language="text")
    if skipped:
        with st.expander("查看被跳过的内容"):
            st.code("\n".join(skipped[:300]), language="text")

    if st.button("开始转 217", type="primary", disabled=not orders):
        with st.spinner("正在调用 Dispatch API..."):
            try:
                result = run_update(api_module, orders, warehouse_id, int(batch_size), batch_number)
                st.session_state["last_result"] = result
            except Exception as exc:
                st.error(f"执行失败: {exc}")

    if "last_result" in st.session_state:
        render_result(st.session_state["last_result"])


if __name__ == "__main__":
    main()
