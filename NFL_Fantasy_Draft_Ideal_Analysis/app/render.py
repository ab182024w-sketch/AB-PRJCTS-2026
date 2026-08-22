"""Rendering helpers: the responsive board, and the theme it lives in.

`st.dataframe` is not usable on a phone — a 12-column grid scrolls sideways and
the ranking, which is the whole product, falls off the screen. So the boards are
rendered as one HTML table whose CSS collapses each row into a card below
700px (README §8, "Mobile"). One markup path, no viewport sniffing, no
duplicated data: the same DOM is a table on a laptop and a stack of cards on a
phone, and it reflows live when the window is resized.

Column priority is expressed in the same place: `Column.detail=True` columns are
hidden until the "more detail" toggle is on, so rank, name and points are what
survives on a narrow screen.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

import pandas as pd

POSITION_COLORS = {
    "QB": "#ef476f",
    "RB": "#06d6a0",
    "WR": "#4cc9f0",
    "TE": "#ffd166",
    "K": "#b892ff",
    "DEF": "#f8961e",
}

CSS = """
<style>
/* Grey at low alpha reads against a dark and a light background alike, and
   muted text is dimmed with opacity rather than a fixed colour, so the board
   follows whichever appearance Streamlit is set to. */
:root {
  --board-bg: rgba(128,128,128,0.10);
  --board-line: rgba(128,128,128,0.35);
  --board-muted-alpha: 0.65;
}
.board { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.board th {
  text-align: right; font-size: 0.72rem; letter-spacing: 0.04em; text-transform: uppercase;
  color: inherit; opacity: var(--board-muted-alpha); font-weight: 600; padding: 0.45rem 0.5rem;
  border-bottom: 1px solid var(--board-line); white-space: nowrap;
}
.board th.left, .board td.left { text-align: left; }
.board td {
  text-align: right; padding: 0.45rem 0.5rem; border-bottom: 1px solid var(--board-line);
  white-space: nowrap;
}
.board tr:hover td { background: var(--board-bg); }
.board .rank { opacity: var(--board-muted-alpha); width: 2.5rem; }
.board .name { font-weight: 600; }
.board .lead { font-weight: 700; }
.pill {
  display: inline-block; padding: 0.05rem 0.4rem; border-radius: 999px;
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.03em; color: #10131a;
}
.team { opacity: var(--board-muted-alpha); font-size: 0.78rem; margin-left: 0.35rem; }
.slot-head {
  display: flex; align-items: baseline; gap: 0.5rem; margin: 1.4rem 0 0.2rem;
}
.slot-head h3 { margin: 0; font-size: 1.05rem; }
.slot-head span { opacity: var(--board-muted-alpha); font-size: 0.8rem; }

/* Phone: every row becomes a card. Headers are dropped and each cell carries
   its own label, so nothing scrolls sideways. */
@media (max-width: 700px) {
  .board thead { display: none; }
  .board, .board tbody, .board tr, .board td { display: block; width: 100%; }
  .board tr {
    border: 1px solid var(--board-line); border-radius: 0.6rem;
    padding: 0.6rem 0.75rem; margin-bottom: 0.55rem; background: var(--board-bg);
  }
  .board td {
    border: none; padding: 0.12rem 0; text-align: right;
    display: flex; justify-content: space-between; align-items: baseline;
  }
  .board td::before {
    content: attr(data-label); opacity: var(--board-muted-alpha);
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .board td.headline {
    font-size: 1.02rem; border-bottom: 1px solid var(--board-line);
    padding-bottom: 0.35rem; margin-bottom: 0.35rem;
  }
  .board td.headline::before { content: none; }
}
</style>
"""


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    fmt: str = "{:.1f}"
    detail: bool = False


def _format(value: object, fmt: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    if isinstance(value, (int, float)):
        return fmt.format(value)
    return html.escape(str(value))


def position_pill(pos: str) -> str:
    color = POSITION_COLORS.get(pos, "#8d99ae")
    return f'<span class="pill" style="background:{color}">{html.escape(pos)}</span>'


def board_table(
    frame: pd.DataFrame,
    columns: list[Column],
    *,
    show_detail: bool,
    pos_column: str | None = "pos",
    rank_column: str | None = None,
) -> str:
    """A ranking as one table that reads as cards on a phone."""
    visible = [column for column in columns if show_detail or not column.detail]
    head = "".join(
        f'<th class="{"left" if index == 0 else ""}">{html.escape(column.label)}</th>'
        for index, column in enumerate(visible)
    )
    if rank_column:
        head = "<th></th>" + head

    rows: list[str] = []
    for offset, (_, record) in enumerate(frame.iterrows(), start=1):
        cells: list[str] = []
        if rank_column:
            rank = record.get(rank_column, offset)
            cells.append(f'<td class="rank left" data-label="Rank">{int(rank)}</td>')
        for index, column in enumerate(visible):
            value = _format(record.get(column.key), column.fmt)
            if index == 0:
                pill = ""
                if pos_column and pos_column in record.index:
                    pill = position_pill(str(record[pos_column])) + " "
                team = ""
                if "team" in record.index and column.key != "team":
                    team = f'<span class="team">{html.escape(str(record["team"]))}</span>'
                cells.append(
                    f'<td class="left name headline" data-label="{html.escape(column.label)}">'
                    f"{pill}{value}{team}</td>"
                )
            else:
                lead = " lead" if index == 1 else ""
                cells.append(
                    f'<td class="{lead.strip()}" data-label="{html.escape(column.label)}">{value}</td>'
                )
        rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        f'<table class="board"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def slot_heading(slot: str, subtitle: str) -> str:
    return (
        f'<div class="slot-head">{position_pill(slot)}'
        f"<h3>{html.escape(slot)}</h3><span>{html.escape(subtitle)}</span></div>"
    )
