"""Thin wrapper around the Google Sheets API. Every pipeline stage reads and
writes rows through here rather than calling the API directly, so the sheet's
layout only has to be understood in one place (see config.COLUMNS).

Each row is represented as a plain dict, e.g.:
    {"id": "42", "status": "drafted", "pillar": "desk setups", ...}
"""
from googleapiclient.discovery import build
from google.oauth2 import service_account

import config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_creds = service_account.Credentials.from_service_account_file(
    config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
_service = build("sheets", "v4", credentials=_creds)


def get_all_rows() -> list[dict]:
    """Returns every row in the queue as a list of dicts, in sheet order.
    The header row (row 1) is assumed to match config.COLUMNS exactly.
    """
    range_name = f"{config.SHEET_TAB_NAME}!A2:Z"
    result = (
        _service.spreadsheets()
        .values()
        .get(spreadsheetId=config.SHEET_ID, range=range_name)
        .execute()
    )
    values = result.get("values", [])
    rows = []
    for i, raw_row in enumerate(values):
        padded = raw_row + [""] * (len(config.COLUMNS) - len(raw_row))
        row = dict(zip(config.COLUMNS, padded))
        row["_sheet_row_number"] = i + 2  # +2: header row + 1-indexing
        rows.append(row)
    return rows


def update_row(sheet_row_number: int, updates: dict) -> None:
    """Writes only the given columns for one row, leaving everything else
    untouched. `updates` keys must be column names from config.COLUMNS.
    """
    data = []
    for col_name, value in updates.items():
        col_index = config.COLUMNS.index(col_name)  # 0-based
        col_letter = _column_letter(col_index)
        cell_range = f"{config.SHEET_TAB_NAME}!{col_letter}{sheet_row_number}"
        data.append({"range": cell_range, "values": [[value]]})

    _service.spreadsheets().values().batchUpdate(
        spreadsheetId=config.SHEET_ID,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()


def append_rows(rows: list[dict]) -> None:
    """Appends new rows (e.g. freshly generated ideas) to the bottom of the
    queue. Each dict's keys should be column names from config.COLUMNS;
    any column not present in a given dict is left blank for that row.
    """
    values = [[row.get(col, "") for col in config.COLUMNS] for row in rows]
    _service.spreadsheets().values().append(
        spreadsheetId=config.SHEET_ID,
        range=f"{config.SHEET_TAB_NAME}!A:A",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()


def _column_letter(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', etc."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
